"""Avisos al celular (Web Push) para la app de correo agregada a la pantalla de inicio.

Cada aviso lleva quién escribió, el asunto y la cantidad de correos sin leer (para la burbuja del ícono).
Estándares: RFC 8291 (cifrado del mensaje, aes128gcm) y RFC 8292 (VAPID: identificación del servidor).
El contenido viaja cifrado de punta a punta: el servicio de avisos (Apple, Google…) no puede leerlo.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CONTACTO = "https://nicolasanavalon.github.io/sii-dte/correo/"   # dato que piden los servicios de avisos


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def de_b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _publica_cruda(llave_publica) -> bytes:
    return llave_publica.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def nuevas_llaves():
    """Llaves VAPID del servidor: {"privada": PEM, "publica": base64url (la usa la página al suscribirse)}."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    return {"privada": pem, "publica": b64u(_publica_cruda(priv.public_key()))}


def _hkdf(salt, ikm, info, largo):
    return HKDF(algorithm=hashes.SHA256(), length=largo, salt=salt, info=info).derive(ikm)


def cifrar(datos: bytes, p256dh: str, auth: str) -> bytes:
    """RFC 8291: cifra el contenido para la suscripción del celular."""
    ua_pub_bytes, secreto = de_b64u(p256dh), de_b64u(auth)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    efimera = ec.generate_private_key(ec.SECP256R1())
    as_pub = _publica_cruda(efimera.public_key())
    compartido = efimera.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(secreto, compartido, b"WebPush: info\x00" + ua_pub_bytes + as_pub, 32)
    sal = os.urandom(16)
    cek = _hkdf(sal, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(sal, ikm, b"Content-Encoding: nonce\x00", 12)
    cifrado = AESGCM(cek).encrypt(nonce, datos + b"\x02", None)
    return sal + (4096).to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + cifrado


def _vapid(endpoint: str, llaves: dict) -> str:
    u = urllib.parse.urlsplit(endpoint)
    cab = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    cuerpo = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": CONTACTO}).encode())
    priv = serialization.load_pem_private_key(llaves["privada"].encode(), None)
    r, s = decode_dss_signature(priv.sign(f"{cab}.{cuerpo}".encode(), ec.ECDSA(hashes.SHA256())))
    firma = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={cab}.{cuerpo}.{firma}, k={llaves['publica']}"


def enviar(sub: dict, mensaje: dict, llaves: dict) -> int:
    """Envía un aviso. Devuelve el código HTTP (201 = entregado; 404/410 = la suscripción ya no existe)."""
    datos = json.dumps(mensaje, ensure_ascii=False).encode()
    req = urllib.request.Request(sub["endpoint"], data=cifrar(datos, sub["keys"]["p256dh"], sub["keys"]["auth"]),
                                 method="POST", headers={
                                     "Authorization": _vapid(sub["endpoint"], llaves), "TTL": "86400",
                                     "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                                     "Urgency": "high"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
