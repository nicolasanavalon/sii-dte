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
    if not para or not texto:
        sys.exit("❌ Falta el destinatario o el texto.")

    modo = p.get("modo") or "responder"            # responder | nuevo | reenviar
    try:
        propios = archivos_adjuntados(p.get("adj_nuevos"), clave)   # archivos que adjuntaste en la página
    except Exception as e:
        sys.exit(f"❌ No pude recuperar los archivos adjuntados: {str(e)[:300]}")
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
                    if modo == "responder":
                        z.responder(acc_id, str(p["message_id"]), principal, para, cc, asunto, texto, adjuntos)
                    else:
                        if modo == "reenviar" and p.get("con_adjuntos") and p.get("message_id") and p.get("carpeta"):
                            for a in z.adjuntos(acc_id, str(p["carpeta"]), str(p["message_id"]))[:10]:
                                datos = z.descargar_adjunto(acc_id, str(p["carpeta"]), str(p["message_id"]), a["attachmentId"])
                                adjuntos.append(z.subir_adjunto(acc_id, str(a.get("attachmentName") or "archivo"), datos))
                        z.enviar_nuevo(acc_id, principal, para, cc, asunto, texto, adjuntos)
                except ZohoError as e:
                    sys.exit(f"❌ {e}")
                que = {"responder": "Respuesta enviada", "nuevo": "Correo enviado", "reenviar": "Correo reenviado"}[modo]
                print(f"✅ {que} desde la casilla '{nombre}' a {len(para)} destinatario(s)"
                      + (f" y {len(cc)} en copia" if cc else "") + (f", con {len(adjuntos)} adjunto(s)." if adjuntos else "."))
                return
    sys.exit("❌ No encontré la casilla de Zoho de ese correo.")


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
