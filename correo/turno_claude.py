"""Herramienta de la sesión programada de Claude (ver correo/TURNO_CLAUDE.md).

  python correo/turno_claude.py claves DIR              -> crea llave efímera en DIR, imprime la pública
  python correo/turno_claude.py abrir PENDIENTES DIR     -> descifra a DIR/pendientes.json
  python correo/turno_claude.py sellar DIR SALIDA        -> sella DIR/respuesta.json para GitHub Actions
  python correo/turno_claude.py validar DIR              -> revisa que respuesta.json cubra todos los correos
"""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cripto  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402


def _priv(d):
    return X25519PrivateKey.from_private_bytes(base64.b64decode((Path(d) / "llave.priv").read_text()))


def claves(d):
    Path(d).mkdir(parents=True, exist_ok=True)
    k = X25519PrivateKey.generate()
    raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    p = Path(d) / "llave.priv"
    p.write_text(base64.b64encode(raw).decode())
    p.chmod(0o600)
    print(cripto.llave_publica_b64(k))


def abrir(pendientes, d):
    datos = cripto.abrir_sello(json.loads(Path(pendientes).read_text()), _priv(d))
    (Path(d) / "pendientes.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1))
    n = sum(len(c["correos_nuevos"]) for c in datos["contactos"])
    print(f"Turno {datos['turno']}: {n} correos de {len(datos['contactos'])} contactos "
          f"(quedan {datos['quedan_despues']} para el próximo turno). Archivo: {Path(d) / 'pendientes.json'}")


def validar(d):
    pend = json.loads((Path(d) / "pendientes.json").read_text())
    resp = json.loads((Path(d) / "respuesta.json").read_text())
    esperados = {(c["email"], m["id"]) for c in pend["contactos"] for m in c["correos_nuevos"]}
    hechos = {(r.get("email"), str(x.get("id"))) for r in resp.get("resultados", []) for x in r.get("correos", [])}
    faltan = esperados - hechos
    errores = []
    for r in resp.get("resultados", []):
        c = r.get("contacto") or {}
        for k in ("nombre", "tipo", "resumen", "pendientes", "datos_clave", "plazos"):
            if k not in c:
                errores.append(f"{r.get('email')}: falta contacto.{k}")
    if faltan or errores:
        print("FALTA COMPLETAR:", *[f"correo {i} de {e}" for e, i in sorted(faltan)], *errores, sep="\n  ")
        sys.exit(1)
    print(f"OK: {len(hechos)} correos y {len(resp['resultados'])} fichas.")


def sellar(d, salida):
    validar(d)
    pend = json.loads((Path(d) / "pendientes.json").read_text())
    resp = json.loads((Path(d) / "respuesta.json").read_text())
    resp["turno"] = pend["turno"]
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    Path(salida).write_text(json.dumps(cripto.sellar(resp, pend["clave_accion"])))
    print(f"Respuesta sellada en {salida}")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["?"]
    {"claves": claves, "abrir": abrir, "sellar": sellar, "validar": validar}.get(
        cmd, lambda *a: sys.exit(__doc__))(*args)
