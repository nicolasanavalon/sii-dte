"""Casilla de Gmail por IMAP (leer, leídos/no leídos) y SMTP (enviar), con una "contraseña de aplicación".

Secretos: GMAIL_CORREO (la dirección) y GMAIL_CLAVE (contraseña de aplicación de 16 letras, se crea en
https://myaccount.google.com/apppasswords con la verificación en 2 pasos activada). No vence.
"""
import email
import email.policy
import imaplib
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parsedate_to_datetime

IMAP, SMTP = "imap.gmail.com", "smtp.gmail.com"
imaplib._MAXLINE = 200_000_000     # la lista de todos los correos de una casilla grande supera el límite de 1 MB


class GmailError(RuntimeError):
    pass


def _dec(valor) -> str:
    try:
        return str(email.header.make_header(email.header.decode_header(valor or "")))
    except Exception:
        return str(valor or "")


class Gmail:
    def __init__(self, correo, clave):
        self.correo = correo.strip()
        self.clave = re.sub(r"\s+", "", clave or "")
        self._c = None
        self._enviados = None

    # ------------------------------------------------------------- conexión
    def conexion(self):
        if self._c is not None:
            try:
                self._c.noop()
                return self._c
            except Exception:
                self._c = None
        try:
            c = imaplib.IMAP4_SSL(IMAP, 993, ssl_context=ssl.create_default_context(), timeout=60)
            c.login(self.correo, self.clave)
        except imaplib.IMAP4.error as e:
            raise GmailError(f"Gmail no aceptó la contraseña de aplicación ({str(e)[:120]}).") from None
        self._c = c
        return c

    def carpeta_todos(self):
        """"Todos" de Gmail (\\All): todo el correo, archivado incluido."""
        typ, lineas = self.conexion().list()
        for l in lineas or []:
            l = l.decode("utf-8", "replace")
            if "\\All" in l:
                return l.rsplit(' "/" ', 1)[-1].strip().strip('"')
        return "INBOX"

    def uids_todos(self, carpeta):
        self._sel(carpeta)
        typ, datos = self.conexion().uid("search", None, "ALL")
        return [u.decode() for u in (datos[0] or b"").split()]

    def cabeceras(self, carpeta, uids):
        """[(gm_msgid, gm_thrid, enviado, fecha_ms, mensaje_solo_cabeceras)] para el índice del historial."""
        if not uids:
            return []
        self._sel(carpeta)
        typ, datos = self.conexion().uid("fetch", ",".join(uids),
                                         "(X-GM-MSGID X-GM-THRID X-GM-LABELS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE)])")
        out = []
        for d in datos or []:
            if not isinstance(d, tuple):
                continue
            meta = d[0].decode("utf-8", "replace")
            m_id, m_th = re.search(r"X-GM-MSGID (\d+)", meta), re.search(r"X-GM-THRID (\d+)", meta)
            m_fe = re.search(r'INTERNALDATE "([^"]+)"', meta)
            if not m_id:
                continue
            try:
                ms = int(datetime.strptime(m_fe.group(1).strip(), "%d-%b-%Y %H:%M:%S %z").timestamp() * 1000)
            except Exception:
                ms = 0
            out.append((m_id.group(1), m_th.group(1) if m_th else "", "\\Sent" in meta, ms,
                        email.message_from_bytes(d[1], policy=email.policy.default)))
        return out

    def carpeta_enviados(self):
        if self._enviados is None:
            typ, lineas = self.conexion().list()
            self._enviados = ""
            for l in lineas or []:
                l = l.decode("utf-8", "replace")
                if "\\Sent" in l:
                    self._enviados = l.rsplit(' "/" ', 1)[-1].strip().strip('"')
                    break
        return self._enviados

    def _sel(self, carpeta):
        typ, _ = self.conexion().select(f'"{carpeta}"', readonly=True)
        if typ != "OK":
            raise GmailError(f"No pude abrir la carpeta {carpeta}.")

    # ------------------------------------------------------------- lectura
    def listar(self, carpeta, desde_ms):
        """[(uid, gm_msgid, gm_thrid, fecha_ms, no_leido)] de la carpeta desde la fecha dada."""
        self._sel(carpeta)
        c = self.conexion()
        desde = (datetime.fromtimestamp(desde_ms / 1000, timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")
        typ, datos = c.uid("search", None, f"SINCE {desde}")
        uids = (datos[0] or b"").split()
        out = []
        for i in range(0, len(uids), 400):
            grupo = b",".join(uids[i:i + 400]).decode()
            typ, filas = c.uid("fetch", grupo, "(X-GM-MSGID X-GM-THRID FLAGS INTERNALDATE)")
            for f in filas or []:
                linea = f.decode("utf-8", "replace") if isinstance(f, bytes) else (f[0].decode("utf-8", "replace") if isinstance(f, tuple) else "")
                m_uid, m_id = re.search(r"UID (\d+)", linea), re.search(r"X-GM-MSGID (\d+)", linea)
                if not (m_uid and m_id):
                    continue
                m_th, m_fe = re.search(r"X-GM-THRID (\d+)", linea), re.search(r'INTERNALDATE "([^"]+)"', linea)
                try:
                    ms = int(datetime.strptime(m_fe.group(1).strip(), "%d-%b-%Y %H:%M:%S %z").timestamp() * 1000)
                except Exception:
                    ms = 0
                out.append((m_uid.group(1), m_id.group(1), m_th.group(1) if m_th else "", ms, "\\Seen" not in linea))
        return out

    def promociones(self, carpeta, desde_ms):
        """UIDs que Gmail clasifica como Promociones o Social (equivalen a los boletines de Zoho)."""
        try:
            self._sel(carpeta)
            desde = datetime.fromtimestamp(desde_ms / 1000, timezone.utc).strftime("%Y/%m/%d")
            typ, datos = self.conexion().uid("search", None, "X-GM-RAW", f'"after:{desde} (category:promotions OR category:social)"')
            return {u.decode() for u in (datos[0] or b"").split()}
        except Exception:
            return set()

    def mensaje(self, carpeta, uid):
        self._sel(carpeta)
        typ, datos = self.conexion().uid("fetch", uid, "(BODY.PEEK[])")   # PEEK: leerlo aquí no lo marca como leído
        for d in datos or []:
            if isinstance(d, tuple):
                return email.message_from_bytes(d[1], policy=email.policy.default)
        raise GmailError(f"No pude leer el correo {uid}.")

    def mensaje_por_id(self, gm_id):
        for carpeta in ["INBOX", self.carpeta_enviados(), self.carpeta_todos()]:
            if not carpeta:
                continue
            self._sel(carpeta)
            typ, datos = self.conexion().uid("search", None, "X-GM-MSGID", str(gm_id))
            for uid in (datos[0] or b"").split():
                return self.mensaje(carpeta, uid.decode())
        raise GmailError("No encontré el correo original en Gmail.")

    def no_leidos(self, carpeta, desde_ms):
        return {x[1] for x in self.listar(carpeta, desde_ms) if x[4]}

    # ------------------------------------------------------------- leídos / no leídos
    def marcar(self, gm_ids, leidos=True):
        c, hechos = self.conexion(), 0
        for carpeta in ["INBOX", self.carpeta_enviados()]:
            if not carpeta:
                continue
            c.select(f'"{carpeta}"')
            for gid in gm_ids:
                typ, datos = c.uid("search", None, "X-GM-MSGID", str(gid))
                for uid in (datos[0] or b"").split():
                    c.uid("store", uid, "+FLAGS" if leidos else "-FLAGS", "(\\Seen)")
                    hechos += 1
        return hechos

    # ------------------------------------------------------------- envío
    def enviar(self, para, cc, asunto, texto, adjuntos=(), responde_a=None, referencias=None, html=None):
        """adjuntos: [(nombre, bytes)]. responde_a: Message-ID del correo que se responde (queda en el mismo hilo)."""
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = self.correo, ", ".join(para), asunto
        if cc:
            m["Cc"] = ", ".join(cc)
        m["Message-ID"] = make_msgid(domain=self.correo.split("@")[-1])
        if responde_a:
            m["In-Reply-To"] = responde_a
            m["References"] = (referencias + " " if referencias else "") + responde_a
        m.set_content(texto)
        if html:
            m.add_alternative(html, subtype="html")   # con el historial citado, como en Outlook o Gmail
        for nombre, datos in adjuntos:
            m.add_attachment(datos, maintype="application", subtype="octet-stream", filename=nombre)
        try:
            with smtplib.SMTP_SSL(SMTP, 465, context=ssl.create_default_context(), timeout=90) as s:
                s.login(self.correo, self.clave)
                s.send_message(m)
        except smtplib.SMTPAuthenticationError:
            raise GmailError("Gmail no aceptó la contraseña de aplicación para enviar.") from None


def partes(msg):
    """(texto, html, [(nombre, bytes)], cabeceras útiles) de un correo."""
    texto, html, adjuntos = "", "", []
    for p in msg.walk():
        if p.is_multipart():
            continue
        disp = (p.get_content_disposition() or "").lower()
        nombre = p.get_filename()
        tipo = p.get_content_type()
        if nombre or disp == "attachment":
            try:
                adjuntos.append((_dec(nombre) or "archivo", p.get_payload(decode=True) or b""))
            except Exception:
                pass
        elif tipo == "text/plain" and not texto:
            try:
                texto = p.get_content()
            except Exception:
                texto = (p.get_payload(decode=True) or b"").decode("utf-8", "replace")
        elif tipo == "text/html" and not html:
            try:
                html = p.get_content()
            except Exception:
                html = (p.get_payload(decode=True) or b"").decode("utf-8", "replace")
    return texto, html, adjuntos


def direcciones(valor):
    return [(_dec(n), a.strip()) for n, a in getaddresses([str(valor or "")]) if a and "@" in a]


def fecha_ms(msg, defecto):
    try:
        return int(parsedate_to_datetime(str(msg["Date"])).timestamp() * 1000)
    except Exception:
        return defecto or int(time.time() * 1000)
