"""Paso único: canjea el código de Zoho por un acceso permanente y prueba que la API funcione.

Lo corre el workflow "Correo: conectar Zoho". Guarda el acceso cifrado con CORREO_CLAVE en
correo/data/zoho_token.enc (ilegible sin tu contraseña).
"""
import json
import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import cripto  # noqa: E402
from zoho import Zoho, ZohoError, canjear_codigo  # noqa: E402


def main():
    e = lambda k: (os.environ.get(k) or "").strip()
    conexion = e("ZOHO_CONEXION") or "principal"           # "principal" o "taller"
    pre = "ZOHO_" if conexion == "principal" else f"ZOHO_{conexion.upper()}_"
    cid, sec = e(pre + "CLIENT_ID"), e(pre + "CLIENT_SECRET")
    falta = [k for k, v in ((pre + "CLIENT_ID", cid), (pre + "CLIENT_SECRET", sec), ("código", e("ZOHO_CODIGO")),
                            ("CORREO_CLAVE", e("CORREO_CLAVE"))) if not v]
    if falta:
        sys.exit("Faltan: " + ", ".join(falta))

    try:
        rt = canjear_codigo(cid, sec, e("ZOHO_CODIGO"))
        print("✅ Código canjeado: Zoho entregó el acceso permanente.")
        z = Zoho(cid, sec, rt)
        casillas = z.cuentas()
        print(f"✅ {len(casillas)} casilla(s) de correo encontrada(s) con este permiso.")
        account_id = casillas[0][0]
        carpetas = z.carpetas(account_id)
        print("✅ Carpetas: " + ", ".join(str(c.get("folderName")) for c in carpetas))
        inbox = next((c for c in carpetas if str(c.get("folderType", "")).lower() == "inbox"), carpetas[0])
        lista = z.listar(account_id, inbox["folderId"], limit=3)
        print(f"✅ Lectura de correos OK ({len(lista)} de prueba). ¡La API funciona con tu plan!")
        if lista:
            z.contenido(account_id, inbox["folderId"], lista[0]["messageId"])
            print("✅ Lectura del contenido OK.")
    except ZohoError as err:
        sys.exit(f"❌ {err}")

    (AQUI / "data").mkdir(exist_ok=True)
    nombre = "zoho_token.enc" if conexion == "principal" else f"zoho_token_{conexion}.enc"
    (AQUI / "data" / nombre).write_text(
        json.dumps(cripto.cifrar({"refresh_token": rt}, e("CORREO_CLAVE"))))
    print("✅ Acceso guardado (cifrado). Ya puedes correr 'Correo: sincronizar'.")


if __name__ == "__main__":
    main()
