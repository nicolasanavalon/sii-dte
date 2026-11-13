"""Sincroniza Zoho Mail -> analiza con Claude -> genera la página cifrada.

Uso:
  python correo/sync.py            # corrida normal (la usa GitHub Actions)
  python correo/sync.py --solo-html   # solo regenera la página (p. ej. tras cambiar el diseño)
  python correo/sync.py --demo        # datos de ejemplo, sin Zoho ni Claude (para probar la página)

Variables de entorno:
  CORREO_CLAVE         contraseña con la que se cifra todo (la misma que usas para abrir la página)
  ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET   del "Self Client" en api-console.zoho.com
  ZOHO_REFRESH_TOKEN   opcional; si no está, se usa correo/data/zoho_token.enc (paso "Conectar")
  ZOHO_DC              com (por defecto), eu, in, com.au, ...
  ANTHROPIC_API_KEY    para los resúmenes
  CORREO_DIAS_INICIALES   cuántos días hacia atrás trae la primera vez (30)
  CORREO_MAX_POR_CORRIDA  tope de correos nuevos por corrida (150)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   opcional: aviso de correos importantes
  CORREO_URL           dirección de la página publicada. Ahí vive el estado (cifrado): cada corrida
                       la lee, agrega lo nuevo y la vuelve a publicar. Sin ella se usa correo/index.html.
"""
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import cripto  # noqa: E402

DATA = AQUI / "data"
TOKEN = DATA / "zoho_token.enc"
PLANTILLA = AQUI / "plantilla.html"
SALIDA = Path(os.environ.get("CORREO_SALIDA") or AQUI / "index.html")

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Santiago")
except Exception:
    TZ = timezone(timedelta(hours=-3))

MAX_MENSAJES = 3000      # se guardan los últimos N correos
MAX_TEXTO_GUARDADO = 15000
LOTE = 12                # correos por llamada a Claude


def env(k, d=""):
    return (os.environ.get(k) or d).strip()


def fecha_txt(ms):
    return datetime.fromtimestamp(int(ms) / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def estado_vacio():
    return {"version": 1, "cuenta": "", "propios": [], "ultimo_ms": 0, "ultima_sync": "",
            "mensajes": {}, "contactos": {}, "errores": [], "avisados": []}


def _caja_de_html(html):
    """Extrae el bloque cifrado embebido en la página (None si la página aún no tiene datos)."""
    marca = "const CAJA = "
    i = html.find(marca)
    if i < 0:
        raise RuntimeError("La página no tiene el formato esperado.")
    fin = html.index(";\n", i)
    return json.loads(html[i + len(marca):fin])


def cargar_estado(clave):
    """El estado vive DENTRO de la página publicada (cifrado), así no se llena el repo de commits.

    Con CORREO_URL se lee la página publicada; sin ella, el archivo local correo/index.html.
    """
    url = env("CORREO_URL")
    if url:
        try:
            req = urllib.request.Request(f"{url}?nocache={int(time.time())}",
                                         headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=60) as r:
                html = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print("Primera corrida: la página aún no existe, se parte de cero.")
                return estado_vacio()
            raise RuntimeError(f"No pude leer la página publicada ({e.code}); se aborta para no perder datos.")
    elif SALIDA.exists():
        html = SALIDA.read_text(encoding="utf-8")
    else:
        return estado_vacio()
    caja = _caja_de_html(html)
    if not caja:
        return estado_vacio()
    try:
        estado = cripto.descifrar(caja, clave)
    except Exception:
        raise RuntimeError("No pude descifrar los datos guardados: ¿cambiaste CORREO_CLAVE? "
                           "Se aborta para no sobrescribir tus datos.")
    return {**estado_vacio(), **estado}


def recortar(estado):
    if len(estado["mensajes"]) > MAX_MENSAJES:
        orden = sorted(estado["mensajes"].values(), key=lambda m: m["fecha"], reverse=True)
        estado["mensajes"] = {m["id"]: m for m in orden[:MAX_MENSAJES]}


# ---------------------------------------------------------------- Zoho

def refresh_token(clave):
    rt = env("ZOHO_REFRESH_TOKEN")
    if rt:
        return rt
    if TOKEN.exists():
        return cripto.descifrar(json.loads(TOKEN.read_text()), clave)["refresh_token"]
    sys.exit("Falta conectar Zoho: corre el workflow 'Correo: conectar Zoho' (ver correo/README.md).")


def traer_nuevos(estado, clave):
    from zoho import Zoho, direcciones, html_a_texto

    z = Zoho(env("ZOHO_CLIENT_ID"), env("ZOHO_CLIENT_SECRET"), refresh_token(clave))
    account_id, propios = z.cuenta()
    estado["propios"] = sorted(set(estado.get("propios", [])) | propios)
    estado["cuenta"] = estado["cuenta"] or (sorted(propios)[0] if propios else "")
    propios = set(estado["propios"])

    carpetas = z.carpetas(account_id)
    def es(c, nombres):
        return (str(c.get("folderType", "")).lower() in nombres or
                str(c.get("folderName", "")).lower() in nombres)
    elegidas = [(c, False) for c in carpetas if es(c, {"inbox", "bandeja de entrada"})]
    elegidas += [(c, True) for c in carpetas if es(c, {"sent", "enviados", "elementos enviados"})]
    if not elegidas:
        raise RuntimeError("No encontré la bandeja de entrada en Zoho. Carpetas: " +
                           ", ".join(str(c.get("folderName")) for c in carpetas))

    dias = int(env("CORREO_DIAS_INICIALES", "30"))
    tope = int(env("CORREO_MAX_POR_CORRIDA", "150"))
    if estado["ultimo_ms"]:
        desde = estado["ultimo_ms"] - 2 * 86400_000          # margen de 2 días
    else:
        desde = int((time.time() - dias * 86400) * 1000)
    conocidos = set(estado["mensajes"])

    candidatos = []
    for carpeta, enviados in elegidas:
        start = 1
        while True:
            pagina = z.listar(account_id, carpeta["folderId"], start=start, limit=100)
            parar = False
            for m in pagina:
                t = int(float(m.get("receivedTime") or m.get("sentDateInGMT") or 0))
                if t and t < desde:
                    parar = True
                    break
                if str(m["messageId"]) not in conocidos:
                    candidatos.append((carpeta, enviados, t, m))
            if parar or len(pagina) < 100 or start > 2000:
                break
            start += 100

    candidatos.sort(key=lambda x: x[2])
    if len(candidatos) > tope:
        print(f"Hay {len(candidatos)} correos nuevos; se procesan los {tope} más recientes ahora "
              f"y el resto queda fuera (sube CORREO_MAX_POR_CORRIDA si quieres todos).")
        candidatos = candidatos[-tope:]

    nuevos = []
    for carpeta, enviados_carpeta, t, m in candidatos:
        mid = str(m["messageId"])
        try:
            texto = html_a_texto(z.contenido(account_id, carpeta["folderId"], mid))
        except Exception as e:
            print(f"::warning::No pude leer el contenido del correo {mid}: {e}")
            texto = ""
        de = direcciones(m.get("fromAddress")) or [("", "")]
        de_nombre = (m.get("sender") or de[0][0] or "").strip()
        para = [a for _, a in direcciones(m.get("toAddress"))]
        cc = [a for _, a in direcciones(m.get("ccAddress"))]
        enviado = enviados_carpeta or de[0][1] in propios
        if enviado:
            otros = [a for a in para + cc if a not in propios]
            contacto = otros[0] if otros else (para[0] if para else de[0][1])
        else:
            contacto = de[0][1]
        msg = {
            "id": mid, "hilo": str(m.get("threadId") or ""), "fecha": t,
            "de": de[0][1], "de_nombre": de_nombre, "para": para, "cc": cc,
            "asunto": (m.get("subject") or "(sin asunto)").strip(),
            "vista_previa": (m.get("summary") or "").strip(),
            "texto": texto[:MAX_TEXTO_GUARDADO], "adjuntos": bool(m.get("hasAttachment") in (True, "1", 1)),
            "enviado": bool(enviado), "contacto": contacto or "(desconocido)", "analisis": None,
        }
        if not enviado and de_nombre:
            msg["nombre_contacto"] = de_nombre
        estado["mensajes"][mid] = msg
        nuevos.append(msg)
        estado["ultimo_ms"] = max(estado["ultimo_ms"], t)
    print(f"Zoho: {len(nuevos)} correos nuevos.")
    return nuevos


# ---------------------------------------------------------------- Claude

def analizar_pendientes(estado):
    from analisis import analizar_contacto

    sin = [m for m in estado["mensajes"].values() if m.get("analisis") is None]
    if not sin:
        return []
    if not env("ANTHROPIC_API_KEY"):
        print("::warning::Falta ANTHROPIC_API_KEY: los correos se guardan, pero sin resumen todavía.")
        return []
    grupos = defaultdict(list)
    for m in sorted(sin, key=lambda m: m["fecha"]):
        grupos[m["contacto"]].append(m)

    hoy = datetime.now(TZ).strftime("%Y-%m-%d (%A)")
    analizados = []
    for contacto, msgs in grupos.items():
        for i in range(0, len(msgs), LOTE):
            lote = msgs[i:i + LOTE]
            ficha = estado["contactos"].get(contacto, {})
            try:
                r = analizar_contacto(ficha, lote, hoy, fecha_txt)
            except Exception as e:
                traceback.print_exc()
                estado.setdefault("errores", []).append(
                    {"cuando": datetime.now(TZ).isoformat(timespec="minutes"), "contacto": contacto, "error": str(e)[:300]})
                break
            c = r["contacto"]
            nombre = c.get("nombre") or next((m.get("nombre_contacto") for m in lote if m.get("nombre_contacto")), "") or contacto
            estado["contactos"][contacto] = {
                **c, "email": contacto, "nombre": nombre,
                "actualizado": datetime.now(TZ).isoformat(timespec="minutes"),
            }
            por_id = {x["id"]: x for x in r["correos"]}
            for m in lote:
                a = por_id.get(m["id"])
                m["analisis"] = a or {"resumen": m["vista_previa"][:200], "importancia": "media",
                                      "requiere_respuesta": False, "accion": ""}
                analizados.append(m)
    estado["errores"] = estado.get("errores", [])[-20:]
    print(f"Claude: {len(analizados)} correos analizados en {len(grupos)} contactos.")
    return analizados


# ---------------------------------------------------------------- Telegram

def avisar(estado, analizados):
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat) or env("CORREO_TELEGRAM", "1") == "0":
        return
    limite = (time.time() - 36 * 3600) * 1000   # no avisar correos viejos (primera importación)
    ya = set(estado.get("avisados", []))
    altos = [m for m in analizados if not m["enviado"] and m["fecha"] >= limite and m["id"] not in ya
             and (m["analisis"] or {}).get("importancia") == "alta"]
    if not altos:
        return
    estado["avisados"] = (estado.get("avisados", []) + [m["id"] for m in altos])[-300:]
    lineas = [f"📬 {len(altos)} correo(s) importante(s):"]
    for m in altos[:8]:
        a = m["analisis"]
        nombre = estado["contactos"].get(m["contacto"], {}).get("nombre") or m["de"]
        lineas.append(f"\n• {nombre} — {m['asunto']}\n  {a['resumen']}")
        if a.get("accion"):
            lineas.append(f"  👉 {a['accion']}")
    if env("CORREO_URL"):
        lineas.append(f"\nVer todo: {env('CORREO_URL')}")
    data = urllib.parse.urlencode({"chat_id": chat, "text": "\n".join(lineas)[:4000],
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=60).read()
        print("Telegram: aviso enviado.")
    except Exception as e:
        print(f"::warning::No se pudo enviar el aviso por Telegram: {e}")


# ---------------------------------------------------------------- Página

def generar_html(estado, clave):
    caja = cripto.cifrar(estado, clave)
    html = PLANTILLA.read_text(encoding="utf-8").replace("/*__DATOS__*/null", json.dumps(caja))
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(html, encoding="utf-8")
    print(f"Página generada: {SALIDA} ({len(html) // 1024} KB)")


def marcar_cambio(valor):
    salida = env("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a") as f:
            f.write(f"cambio={'true' if valor else 'false'}\n")


def main():
    clave = env("CORREO_CLAVE")
    if len(clave) < 8:
        sys.exit("Falta CORREO_CLAVE (mínimo 8 caracteres): es la contraseña que protege tus correos.")

    if "--demo" in sys.argv:
        from demo import estado_demo
        estado = estado_demo()
        estado["ultima_sync"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        generar_html(estado, clave)
        return

    estado = cargar_estado(clave)
    if "--solo-html" in sys.argv:
        generar_html(estado, clave)
        marcar_cambio(True)
        return

    nuevos = traer_nuevos(estado, clave)
    analizados = analizar_pendientes(estado)
    cambio = bool(nuevos or analizados) or not estado["ultima_sync"]
    estado["ultima_sync"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    recortar(estado)
    avisar(estado, analizados)
    if cambio or "--forzar" in sys.argv:
        generar_html(estado, clave)
    else:
        print("Sin correos nuevos: la página publicada queda igual.")
    marcar_cambio(cambio)


if __name__ == "__main__":
    main()
