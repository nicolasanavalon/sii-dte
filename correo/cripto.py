"""Cifrado con contraseña (AES-256-GCM + PBKDF2-SHA256).

Mismo formato que descifra la página web en el navegador (WebCrypto), así los correos
quedan guardados en el repo y publicados en Pages, pero ilegibles sin tu contraseña.
"""
import base64
import gzip
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERACIONES = 250_000


def _llave(clave: str, salt: bytes, iteraciones: int = ITERACIONES) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iteraciones)
    return kdf.derive(clave.encode("utf-8"))


def cifrar(obj, clave: str) -> dict:
    """Comprime (gzip) y cifra un objeto JSON. Devuelve un dict serializable."""
    salt, iv = os.urandom(16), os.urandom(12)
    plano = gzip.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    ct = AESGCM(_llave(clave, salt)).encrypt(iv, plano, None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "iter": ITERACIONES, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def descifrar(caja: dict, clave: str):
    d = lambda k: base64.b64decode(caja[k])
    llave = _llave(clave, d("salt"), int(caja.get("iter", ITERACIONES)))
    plano = AESGCM(llave).decrypt(d("iv"), d("ct"), None)
    return json.loads(gzip.decompress(plano).decode("utf-8"))


# ---- Intercambio con la sesión programada de Claude -------------------------------------
# La sesión de Claude genera una llave efímera por turno: GitHub Actions le sella los correos
# pendientes con esa llave pública, y Claude le devuelve los resúmenes sellados con la llave
# pública de Actions (derivada de CORREO_CLAVE). Así ninguna contraseña vive en la sesión.
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

_RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def _b64(b):
    return base64.b64encode(b).decode()


def llave_publica_b64(priv: X25519PrivateKey) -> str:
    return _b64(priv.public_key().public_bytes(**_RAW))


def llave_accion(clave: str) -> X25519PrivateKey:
    """Llave fija de GitHub Actions, derivada de CORREO_CLAVE (nunca se guarda)."""
    return X25519PrivateKey.from_private_bytes(_llave(clave, b"correo-intercambio-v1"))


def sellar(obj, pub_b64: str) -> dict:
    """Cifra para el dueño de la llave pública pub_b64 (X25519 + HKDF + AES-GCM)."""
    eph = X25519PrivateKey.generate()
    compartido = eph.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)))
    llave = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"correo-sello-v1").derive(compartido)
    iv = os.urandom(12)
    ct = AESGCM(llave).encrypt(iv, gzip.compress(json.dumps(obj, ensure_ascii=False).encode()), None)
    return {"v": 1, "para": pub_b64, "epk": llave_publica_b64(eph), "iv": _b64(iv), "ct": _b64(ct)}


def abrir_sello(caja: dict, priv: X25519PrivateKey):
    compartido = priv.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(caja["epk"])))
    llave = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"correo-sello-v1").derive(compartido)
    plano = AESGCM(llave).decrypt(base64.b64decode(caja["iv"]), base64.b64decode(caja["ct"]), None)
    return json.loads(gzip.decompress(plano).decode("utf-8"))


# ---- Archivos adjuntos cifrados (formato binario: salt[16] | iv[12] | datos cifrados) -------------
def cifrar_bytes(datos: bytes, clave: str) -> bytes:
    salt, iv = os.urandom(16), os.urandom(12)
    return salt + iv + AESGCM(_llave(clave, salt)).encrypt(iv, datos, None)


def descifrar_bytes(caja: bytes, clave: str) -> bytes:
    return AESGCM(_llave(clave, caja[:16])).decrypt(caja[16:28], caja[28:], None)
