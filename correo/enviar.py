"""Envía una respuesta escrita en la Bandeja (la usa el workflow "Correo: enviar respuesta").

La página cifra la respuesta con tu contraseña (CORREO_CLAVE) antes de mandarla a GitHub, así que en los
registros públicos del workflow solo se ve texto cifrado. Este script la descifra y la envía con la API de Zoho.
Por privacidad, no imprime direcciones ni contenido.
"""
import json
import re
import sys
import time
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
    para = [x.strip() for x in p.get("para", []) if CORREO.match(x.strip())]
    cc = [x.strip() for x in p.get("cc", []) if CORREO.match(x.strip())]
    texto, asunto = str(p.get("texto", "")).strip(), str(p.get("asunto", "")).strip()[:300]
    if not para or not texto:
        sys.exit("❌ Falta el destinatario o el texto.")

    modo = p.get("modo") or "responder"            # responder | nuevo | reenviar
    for nombre, cid, sec, rt in sync.conexiones(clave):
        z = Zoho(cid, sec, rt)
        try:
            casillas = z.cuentas()
        except Exception:
            continue
        for acc_id, _, principal in casillas:
            if acc_id == str(p.get("cuenta_id")) or (not p.get("cuenta_id") and principal == p.get("cuenta")):
                try:
                    if modo == "responder":
                        z.responder(acc_id, str(p["message_id"]), principal, para, cc, asunto, texto)
                    else:
                        adjuntos = []
                        if modo == "reenviar" and p.get("con_adjuntos") and p.get("message_id") and p.get("carpeta"):
                            for a in z.adjuntos(acc_id, str(p["carpeta"]), str(p["message_id"]))[:10]:
                                datos = z.descargar_adjunto(acc_id, str(p["carpeta"]), str(p["message_id"]), a["attachmentId"])
                                adjuntos.append(z.subir_adjunto(acc_id, str(a.get("attachmentName") or "archivo"), datos))
                        z.enviar_nuevo(acc_id, principal, para, cc, asunto, texto, adjuntos)
                except ZohoError as e:
                    sys.exit(f"❌ {e}")
                que = {"responder": "Respuesta enviada", "nuevo": "Correo enviado", "reenviar": "Correo reenviado"}[modo]
                print(f"✅ {que} desde la casilla '{nombre}' a {len(para)} destinatario(s)"
                      + (f" y {len(cc)} en copia" if cc else "") + (f", con {len(adjuntos)} adjunto(s)." if modo == "reenviar" else "."))
                return
    sys.exit("❌ No encontré la casilla de Zoho de ese correo.")


if __name__ == "__main__":
    main()
