#!/usr/bin/env python3
"""
Descarga el Registro de Compra (DTE) desde el SII y regenera el dashboard (index.html).

Autenticación: Clave Tributaria vía el formulario oficial del SII (Playwright headless).
Las credenciales se leen de variables de entorno y NUNCA se escriben en el código ni en el HTML:

    SII_RUT    = "77752795-9"      (RUT completo con dígito verificador)
    SII_CLAVE  = "········"        (Clave Tributaria)
    SII_MESES  = "2"               (opcional; cuántos meses hacia atrás bajar. 2 = mes actual + anterior)

Uso:
    python fetch_sii.py            # baja del SII y escribe index.html
    python fetch_sii.py --build-only  # solo reconstruye index.html desde los CSV ya bajados (sin tocar el SII)
"""
import os
import re
import sys
import json
import argparse
import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
BASE_URL = "https://www4.sii.cl/consdcvinternetui/"
EXPORT_PATH = "/consdcvinternetui/services/data/facadeService/getDetalleCompraExport"
TEMPLATE = "dashboard_template.html"
OUTPUT = "index.html"
DATA_DIR = "data"


def periodos(n):
    """Devuelve los últimos n períodos tributarios como 'YYYYMM', del más nuevo al más antiguo."""
    hoy = datetime.datetime.now(TZ).date()
    y, m, out = hoy.year, hoy.month, []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def formatear_rut(rut_full):
    num, dv = rut_full.replace(".", "").strip().split("-")
    miles = f"{int(num):,}".replace(",", ".")
    return num, dv, f"{miles}-{dv}"


def descargar_del_sii(rut_full, clave, periodos_lista):
    """Loguea en el SII y baja el detalle de compras de cada período. Devuelve {periodo: csv_texto}."""
    from playwright.sync_api import sync_playwright

    num, dv, _ = formatear_rut(rut_full)
    resultados = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=False)
        page = ctx.new_page()
        page.set_default_timeout(45000)
        page.goto(BASE_URL, wait_until="domcontentloaded")

        # --- Login con Clave Tributaria (campos del formulario oficial del SII) ---
        try:
            page.fill("#rutcntr", rut_full)
            page.fill("#clave", clave)
            page.click("#bt_ingresar")
        except Exception:
            # Layout alternativo de la página de identificación
            page.fill("input[name='rut']", rut_full)
            page.fill("input[name='clave'], input[type='password']", clave)
            page.click("button[type='submit'], #bt_ingresar")

        page.wait_for_load_state("networkidle")

        # Verifica que quedamos autenticados dentro del RCV
        if "consdcvinternetui" not in page.url:
            page.goto(BASE_URL, wait_until="networkidle")
        body_text = page.inner_text("body")
        if "Clave" in body_text and "REGISTRO DE COMPRAS" not in body_text.upper():
            page.screenshot(path="login_error.png", full_page=True)
            raise SystemExit(
                "No se pudo autenticar en el SII. Revisa SII_RUT / SII_CLAVE. "
                "Se guardó 'login_error.png' para diagnóstico (posible captcha o campos cambiados)."
            )

        # --- Descarga el detalle de compras de cada período vía la API interna del RCV ---
        for pt in periodos_lista:
            payload = json.dumps({
                "metaData": {
                    "namespace": "cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService/getDetalleCompraExport",
                    "conversationId": "AUTO",
                    "transactionId": "auto",
                    "page": None,
                },
                "data": {
                    "rutEmisor": num,
                    "dvEmisor": dv,
                    "ptributario": pt,
                    "codTipoDoc": 0,
                    "operacion": "COMPRA",
                    "estadoContab": "REGISTRO",
                    "accionRecaptcha": "RCV_DDETC",
                    "tokenRecaptcha": "t-o-k-e-n-web",
                },
            })
            res = page.evaluate(
                """async ({url, body}) => {
                    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body});
                    if (!r.ok) return {error: r.status};
                    const j = await r.json();
                    return {rows: (j && j.data) ? j.data : []};
                }""",
                {"url": EXPORT_PATH, "body": payload},
            )
            if isinstance(res, dict) and res.get("error"):
                print(f"  · {pt}: error HTTP {res['error']} (se omite)", file=sys.stderr)
                continue
            rows = res.get("rows", [])
            if len(rows) <= 1:
                print(f"  · {pt}: sin documentos")
                continue
            resultados[pt] = "\n".join(rows)
            print(f"  · {pt}: {len(rows) - 1} documentos")
        browser.close()
    return resultados


def guardar_csv(resultados):
    os.makedirs(DATA_DIR, exist_ok=True)
    for pt, csv in resultados.items():
        with open(os.path.join(DATA_DIR, f"{pt}.csv"), "w", encoding="utf-8") as f:
            f.write(csv)


def cargar_csv_guardados():
    data = {}
    if not os.path.isdir(DATA_DIR):
        return data
    for name in sorted(os.listdir(DATA_DIR)):
        m = re.fullmatch(r"(\d{6})\.csv", name)
        if not m:
            continue
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            data[m.group(1)] = f.read()
    return data


def construir_html(data, rut_display):
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    payload = json.dumps(data, ensure_ascii=False)
    sello = datetime.datetime.now(TZ).strftime("%d/%m/%Y, %H:%M")
    html = tpl.replace("/*__DATA__*/{}", payload)
    html = html.replace("__ULTIMA_ACTUALIZACION__", sello)
    html = html.replace("__RUT__", rut_display)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"→ {OUTPUT} generado ({len(data)} períodos, sello {sello})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-only", action="store_true",
                    help="Solo reconstruye index.html desde los CSV en data/ (no toca el SII)")
    args = ap.parse_args()

    rut_full = os.environ.get("SII_RUT", "").strip()
    rut_display = formatear_rut(rut_full)[2] if rut_full else "—"

    if args.build_only:
        data = cargar_csv_guardados()
        if not data:
            raise SystemExit("No hay CSV en data/. Corre sin --build-only para bajar del SII primero.")
        construir_html(data, rut_display)
        return

    clave = os.environ.get("SII_CLAVE", "")
    if not rut_full or not clave:
        raise SystemExit("Faltan variables de entorno SII_RUT y/o SII_CLAVE.")
    n = int(os.environ.get("SII_MESES", "2"))
    print(f"Descargando del SII períodos: {', '.join(periodos(n))}")
    resultados = descargar_del_sii(rut_full, clave, periodos(n))
    if not resultados:
        raise SystemExit("El SII no devolvió datos para ningún período.")
    guardar_csv(resultados)
    # Combina lo recién bajado con lo previamente guardado (para conservar meses anteriores)
    data = cargar_csv_guardados()
    construir_html(data, rut_display)


if __name__ == "__main__":
    main()
