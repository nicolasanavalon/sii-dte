"""Saca el texto de los archivos adjuntos (PDF, Excel, Word, CSV, texto) para poder buscar en ellos."""
import csv
import io
import re

MAX_TEXTO = 30000   # caracteres por adjunto


def _limpiar(t):
    t = re.sub(r"[ \t]+", " ", t or "")
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t.strip()[:MAX_TEXTO]


def _pdf(b):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(b))
    partes = []
    for i, pag in enumerate(r.pages):
        partes.append(f"[pág. {i + 1}]\n" + (pag.extract_text() or ""))
        if sum(map(len, partes)) > MAX_TEXTO:
            break
    return "\n".join(partes)


def _filas(nombre_hoja, filas):
    out = [f"[hoja: {nombre_hoja}]"]
    for fila in filas:
        celdas = ["" if v is None else str(v).strip() for v in fila]
        while celdas and not celdas[-1]:
            celdas.pop()
        if any(celdas):
            out.append("\t".join(celdas))
        if sum(map(len, out)) > MAX_TEXTO:
            break
    return "\n".join(out)


def _xlsx(b):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    return "\n\n".join(_filas(ws.title, ws.iter_rows(values_only=True)) for ws in wb.worksheets)


def _xls(b):
    import xlrd
    wb = xlrd.open_workbook(file_contents=b)
    return "\n\n".join(_filas(sh.name, (sh.row_values(i) for i in range(sh.nrows))) for sh in wb.sheets())


def _docx(b):
    from docx import Document
    d = Document(io.BytesIO(b))
    partes = [p.text for p in d.paragraphs]
    for t in d.tables:
        for fila in t.rows:
            partes.append("\t".join(c.text.strip() for c in fila.cells))
    return "\n".join(partes)


def _texto(b):
    for cod in ("utf-8", "latin-1"):
        try:
            return b.decode(cod)
        except UnicodeDecodeError:
            pass
    return ""


def _csv(b):
    t = _texto(b)
    try:
        return "\n".join("\t".join(f) for f in csv.reader(io.StringIO(t), delimiter=";" if t.count(";") > t.count(",") else ","))
    except Exception:
        return t


def _ocr_imagen(img):
    """Texto de una imagen con Tesseract (español). '' si no está instalado."""
    try:
        import pytesseract
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img).convert("L")
        if max(img.size) > 2600:
            img.thumbnail((2600, 2600))
        return pytesseract.image_to_string(img, lang="spa+eng", timeout=90)
    except Exception:
        return ""


def _ocr(contenido: bytes):
    from PIL import Image
    return _ocr_imagen(Image.open(io.BytesIO(contenido)))


def _ocr_pdf(contenido: bytes, paginas=4):
    """PDF escaneado (sin texto): se dibujan las primeras páginas y se leen con OCR."""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(contenido)
        partes = []
        for i in range(min(len(doc), paginas)):
            img = doc[i].render(scale=2).to_pil()
            partes.append(f"[pág. {i + 1}, leída de la imagen]\n" + _ocr_imagen(img))
        return "\n".join(partes)
    except Exception:
        return ""


def texto_de(nombre, contenido: bytes) -> str:
    """Devuelve el texto del archivo, o '' si el tipo no se puede leer (imágenes, zip, etc.)."""
    ext = (nombre or "").lower().rsplit(".", 1)[-1] if "." in (nombre or "") else ""
    try:
        if ext == "pdf" or contenido[:5] == b"%PDF-":
            t = _pdf(contenido)
            if len(re.sub(r"\[pág\. \d+\]|\s", "", t)) < 40:      # PDF escaneado: leer la imagen
                t = _ocr_pdf(contenido) or t
        elif ext in ("jpg", "jpeg", "png", "webp", "tif", "tiff", "bmp", "gif"):
            t = _ocr(contenido)
            if t.strip():
                t = "[texto leído de la imagen]\n" + t
        elif ext in ("xlsx", "xlsm"):
            t = _xlsx(contenido)
        elif ext == "xls":
            t = _xls(contenido)
        elif ext == "docx":
            t = _docx(contenido)
        elif ext == "csv":
            t = _csv(contenido)
        elif ext in ("txt", "xml", "json", "htm", "html"):
            from zoho import html_a_texto
            t = _texto(contenido)
            t = html_a_texto(t) if ext in ("htm", "html") else t
        else:
            return ""
    except Exception as e:
        return f"(no se pudo leer el archivo: {type(e).__name__})"
    return _limpiar(t)
