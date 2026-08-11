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


MESES_LABEL = {
    "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril", "05": "Mayo", "06": "Junio",
    "07": "Julio", "08": "Agosto", "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre",
}


def descargar_del_sii(rut_full, clave, periodos_lista):
    """Loguea en el SII y baja el detalle de compras de cada período manejando la página
    igual que un humano: seleccionar mes/año → Consultar → Descargar Detalles.
    Devuelve {periodo: csv_texto}."""
    import re as _re
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    resultados = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        page.set_default_timeout(45000)
        page.goto(BASE_URL, wait_until="domcontentloaded")

        # --- Login con Clave Tributaria (campos del formulario oficial del SII) ---
        try:
            page.fill("#rutcntr", rut_full)
            page.fill("#clave", clave)
            page.click("#bt_ingresar")
        except Exception:
            page.fill("input[name='rut'], input[name='rutcntr']", rut_full)
            page.fill("input[type='password']", clave)
            page.get_by_role("button", name=_re.compile("ingresar", _re.I)).first.click()

        page.wait_for_load_state("networkidle")
        if "consdcvinternetui" not in page.url:
            page.goto(BASE_URL, wait_until="networkidle")

        # Autenticación OK = aparece el formulario de consulta (botón "Consultar")
        consultar = page.get_by_role("button", name=_re.compile(r"^\s*consultar\s*$", _re.I))
        try:
            consultar.first.wait_for(timeout=25000)
        except PWTimeout:
            page.screenshot(path="login_error.png", full_page=True)
            raise SystemExit(
                "No se pudo autenticar en el SII (no apareció el formulario de consulta). "
                "Revisa SII_RUT / SII_CLAVE o un posible captcha. Se guardó 'login_error.png'."
            )

        selects = page.locator("select")
        for pt in periodos_lista:
            mes, anio = pt[4:6], pt[:4]
            # Comboboxes: 0 = empresa/RUT, 1 = mes, 2 = año
            try:
                selects.nth(1).select_option(value=mes)
            except Exception:
                selects.nth(1).select_option(label=MESES_LABEL[mes])
            try:
                selects.nth(2).select_option(value=anio)
            except Exception:
                selects.nth(2).select_option(label=anio)

            # Consultar el período (dispara getResumen) y esperar a que cargue
            try:
                with page.expect_response(lambda r: "getResumen" in r.url, timeout=30000):
                    page.get_by_role("button", name=_re.compile(r"^\s*consultar\s*$", _re.I)).first.click()
            except PWTimeout:
                page.get_by_role("button", name=_re.compile(r"^\s*consultar\s*$", _re.I)).first.click()
            page.wait_for_timeout(2500)

            # Captura la respuesta JSON del export al presionar "Descargar Detalles"
            try:
                with page.expect_response(lambda r: "getDetalleCompraExport" in r.url, timeout=30000) as ri:
                    page.get_by_role("button", name=_re.compile("descargar detalles", _re.I)).first.click()
                j = ri.value.json()
                rows = j.get("data", []) if isinstance(j, dict) else []
            except PWTimeout:
                print(f"  · {pt}: no se pudo capturar la descarga (¿sin datos?)", file=sys.stderr)
                continue

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


# ===== OneDrive (imágenes de facturas vía Microsoft Graph) =====
MS_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"  # cliente público de Microsoft Graph CLI
FACT_PATH = os.environ.get("ONEDRIVE_FACT_PATH", "Anasep 2/Facturas")

def _ms_token():
    rt = os.environ.get("MS_REFRESH_TOKEN", "").strip()
    if not rt:
        return None
    import urllib.request, urllib.parse
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": MS_CLIENT_ID,
        "refresh_token": rt, "scope": "Files.Read offline_access User.Read",
    }).encode()
    req = urllib.request.Request("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", data=data)
    try:
        return json.load(urllib.request.urlopen(req, timeout=40)).get("access_token")
    except Exception as e:
        print("OneDrive: no se pudo refrescar el token:", e, file=sys.stderr)
        return None

def _folio_cands(name):
    base = name.rsplit(".", 1)[0]
    out = set()
    for m in re.findall(r'N[º°o]?\s*0*(\d{3,})', base, re.I):
        out.add(m)
    for m in re.findall(r'\b0*(\d{4,})\b', base):
        if not (2019 <= int(m) <= 2027):
            out.add(m)
    return out

def mapear_onedrive(folios_set):
    """Devuelve {folio: [webUrl,...]} buscando en la carpeta de OneDrive por el número en el nombre."""
    at = _ms_token()
    if not at:
        print("OneDrive: sin MS_REFRESH_TOKEN, se omite el mapeo de imágenes.")
        return {}
    import urllib.request
    from urllib.parse import quote
    def g(url):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + at})
        return json.load(urllib.request.urlopen(req, timeout=60))
    try:
        root = g("https://graph.microsoft.com/v1.0/me/drive/root:/" + quote(FACT_PATH))
    except Exception as e:
        print("OneDrive: no encontré la carpeta '%s': %s" % (FACT_PATH, e), file=sys.stderr)
        return {}
    idx = {}
    stack = [root["id"]]
    while stack:
        fid = stack.pop()
        url = "https://graph.microsoft.com/v1.0/me/drive/items/%s/children?$select=id,name,file,folder,webUrl&$top=200" % fid
        while url:
            try:
                d = g(url)
            except Exception as e:
                print("OneDrive: error listando:", e, file=sys.stderr); break
            for it in d.get("value", []):
                if it.get("folder"):
                    stack.append(it["id"])
                elif it.get("file") and it["name"].lower().endswith((".jpg", ".jpeg", ".pdf", ".png")):
                    for n in _folio_cands(it["name"]):
                        idx.setdefault(n, []).append(it["webUrl"])
            url = d.get("@odata.nextLink")
    mapping = {folio: idx[folio][:5] for folio in folios_set if folio in idx}
    print("OneDrive: %d facturas con imagen (de %d)." % (len(mapping), len(folios_set)))
    return mapping

def folios_de(data):
    fol = set()
    for csv in data.values():
        for line in csv.split("\n")[1:]:
            c = line.split(";")
            if len(c) > 5 and c[0].strip() and c[5].strip():
                fol.add(c[5].strip())
    return fol


def construir_html(data, rut_display, img=None):
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    sello = datetime.datetime.now(TZ).strftime("%d/%m/%Y, %H:%M")
    html = tpl.replace("/*__DATA__*/{}", json.dumps(data, ensure_ascii=False))
    html = html.replace("/*__IMG__*/{}", json.dumps(img or {}, ensure_ascii=False))
    html = html.replace("__ULTIMA_ACTUALIZACION__", sello)
    html = html.replace("__RUT__", rut_display)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"→ {OUTPUT} generado ({len(data)} períodos, {len(img or {})} imágenes, sello {sello})")


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
        construir_html(data, rut_display, mapear_onedrive(folios_de(data)))
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
    construir_html(data, rut_display, mapear_onedrive(folios_de(data)))


if __name__ == "__main__":
    main()
