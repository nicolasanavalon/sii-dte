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
