"""Facturas a OneDrive: las fotos/PDF de las facturas que llegan en el correo diario de la VALIJA (y las de
cualquier correo donde aprietes "Subir a OneDrive") se suben solas a la carpeta de su proveedor.

La subida la hace rclone (programa gratuito y de código abierto para OneDrive). La conexión se hace una sola vez:
en tu Mac corres `rclone authorize "onedrive"`, inicias sesión en Microsoft y pegas el resultado en la página.
Ese "token" se guarda CIFRADO con tu contraseña en los datos del correo (rclone lo va renovando solo).

Carpeta del proveedor: la primera vez que llega un proveedor nuevo, la página te pregunta en qué carpeta va
(si hay una carpeta con el mismo nombre, se usa sola). Tu respuesta queda guardada y no se vuelve a preguntar.
"""
import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

BASE = "Anasep 2/Facturas"                      # carpeta de OneDrive con una subcarpeta por proveedor
EXT_FACTURA = (".jpg", ".jpeg", ".png", ".pdf", ".heic", ".webp", ".tif", ".tiff")
RCLONE = os.environ.get("RCLONE_BIN", "rclone")


def norm(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", t)


def proveedor(nombre):
    """'CENTRO PAN 24229 DEL 19 NOVIEMBRE.jpg' -> 'CENTRO PAN'. Las palabras hasta el primer número (N° de factura)."""
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", str(nombre or "")).replace("_", " ")
    palabras = []
    for w in base.split():
        if re.fullmatch(r"(N[°ºO]?\.?|#)?\d[\d.\-]*", w, re.I) or w.upper() in ("DEL", "FACTURA", "FACT", "FAC"):
            break
        palabras.append(w)
    return " ".join(palabras).strip(" -.,") or base.strip()


def es_factura(nombre):
    return str(nombre or "").lower().endswith(EXT_FACTURA)


def es_valija(m):
    return (not m.get("enviado") and re.search(r"valija", m.get("asunto") or "", re.I)
            and "platon.cl" in (m.get("de") or "").lower())


class OneDrive:
    def __init__(self, od):
        """od = estado["onedrive"] (token, drive_id, drive_type)."""
        self.od = od
        self.dir = Path(tempfile.mkdtemp(prefix="od"))
        self.conf = self.dir / "rclone.conf"
        self._escribir()

    def _escribir(self):
        self.conf.write_text(
            "[od]\ntype = onedrive\n"
            f"token = {json.dumps(self.od['token'], separators=(',', ':'))}\n"
            f"drive_id = {self.od['drive_id']}\ndrive_type = {self.od['drive_type']}\n", encoding="utf-8")

    def _token_al_dia(self):
        """rclone renueva el token y lo reescribe en su archivo: se copia al estado para no perderlo."""
        m = re.search(r"^token\s*=\s*(.+)$", self.conf.read_text(encoding="utf-8"), re.M)
        if m:
            try:
                t = json.loads(m.group(1))
                if t != self.od["token"]:
                    self.od["token"] = t
                    return True
            except Exception:
                pass
        return False

    def rc(self, *args, timeout=180):
        r = subprocess.run([RCLONE, "--config", str(self.conf), "--retries", "2", "--low-level-retries", "3", *args],
                           capture_output=True, text=True, timeout=timeout)
        self._token_al_dia()
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "rclone falló").strip().splitlines()[-1][:300])
        return r.stdout

    def carpetas(self, base=BASE):
        return sorted(x.rstrip("/") for x in self.rc("lsf", "--dirs-only", f"od:{base}").splitlines() if x.strip())

    def subir(self, datos, ruta):
        tmp = self.dir / "subir.bin"
        tmp.write_bytes(datos)
        try:
            self.rc("copyto", "--ignore-existing", str(tmp), f"od:{ruta}", timeout=600)
        finally:
            tmp.unlink(missing_ok=True)


def conectar(texto):
    """Del texto que entrega `rclone authorize "onedrive"` saca el token y averigua cuál es tu OneDrive."""
    m = re.search(r"\{.*\"access_token\".*\}", texto or "", re.S)
    if not m:
        raise ValueError("No encontré el token en lo que pegaste (debe incluir {\"access_token\": …}).")
    token = json.loads(m.group(0))
    req = urllib.request.Request("https://graph.microsoft.com/v1.0/me/drive",
                                 headers={"Authorization": "Bearer " + token["access_token"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        drive = json.loads(r.read())
    return {"token": token, "drive_id": drive["id"], "drive_type": drive.get("driveType") or "personal",
            "conectado_ms": int(time.time() * 1000)}
