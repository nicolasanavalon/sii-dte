"""Reporte horario de "Pulso de venta" (Platón Marina Arauco).

Lee /api/sales de la página y envía un resumen por email y/o Telegram.
Solo usa la librería estándar de Python.

Variables de entorno (cada canal se activa si están sus datos):
  PULSO_URL            URL base de la página (por defecto la de Platón)
  REPORT_HOURS         rango de horas de Chile en que se envía, ej. "9-22" (vacío = siempre)
  Email (Gmail):  SMTP_USER, SMTP_PASSWORD (contraseña de aplicación), REPORT_TO
                  opcional SMTP_HOST (smtp.gmail.com), SMTP_PORT (465)
  Telegram:       TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
BASE_URL = os.environ.get("PULSO_URL") or "https://pulso-ventas-platon.nicolas-anavalon.chatgpt.site"


def env(name):
    return (os.environ.get(name) or "").strip()


def money(value):
    return "$" + f"{round(value or 0):,}".replace(",", ".")


def pct(value):
    if value is None:
        return "—"
    return f"{value:+.1f}%".replace(".", ",")


def fetch_sales():
    req = urllib.request.Request(BASE_URL.rstrip("/") + "/api/sales", headers={"accept": "application/json", "user-agent": "Mozilla/5.0 (pulso-report)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def build_report(d):
    now = datetime.now(TZ)
    actual = d.get("currentSales") or 0
    plan = d.get("planSales") or 0
    goal = d.get("dailyGoal") or 0
    projected = d.get("projectedSales") or 0
    vs_plan = (actual / plan - 1) * 100 if plan else None
    proj_vs_goal = (projected / goal - 1) * 100 if goal else None

    subject = f"Pulso {d.get('storeName', '')} {now:%H:%M} · {money(actual)} ({pct(vs_plan)} vs plan)"
    lines = [
        f"Pulso de venta · {d.get('storeName', '')} — {now:%d-%m-%Y %H:%M}",
        "",
        f"Venta acumulada: {money(actual)}",
        f"Plan a esta hora: {money(plan)}  ({pct(vs_plan)})",
        f"Proyección del día: {money(projected)}",
        f"Meta del día: {money(goal)}  (proyección {pct(proj_vs_goal)})",
    ]
    if d.get("priorFinalSales"):
        lines.append(f"Cierre día de referencia: {money(d['priorFinalSales'])}")

    hours = [c for c in d.get("checkpoints") or [] if c.get("status") != "future" and (c.get("actual") or c.get("plan"))]
    if hours:
        lines += ["", "Por hora (acumulado real / plan):"]
        for c in hours:
            lines.append(f"  {c['hour']:02d}:00  {money(c.get('actual'))} / {money(c.get('plan'))}  {pct(c.get('variationPercent'))}")

    top = (d.get("topProducts") or [])[:5]
    if top:
        lines += ["", "Top productos:"]
        for p in top:
            lines.append(f"  {p['units']} × {p['name']} — {money(p['sales'])}")

    if not d.get("connected", True):
        lines += ["", "⚠️ La página indica que la conexión con Justo está caída."]
    lines += ["", BASE_URL]
    return subject, "\n".join(lines)


def send_email(subject, body):
    user, password, to = env("SMTP_USER"), env("SMTP_PASSWORD"), env("REPORT_TO")
    if not (user and password and to):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    with smtplib.SMTP_SSL(env("SMTP_HOST") or "smtp.gmail.com", int(env("SMTP_PORT") or 465), timeout=60) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print("Email enviado a", to)
    return True


def send_telegram(body):
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": body, "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=60).read()
    print("Telegram enviado")
    return True


def within_hours():
    window = env("REPORT_HOURS")
    if not window or "--force" in sys.argv:
        return True
    start, end = (int(x) for x in window.split("-"))
    return start <= datetime.now(TZ).hour <= end


def main():
    if not within_hours():
        print("Fuera del horario de reporte, no se envía nada.")
        return
    subject, body = build_report(fetch_sales())
    if "--dry-run" in sys.argv:
        print(subject, body, sep="\n\n")
        return
    sent = send_email(subject, body)
    sent = send_telegram(body) or sent
    if not sent:
        sys.exit("No hay canal configurado: define SMTP_USER/SMTP_PASSWORD/REPORT_TO o TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.")


if __name__ == "__main__":
    main()
