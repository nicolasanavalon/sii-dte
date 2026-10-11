"""Sincroniza el catálogo de banderas desde la carpeta SUBLIMAR del OneDrive.

- Lista todos los PDF de la carpeta (solo lectura).
- Descarga los PDF nuevos o modificados y genera una miniatura (miniaturas/<id>.webp).
- Escribe catalogo.json, que es lo que lee la web (index.html).

Uso:
    MS_REFRESH_TOKEN=... python catalogo.py      # sincroniza con OneDrive
    python catalogo.py --solo-json listado.txt  # (pruebas) arma el JSON desde un listado de rutas
"""
import datetime as dt
import html
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import quote

MS_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"  # cliente público de Microsoft Graph CLI
CARPETA = os.environ.get("ONEDRIVE_CARPETA", "SUBLIMAR")
G = "https://graph.microsoft.com/v1.0/me/drive"
AQUI = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(AQUI, "catalogo.json")
MINIS = os.path.join(AQUI, "miniaturas")
ANCHO_MINI = 480

MESES = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}
TELAS = ["microperforado", "bistrech", "oxford", "raso", "saten", "satén", "poliester", "poliéster", "gamuza"]
UTILIDADES = re.compile(
    r"^(blanco|iniciador|prueba|muestra|pantonera|color\b|tetris|azul|amarillo|verde|rojo|negro|gris)"
    r"|^[0-9a-f]{8}-[0-9a-f]{4}-", re.I)


# ---------- análisis del nombre de archivo ----------

def sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _num(s):
    return float(s.replace(",", "."))


def analizar(ruta):
    """'/08:2026/13-08/1) OT 1474 6 Pass Fiscalía 1,80 x 1,20 Bistrech.pdf' -> dict con los campos."""
    partes = [html.unescape(p) for p in ruta.strip("/").split("/")]
    s = re.sub(r"\.pdf$", "", partes[-1], flags=re.I).strip()
    # numeración de impresión al inicio: "1) ", "12.12) ", "XXX) ", "3-OT"
    s = re.sub(r"^\s*(x+|\d+(\.\d+)?)\s*[)\-]\s*", "", s, flags=re.I)

    ot = None
    m = re.search(r"\bOT\s*(\d+(?:[-v]\w+)?|stock|x+)\b", s, re.I)
    if m:
        ot = m.group(1)
        s = s[:m.start()] + " " + s[m.end():]
    if ot and ot.lower().startswith("x"):
        ot = None

    pases = None
    m = re.search(r"\b(\d+)\s*pass\b", s, re.I)
    if m:
        pases = int(m.group(1))
        s = s[:m.start()] + " " + s[m.end():]

    medida = None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)", s)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        medida = "%s x %s" % (("%.2f" % a).replace(".", ","), ("%.2f" % b).replace(".", ","))
        s = s[:m.start()] + " " + s[m.end():]

    tela = None
    for t in TELAS:
        m = re.search(r"\b%s\b" % t, s, re.I)
        if m:
            tela = sin_tildes(t).capitalize()
            s = s[:m.start()] + " " + s[m.end():]
            break

    nombre = re.sub(r"\s+", " ", s).strip(" -_.")
    nombre = re.sub(r"\s*-\s*$", "", nombre) or partes[-1]
    if len(nombre) <= 2 and len(partes) > 1 and not re.match(r"[\d:\-_. ]+$", partes[-2]):
        nombre = partes[-2].capitalize() + " " + nombre  # 'BANDERA SEÑALES/J.pdf' -> 'Bandera señales J'
    carpeta = "/".join(partes[:-1])
    utilidad = bool(UTILIDADES.search(nombre)) or partes[0].lower() in ("pantoneras", "colores")

    return {
        "nombre": nombre,
        "grupo": re.sub(r"[^a-z0-9]+", " ", sin_tildes(nombre).lower()).strip(),
        "ot": ot, "pases": pases, "medida": medida, "tela": tela,
        "fecha": fecha_de(partes[:-1]),
        "carpeta": carpeta,
        "archivo": partes[-1],
        "utilidad": utilidad,
    }


def fecha_de(carpetas):
    """Deduce la fecha de impresión desde carpetas tipo '08:2026/13-08' o 'Abril 2026/10:04'."""
    if not carpetas:
        return None
    top = sin_tildes(carpetas[0].lower())
    anio = re.search(r"(20\d\d)", top)
    if not anio:
        return None
    anio = int(anio.group(1))
    mes = None
    m = re.match(r"(\d{1,2})\s*[:\-_.]", top)
    if m:
        mes = int(m.group(1))
    for nombre, n in MESES.items():
        if nombre in top:
            mes = n
    dia = None
    if len(carpetas) > 1:
        m = re.match(r"(\d{1,2})\s*[:\-]\s*(\d{1,2})", carpetas[1])
        if m:
            dia, mes2 = int(m.group(1)), int(m.group(2))
            if 1 <= mes2 <= 12:
                mes = mes2
    if not mes:
        return None
    try:
        return dt.date(anio, mes, dia or 1).isoformat()
    except ValueError:
        return dt.date(anio, mes, 1).isoformat()


# ---------- OneDrive ----------

def token():
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": MS_CLIENT_ID,
        "refresh_token": os.environ["MS_REFRESH_TOKEN"].strip(),
        "scope": "Files.Read offline_access User.Read",
    }).encode()
    req = urllib.request.Request("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", data=data)
    return json.load(urllib.request.urlopen(req, timeout=40))["access_token"]


def listar_onedrive(at):
    def g(url):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + at})
        return json.load(urllib.request.urlopen(req, timeout=60))

    root = g(G + "/root:/" + quote(CARPETA))
    stack, items = [(root["id"], "")], []
    campos = "id,name,file,folder,size,cTag,webUrl,lastModifiedDateTime"
    while stack:
        fid, ruta = stack.pop()
        url = G + "/items/%s/children?$select=%s&$top=200" % (fid, campos)
        while url:
            d = g(url)
            for it in d.get("value", []):
                p = ruta + "/" + it["name"]
                if it.get("folder"):
                    stack.append((it["id"], p))
                elif it["name"].lower().endswith(".pdf"):
                    it["ruta"] = p
                    items.append(it)
            url = d.get("@odata.nextLink")
    return items


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def descargar(at, item_id):
    # /content responde 302 a una URL de descarga ya firmada; hay que seguirla SIN el token
    # (si se reenvía el Authorization, el servidor de descarga responde 401).
    req = urllib.request.Request(G + "/items/%s/content" % item_id, headers={"Authorization": "Bearer " + at})
    try:
        return urllib.request.build_opener(_SinRedireccion).open(req, timeout=120).read()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        return urllib.request.urlopen(e.headers["Location"], timeout=300).read()


def miniatura(pdf_bytes, destino):
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pag = doc[0]
    zoom = ANCHO_MINI / max(pag.rect.width, 1)
    pix = pag.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    from PIL import Image
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    img.save(destino, "WEBP", quality=78, method=6)
    return pag.rect.width / 72 * 2.54, pag.rect.height / 72 * 2.54  # tamaño de página en cm


# ---------- catálogo ----------

def cargar_previo():
    try:
        with open(JSON_PATH, encoding="utf-8") as f:
            return {a["id"]: a for a in json.load(f)["archivos"]}
    except (OSError, ValueError, KeyError):
        return {}


def guardar(archivos):
    archivos.sort(key=lambda a: (a.get("fecha") or "", a["archivo"]), reverse=True)
    out = {"actualizado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "carpeta": CARPETA, "archivos": archivos}
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print("catalogo.json: %d PDF (%d banderas, %d utilidades)" % (
        len(archivos), sum(not a["utilidad"] for a in archivos), sum(a["utilidad"] for a in archivos)))


def sincronizar():
    at = token()
    items = listar_onedrive(at)
    print("OneDrive: %d PDF en %s" % (len(items), CARPETA))
    previo = cargar_previo()
    os.makedirs(MINIS, exist_ok=True)
    archivos, pendientes = [], []
    for it in items:
        a = analizar(it["ruta"])
        a.update(id=it["id"], tamano=it.get("size", 0), url=it.get("webUrl"),
                 modificado=it.get("lastModifiedDateTime"), ctag=it.get("cTag"), mini=None, pagina_cm=None)
        ant = previo.get(it["id"])
        if ant and ant.get("ctag") == a["ctag"] and os.path.exists(os.path.join(MINIS, it["id"] + ".webp")):
            a["mini"], a["pagina_cm"] = ant.get("mini"), ant.get("pagina_cm")
        else:
            pendientes.append((a, it["ruta"]))
        archivos.append(a)

    def hacer(par):
        a, ruta = par
        try:
            w, h = miniatura(descargar(at, a["id"]), os.path.join(MINIS, a["id"] + ".webp"))
            a["mini"], a["pagina_cm"] = "miniaturas/%s.webp" % a["id"], [round(w, 1), round(h, 1)]
            return True
        except Exception as e:  # PDF dañado, tiempo de espera, etc.: queda sin miniatura
            print("  sin miniatura:", ruta, "-", e, file=sys.stderr)
            return False

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(hacer, pendientes))
    nuevos, errores = sum(res), len(res) - sum(res)
    # borra miniaturas de archivos que ya no están en OneDrive
    vigentes = {a["id"] + ".webp" for a in archivos}
    for f in os.listdir(MINIS):
        if f.endswith(".webp") and f not in vigentes:
            os.remove(os.path.join(MINIS, f))
    print("Miniaturas nuevas: %d, con error: %d" % (nuevos, errores))
    guardar(archivos)


def solo_json(listado):
    """Arma catalogo.json sin OneDrive, desde un archivo con una ruta por línea (para probar la web)."""
    archivos = []
    with open(listado, encoding="utf-8") as f:
        for i, ruta in enumerate(l.strip() for l in f):
            if ruta:
                a = analizar(ruta)
                a.update(id="demo%04d" % i, tamano=0, url=None, modificado=None, ctag=None,
                         mini=None, pagina_cm=None)
                archivos.append(a)
    guardar(archivos)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--solo-json":
        solo_json(sys.argv[2])
    else:
        sincronizar()
