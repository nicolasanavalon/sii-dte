"""Reporte horario de "Pulso de venta" (Platón Marina Arauco).

Lee /api/sales de la página y envía un resumen por Telegram.
Solo usa la librería estándar de Python.

Variables de entorno:
  PULSO_URL           URL base de la página (por defecto la de Platón)
  REPORT_WINDOW       horario de Chile en que se envía, ej. "10:00-20:30" (vacío = siempre)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Con --backup (corrida de respaldo de GitHub) no envía si otra corrida del mismo
workflow ya terminó bien en los últimos BACKUP_LOOKBACK_MIN minutos; usa
GITHUB_TOKEN, GITHUB_REPOSITORY y GITHUB_RUN_ID que entrega GitHub Actions.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
BASE_URL = os.environ.get("PULSO_URL") or "https://pulso-ventas-platon.nicolas-anavalon.chatgpt.site"


def env(name):
    return (os.environ.get(name) or "").strip()


def money(value):
    value = round(value or 0)
    return ("-" if value < 0 else "") + "$" + f"{abs(value):,}".replace(",", ".")


def signed_money(value):
    return ("+" if round(value) > 0 else "") + money(value)


def pct(value):
    if value is None:
        return "—"
    return f"{value:+.1f}%".replace(".", ",")


def compare(actual, goal):
    variation = (actual / goal - 1) * 100 if goal else None
    return f"{money(actual)} · Meta: {money(goal)}  {pct(variation)} · {signed_money(actual - goal)}"


def with_retries(fn, attempts=4):
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if i == attempts - 1:
                raise
            print(f"Falló ({exc}); reintentando…")
            time.sleep(10 * (i + 1))


def fetch_sales():
    req = urllib.request.Request(BASE_URL.rstrip("/") + "/api/sales", headers={"accept": "application/json", "user-agent": "Mozilla/5.0 (pulso-report)"})

    def get():
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    return with_retries(get)


def checkpoints(d, now):
    """Cortes de :00 (checkpoints) y :30 (telegramCheckpoints) ya cerrados, del más nuevo al más viejo."""
    points = [
        (c["hour"], 0, c["actual"], c["plan"])
        for c in d.get("checkpoints") or []
        if c.get("status") != "future" and c.get("actual") is not None and c.get("plan")
    ] + [
        (c["hour"], c.get("minute", 30), c["actual"], c["plan"])
        for c in d.get("telegramCheckpoints") or []
        if c.get("actual") is not None and c.get("plan")
    ]
    return sorted((p for p in points if (p[0], p[1]) <= (now.hour, now.minute)), reverse=True)


def build_report(d):
    now = datetime.now(TZ)
    slot = now.replace(minute=0 if now.minute < 30 else 30)
    lines = ["📊 Pulso de venta"]
    for hour, minute, actual, plan in checkpoints(d, now)[:2]:
        lines += [f"Checkpoint {hour:02d}:{minute:02d}  Acum: {compare(actual, plan)}", ""]
    lines.append(f"Proy. {slot:%H:%M}  Real: {compare(d.get('projectedSales') or 0, d.get('dailyGoal') or 0)}")
    if not d.get("connected", True):
        lines += ["", "⚠️ La página indica que la conexión con Justo está caída."]
    return "\n".join(lines)


def send_telegram(body):
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": body, "disable_web_page_preview": "true"}).encode()
    with_retries(lambda: urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=60).read())
    print("Telegram enviado")
    return True


def within_window():
    window = env("REPORT_WINDOW")
    if not window or "--force" in sys.argv:
        return True
    start, end = (datetime.strptime(x, "%H:%M").time() for x in window.split("-"))
    return start <= datetime.now(TZ).time() <= end


def recently_sent():
    """True si otra corrida de este workflow terminó bien hace poco (evita duplicados del respaldo)."""
    repo, run_id, token = env("GITHUB_REPOSITORY"), env("GITHUB_RUN_ID"), env("GITHUB_TOKEN")
    run = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}",
        headers={"authorization": f"Bearer {token}", "accept": "application/vnd.github+json"}), timeout=60))
    since = datetime.now(timezone.utc) - timedelta(minutes=int(env("BACKUP_LOOKBACK_MIN") or 20))
    runs = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{run['workflow_id']}/runs?status=success&per_page=10",
        headers={"authorization": f"Bearer {token}", "accept": "application/vnd.github+json"}), timeout=60))["workflow_runs"]
    return any(
        r["id"] != run["id"] and datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= since
        for r in runs
    )


def main():
    if not within_window():
        print("Fuera del horario de reporte, no se envía nada.")
        return
    if "--backup" in sys.argv and recently_sent():
        print("El envío principal ya salió; el respaldo no hace nada.")
        return
    body = build_report(fetch_sales())
    if "--dry-run" in sys.argv:
        print(body)
        return
    if not send_telegram(body):
        sys.exit("Faltan los secretos TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.")


if __name__ == "__main__":
    main()
