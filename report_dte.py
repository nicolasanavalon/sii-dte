"""Reporte diario por Telegram: facturas de compra que vencen hoy y no están pagadas.

Usa la misma lógica que la franja "Hoy" del dashboard (dashboard_template.html):
  fecha de pago = fecha del documento + días de pago (de la factura o, si no, del proveedor),
  excluye Notas de Crédito y facturas con estado "Pagada", y descuenta la NC manual.
Lee los CSV de data/ (los actualiza update.yml) y la configuración guardada en Google Sheets.

Variables de entorno: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID; con --backup no envía si
otra corrida del mismo workflow ya salió bien hace poco (ver report_pulso.recently_sent).
"""
import glob
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

from report_pulso import TZ, money, recently_sent, send_telegram, with_retries

API_URL = "https://script.google.com/macros/s/AKfycby2RiC-sLbkTEiUyivIfxvWLNZLCBUcpuO_IL5aGB9mMzp1vrelUsKsNskkj5OmBt2N/exec"
DOW = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def load_cloud():
    """Config del dashboard en Google Sheets (responde JSONP)."""
    def get():
        with urllib.request.urlopen(f"{API_URL}?callback=cb&ts={int(datetime.now().timestamp())}", timeout=60) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text[text.index("(") + 1:text.rindex(")")])
    return with_retries(get)


def load_rows():
    rows = []
    for path in sorted(glob.glob("data/*.csv")):
        with open(path, encoding="utf-8") as fh:
            lines = [l for l in fh.read().split("\n") if l.strip()]
        for line in lines[1:]:
            c = line.split(";")
            if not c[0].strip():
                continue
            try:
                d, m, y = c[6].strip().split("/")
                fecha = date(int(y), int(m), int(d))
            except ValueError:
                continue
            rows.append({
                "tipo": c[1].strip(), "rut": c[3].strip(), "razon": c[4].strip(),
                "folio": c[5].strip(), "fecha": fecha, "total": float(c[14] or 0),
            })
    return rows


def dias_pago(r, prov, fac):
    for cfg in (fac.get(f"{r['rut']}|{r['folio']}") or {}, prov.get(r["rut"]) or {}):
        if cfg.get("dias") not in (None, ""):
            try:
                return int(cfg["dias"])
            except ValueError:
                return 0
    return 0


def vencen(rows, cloud, dia):
    prov, fac = cloud.get("prov") or {}, cloud.get("fac") or {}
    try:
        nc = json.loads((prov.get("__NC__") or {}).get("credito") or "{}")
    except ValueError:
        nc = {}
    por_prov = defaultdict(lambda: {"monto": 0, "facturas": 0})
    for r in rows:
        fid = f"{r['rut']}|{r['folio']}"
        if r["tipo"] == "61" or (fac.get(fid) or {}).get("estado") == "Pagada":
            continue
        if r["fecha"] + timedelta(days=dias_pago(r, prov, fac)) != dia:
            continue
        descuento = int("".join(ch for ch in str(nc.get(fid, "")) if ch.isdigit()) or 0)
        p = por_prov[r["razon"] or r["rut"]]
        p["monto"] += r["total"] - descuento
        p["facturas"] += 1
    return sorted(por_prov.items(), key=lambda kv: -kv[1]["monto"])


def build_report(items, dia):
    lines = [f"💸 Pagos que vencen hoy · {DOW[dia.weekday()]} {dia:%d-%m}", ""]
    if not items:
        lines.append("No hay facturas por pagar que venzan hoy.")
        return "\n".join(lines)
    for razon, p in items:
        n = p["facturas"]
        lines.append(f"• {razon} — {money(p['monto'])}" + (f" ({n} facturas)" if n > 1 else ""))
    lines += ["", f"Total: {money(sum(p['monto'] for _, p in items))}"]
    return "\n".join(lines)


def main():
    if "--backup" in sys.argv:
        # El respaldo corre a las 08:15 y 09:15 UTC-3/UTC-4 según la época; solo vale el de las 8.
        if datetime.now(TZ).hour != 8:
            print("Fuera del horario del respaldo, no se envía nada.")
            return
        if recently_sent():
            print("El envío principal ya salió; el respaldo no hace nada.")
            return
    dia = datetime.now(TZ).date()
    body = build_report(vencen(load_rows(), load_cloud(), dia), dia)
    if "--dry-run" in sys.argv:
        print(body)
        return
    if not send_telegram(body):
        sys.exit("Faltan los secretos TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.")


if __name__ == "__main__":
    main()
