"""Envía una respuesta escrita en la Bandeja (la usa el workflow "Correo: enviar respuesta").

La página cifra la respuesta con tu contraseña (CORREO_CLAVE) antes de mandarla a GitHub, así que en los
registros públicos del workflow solo se ve texto cifrado. Este script la descifra y la envía con la API de Zoho.
Por privacidad, no imprime direcciones ni contenido.
"""
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import cripto  # noqa: E402
import sync  # noqa: E402
from zoho import Zoho, ZohoError  # noqa: E402

CORREO = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def main():
    clave = sync.env("CORREO_CLAVE")
    try:
        p = cripto.descifrar(json.loads(sync.env("PAQUETE")), clave)
    except Exception:
        sys.exit("❌ No se pudo abrir la respuesta (no fue cifrada con tu contraseña).")
    if abs(time.time() * 1000 - float(p.get("ts", 0))) > 2 * 3600 * 1000:
        sys.exit("❌ La respuesta es antigua (más de 2 horas); no se envía por seguridad.")
    if p.get("modo") == "leidos":
        return marcar_leidos(p, clave)
    para = [x.strip() for x in p.get("para", []) if CORREO.match(x.strip())]
    cc = [x.strip() for x in p.get("cc", []) if CORREO.match(x.strip())]
    texto, asunto = str(p.get("texto", "")).strip(), str(p.get("asunto", "")).strip()[:300]
    modo = p.get("modo") or "responder"            # responder | nuevo | reenviar
    if not para or (not texto and modo != "reenviar"):
        sys.exit("❌ Falta el destinatario o el texto.")
    try:
        propios = archivos_adjuntados(p.get("adj_nuevos"), clave)   # archivos que adjuntaste en la página
    except Exception as e:
        sys.exit(f"❌ No pude recuperar los archivos adjuntados: {str(e)[:300]}")
    if str(p.get("cuenta_id", "")).startswith("gmail:"):
        return enviar_gmail(p, modo, para, cc, asunto, texto, propios)
    for nombre, cid, sec, rt in sync.conexiones(clave):
        z = Zoho(cid, sec, rt)
        try:
            casillas = z.cuentas()
        except Exception:
            continue
        for acc_id, _, principal in casillas:
            if acc_id == str(p.get("cuenta_id")) or (not p.get("cuenta_id") and principal == p.get("cuenta")):
                try:
                    adjuntos = [z.subir_adjunto(acc_id, nombre, datos) for nombre, datos in propios]
                    original = ""
                    if modo in ("responder", "reenviar") and p.get("message_id") and p.get("carpeta"):
                        try:            # el correo original COMPLETO (con su propia cadena) para citarlo debajo
                            original = z.contenido(acc_id, str(p["carpeta"]), str(p["message_id"]))
                        except Exception as e:
                            print(f"::warning::No pude leer el correo original para citarlo: {str(e)[:150]}")
                    cuerpo, formato = con_historial(texto, modo, p, original)
                    if modo == "responder":
                        z.responder(acc_id, str(p["message_id"]), principal, para, cc, asunto, cuerpo, adjuntos, formato)
                    else:
                        if modo == "reenviar" and p.get("con_adjuntos") and p.get("message_id") and p.get("carpeta"):
                            for a in z.adjuntos(acc_id, str(p["carpeta"]), str(p["message_id"]))[:10]:
                                datos = z.descargar_adjunto(acc_id, str(p["carpeta"]), str(p["message_id"]), a["attachmentId"])
                                adjuntos.append(z.subir_adjunto(acc_id, str(a.get("attachmentName") or "archivo"), datos))
                        z.enviar_nuevo(acc_id, principal, para, cc, asunto, cuerpo, adjuntos, formato)
                except ZohoError as e:
                    sys.exit(f"❌ {e}")
                que = {"responder": "Respuesta enviada", "nuevo": "Correo enviado", "reenviar": "Correo reenviado"}[modo]
                print(f"✅ {que} desde la casilla '{nombre}' a {len(para)} destinatario(s)"
                      + (f" y {len(cc)} en copia" if cc else "") + (f", con {len(adjuntos)} adjunto(s)." if adjuntos else "."))
                return
    sys.exit("❌ No encontré la casilla de Zoho de ese correo.")


def _esc(t):
    import html
    return html.escape(str(t or ""))


def _sin_envoltura(h):
    """HTML del correo original listo para ir dentro de otro (sin <html>, <head> ni <body>)."""
    h = re.sub(r"(?is)<head\b.*?</head>|<!doctype[^>]*>|</?(html|body)\b[^>]*>", "", h or "")
    return re.sub(r"(?is)<(script|iframe|object|embed)\b.*?</\1>", "", h)


def _cabecera_original(p):
    o = p.get("orig") or {}
    quien = (f"{o.get('de_nombre')} <{o.get('de')}>" if o.get("de_nombre") else o.get("de", "")).strip()
    return o, quien


def _texto_historial(modo, p):
    """Versión en texto simple del historial (para los programas que no muestran HTML)."""
    if modo not in ("responder", "reenviar") or not (p.get("orig") or {}).get("texto"):
        return ""
    o, quien = _cabecera_original(p)
    if modo == "responder":
        return f"\n\nEl {o.get('fecha', '')}, {quien} escribió:\n" + "\n".join("> " + l for l in str(o["texto"]).splitlines())
    return (f"\n\n---------- Mensaje reenviado ----------\nDe: {quien}\nFecha: {o.get('fecha', '')}\nAsunto: {o.get('asunto', '')}\n"
            f"Para: {', '.join(o.get('para') or [])}\n" + (f"Cc: {', '.join(o.get('cc') or [])}\n" if o.get("cc") else "") + "\n" + str(o["texto"]))


def con_historial(texto, modo, p, original_html):
    """Arma el correo como Outlook/Zoho: tu mensaje arriba y, debajo, el correo anterior con TODA su cadena
    (al responder: "El …, X escribió:" citado; al reenviar: el bloque "Mensaje reenviado"). Devuelve (cuerpo, formato)."""
    if modo not in ("responder", "reenviar"):
        return texto, "plaintext"
    o, quien = _cabecera_original(p)
    original = _sin_envoltura(original_html) or (
        "<div style=\"white-space:pre-wrap\">" + _esc(o.get("texto", "")) + "</div>" if o.get("texto") else "")
    if not original:
        return texto + _texto_historial(modo, p), "plaintext"
    mio = f"<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;white-space:pre-wrap\">{_esc(texto)}</div>"
    gris = "font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#555"
    if modo == "responder":
        cita = (f"<br><div style=\"{gris}\">El {_esc(o.get('fecha', ''))}, {_esc(quien)} escribió:</div>"
                f"<blockquote style=\"margin:6px 0 0 4px;padding-left:10px;border-left:2px solid #c8c8c8\">{original}</blockquote>")
    else:
        filas = [("De", quien), ("Fecha", o.get("fecha", "")), ("Asunto", o.get("asunto", "")), ("Para", ", ".join(o.get("para") or []))]
        if o.get("cc"):
            filas.append(("Cc", ", ".join(o["cc"])))
        cita = (f"<br><div style=\"{gris}\">---------- Mensaje reenviado ----------<br>"
                + "<br>".join(f"{k}: {_esc(v)}" for k, v in filas) + f"</div><br>{original}")
    return mio + cita, "html"


def _gmail():
    import gmail
    if not (sync.env("GMAIL_CORREO") and sync.env("GMAIL_CLAVE")):
        sys.exit("❌ Falta configurar Gmail (secretos GMAIL_CORREO y GMAIL_CLAVE).")
    return gmail, gmail.Gmail(sync.env("GMAIL_CORREO"), sync.env("GMAIL_CLAVE"))


def enviar_gmail(p, modo, para, cc, asunto, texto, adjuntos):
    G, g = _gmail()
    adjuntos = list(adjuntos)
    try:
        original_html = ""
        if modo in ("responder", "reenviar") and str(p.get("message_id", "")).startswith("g"):
            orig = g.mensaje_por_id(str(p["message_id"])[1:])
            t, h, adj = G.partes(orig)
            original_html = h or ("<div style=\"white-space:pre-wrap\">" + _esc(t) + "</div>" if t else "")
            if modo == "reenviar" and p.get("con_adjuntos"):
                adjuntos += adj[:10]
        cuerpo, formato = con_historial(texto, modo, p, original_html)
        responde = p.get("mid_cabecera") if modo == "responder" else None
        g.enviar(para, cc, asunto, texto + _texto_historial(modo, p), adjuntos, responde_a=responde,
                 referencias=p.get("referencias") or None, html=cuerpo if formato == "html" else None)
    except G.GmailError as e:
        sys.exit(f"❌ {e}")
    que = {"responder": "Respuesta enviada", "nuevo": "Correo enviado", "reenviar": "Correo reenviado"}[modo]
    print(f"✅ {que} desde Gmail a {len(para)} destinatario(s)" + (f" y {len(cc)} en copia" if cc else "")
          + (f", con {len(adjuntos)} adjunto(s)." if adjuntos else "."))


def _gh(url, crudo=False):
    """GET a la API de GitHub con la llave del workflow (los archivos de descarga redirigen a otro sitio: sin llave)."""
    class SinRedireccion(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.build_opener(SinRedireccion).open(req, timeout=60) as r:
            return r.read() if crudo else json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
            with urllib.request.urlopen(e.headers["Location"], timeout=120) as r:
                return r.read()
        raise


def archivos_adjuntados(info, clave):
    """La página sube cada archivo cifrado en partes (workflow "Correo: subir adjunto"); aquí se juntan.
    Devuelve [(nombre, bytes)]."""
    if not info or not int(info.get("n") or 0):
        return []
    envio, n = re.sub(r"[^a-z0-9]", "", str(info.get("id")))[:20], min(int(info["n"]), 400)
    api = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/actions/artifacts"
    trozos, fin = {}, time.time() + 420
    while len(trozos) < n and time.time() < fin:
        for k in range(n):
            if k in trozos:
                continue
            arts = _gh(f"{api}?name=trozo-{envio}-{k}&per_page=1").get("artifacts") or []
            if arts and not arts[0].get("expired"):
                z = zipfile.ZipFile(io.BytesIO(_gh(arts[0]["archive_download_url"], crudo=True)))
                trozos[k] = (z.read("trozo.txt").decode(), arts[0]["id"])
        if len(trozos) < n:
            time.sleep(4)
    if len(trozos) < n:
        raise RuntimeError(f"llegaron {len(trozos)} de {n} partes")
    import base64
    caja = base64.b64decode("".join(trozos[k][0] for k in range(n)))
    plano = cripto.descifrar_bytes(caja, clave)
    largo = int.from_bytes(plano[:4], "big")
    cabecera, pos, salida = json.loads(plano[4:4 + largo]), 4 + largo, []
    for a in cabecera:
        nombre = re.sub(r'[\\/:*?"<>|\r\n]', "_", str(a.get("nombre") or "archivo"))[:150]
        salida.append((nombre, plano[pos:pos + int(a["tam"])]))
        pos += int(a["tam"])
    for _, art_id in trozos.values():              # ya no se necesitan (igual expiran en 1 día)
        try:
            req = urllib.request.Request(f"{api}/{art_id}", method="DELETE",
                                         headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"})
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            pass
    return salida


def marcar_leidos(p, clave):
    """p["ids"] = {cuenta_id: [message_id, ...]}: los correos abiertos en la página se marcan leídos en Zoho."""
    def lista(clave_ids):
        return {str(k): [str(x) for x in v][:500] for k, v in (p.get(clave_ids) or {}).items() if isinstance(v, list)}
    pedidos, pedidos_no = lista("ids"), lista("ids_no")
    hechos, hechos_no, fallas = 0, 0, []
    gm = [k for k in set(pedidos) | set(pedidos_no) if k.startswith("gmail:")]
    if gm:
        try:
            _, g = _gmail()
            for k in gm:
                hechos += g.marcar([x[1:] for x in pedidos.pop(k, []) if x.startswith("g")], leidos=True)
                hechos_no += g.marcar([x[1:] for x in pedidos_no.pop(k, []) if x.startswith("g")], leidos=False)
        except Exception as e:
            fallas.append(f"Gmail: {str(e)[:200]}")
    for nombre, cid, sec, rt in sync.conexiones(clave):
        z = Zoho(cid, sec, rt)
        try:
            casillas = z.cuentas()
        except Exception as e:
            fallas.append(f"{nombre}: {str(e)[:200]}")
            continue
        for acc_id, _, _ in casillas:
            try:
                if pedidos.get(str(acc_id)):
                    hechos += z.marcar_leidos(acc_id, pedidos.pop(str(acc_id)))
                if pedidos_no.get(str(acc_id)):
                    hechos_no += z.marcar_leidos(acc_id, pedidos_no.pop(str(acc_id)), leidos=False)
            except ZohoError as e:
                fallas.append(f"{nombre}: {e}")
    print(f"✅ En Zoho: {hechos} marcado(s) como leídos y {hechos_no} como no leídos.")
    for f in fallas:     # sin marcar la tarea como fallida: la página reintenta sola más tarde
        print(f"::warning::No pude marcar leídos en {f[:300]}")


if __name__ == "__main__":
    main()
