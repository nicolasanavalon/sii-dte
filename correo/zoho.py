"""Cliente mínimo de la API de Zoho Mail (solo lectura).

Usa OAuth con un "refresh token" que se obtiene una sola vez (ver README, paso "Conectar Zoho").
Documentación: https://www.zoho.com/mail/help/api/
"""
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import getaddresses
from html.parser import HTMLParser

SCOPES = "ZohoMail.accounts.READ,ZohoMail.folders.READ,ZohoMail.messages.READ"
MAX_ADJUNTO = 12 * 1024 * 1024   # no se descargan adjuntos de más de 12 MB


class ZohoError(RuntimeError):
    pass


def _dc():
    # Centro de datos de tu cuenta: com (por defecto), eu, in, com.au, jp, ca, sa, com.cn
    return os.environ.get("ZOHO_DC", "com").strip() or "com"


def _post_token(params: dict) -> dict:
    url = f"https://accounts.zoho.{_dc()}/oauth/v2/token"
    req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)
    if "error" in data:
        raise ZohoError(f"Zoho rechazó la autorización: {data['error']}")
    return data


def canjear_codigo(client_id, client_secret, codigo) -> str:
    """Canjea el código de un solo uso (Self Client) por un refresh token permanente."""
    data = _post_token({
        "grant_type": "authorization_code", "client_id": client_id,
        "client_secret": client_secret, "code": codigo.strip(),
    })
    if "refresh_token" not in data:
        raise ZohoError("Zoho no entregó refresh_token (¿el código ya se usó o venció?).")
    return data["refresh_token"]


class Zoho:
    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self._token, self._vence = None, 0
        self.base = f"https://mail.zoho.{_dc()}/api"

    def _access(self):
        if not self._token or time.time() > self._vence - 60:
            data = _post_token({
                "grant_type": "refresh_token", "client_id": self.client_id,
                "client_secret": self.client_secret, "refresh_token": self.refresh_token,
            })
            self._token = data["access_token"]
            self._vence = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def post(self, ruta, cuerpo):
        req = urllib.request.Request(f"{self.base}{ruta}", data=json.dumps(cuerpo).encode(), method="POST", headers={
            "Authorization": f"Zoho-oauthtoken {self._access()}", "Accept": "application/json",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            cuerpo_err = e.read().decode("utf-8", "replace")[:400]
            if e.code in (401, 403):
                raise ZohoError(f"Zoho no dio permiso para enviar ({e.code}). Hay que volver a conectar la cuenta "
                                f"con el permiso ZohoMail.messages.CREATE. Detalle: {cuerpo_err}") from None
            raise ZohoError(f"Error {e.code} al enviar: {cuerpo_err}") from None

    def responder(self, account_id, message_id, desde, para, cc, asunto, texto):
        """Responde un correo (queda en la conversación y en Enviados de Zoho)."""
        cuerpo = {"fromAddress": desde, "toAddress": ",".join(para), "subject": asunto,
                  "content": texto, "action": "reply", "mailFormat": "plaintext"}
        if cc:
            cuerpo["ccAddress"] = ",".join(cc)
        return self.post(f"/accounts/{account_id}/messages/{message_id}", cuerpo)

    def get(self, ruta, _crudo=False, **params):
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}{ruta}" + (f"?{q}" if q else "")
        for intento in range(4):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Zoho-oauthtoken {self._access()}",
                "Accept": "application/octet-stream" if _crudo else "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return r.read(MAX_ADJUNTO + 1) if _crudo else json.load(r)
            except urllib.error.HTTPError as e:
                cuerpo = e.read().decode("utf-8", "replace")[:500]
                if e.code in (429, 500, 502, 503, 504) and intento < 3:
                    time.sleep(2 ** intento * 3)
                    continue
                if e.code in (401, 403):
                    raise ZohoError(
                        f"Zoho negó el acceso ({e.code}) a {ruta}. Si dice que tu plan no lo permite, "
                        f"la API no está disponible en el plan gratis y hay que pasar a Mail Lite. "
                        f"Detalle: {cuerpo}") from None
                raise ZohoError(f"Error {e.code} en {ruta}: {cuerpo}") from None
            except urllib.error.URLError:
                if intento < 3:
                    time.sleep(2 ** intento * 3)
                    continue
                raise

    # --- endpoints ---
    def cuentas(self):
        """Todas las casillas a las que da acceso este permiso: [(accountId, {direcciones}, dirección principal)]."""
        datos = self.get("/accounts")["data"]
        if not datos:
            raise ZohoError("La API no devolvió ninguna cuenta de correo.")
        out = []
        for c in datos:
            correos = {c.get("primaryEmailAddress", "")} | {
                (a.get("mailId") or "") for a in c.get("emailAddress", []) if isinstance(a, dict)}
            if c.get("mailboxAddress"):
                correos.add(c["mailboxAddress"])
            correos = {x.lower() for x in correos if x}
            principal = (c.get("primaryEmailAddress") or c.get("mailboxAddress") or (sorted(correos) or [""])[0]).lower()
            out.append((str(c["accountId"]), correos, principal))
        return out

    def cuenta(self):
        acc, correos, _ = self.cuentas()[0]
        return acc, correos

    def carpetas(self, account_id):
        return self.get(f"/accounts/{account_id}/folders")["data"]

    def listar(self, account_id, folder_id, start=1, limit=100, status=None):
        return self.get(f"/accounts/{account_id}/messages/view", folderId=folder_id, status=status,
                        start=start, limit=limit, sortorder="false", includeto="true").get("data", [])

    def no_leidos(self, account_id, folder_id, maximo=2000):
        """Ids de los correos que en Zoho siguen sin leer en esa carpeta."""
        ids, start = set(), 1
        while start <= maximo:
            pagina = self.listar(account_id, folder_id, start=start, limit=200, status="unread")
            ids |= {str(m["messageId"]) for m in pagina}
            if len(pagina) < 200:
                break
            start += 200
        return ids

    def contenido(self, account_id, folder_id, message_id):
        """HTML del correo INCLUYENDO la cadena citada (historial de respuestas/reenvíos)."""
        d = self.get(f"/accounts/{account_id}/folders/{folder_id}/messages/{message_id}/content",
                     includeBlockContent="true")
        return (d.get("data") or {}).get("content", "")

    def adjuntos(self, account_id, folder_id, message_id):
        """[{attachmentId, attachmentName, attachmentSize}] de un correo."""
        d = self.get(f"/accounts/{account_id}/folders/{folder_id}/messages/{message_id}/attachmentinfo")
        data = d.get("data") or {}
        lista = data.get("attachments") if isinstance(data, dict) else data
        return [a for a in (lista or []) if isinstance(a, dict) and a.get("attachmentId")]

    def descargar_adjunto(self, account_id, folder_id, message_id, attachment_id):
        return self.get(f"/accounts/{account_id}/folders/{folder_id}/messages/{message_id}/attachments/{attachment_id}",
                        _crudo=True)


# --- utilidades de texto ---

class _Texto(HTMLParser):
    BLOQUES = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes, self._saltar = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self._saltar += 1
        elif tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self._saltar = max(0, self._saltar - 1)
        elif tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._saltar:
            self.partes.append(data)


def html_a_texto(contenido: str) -> str:
    p = _Texto()
    try:
        p.feed(contenido or "")
        texto = "".join(p.partes)
    except Exception:
        texto = re.sub(r"<[^>]+>", " ", contenido or "")
    texto = texto.replace("\xa0", " ")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n\s*\n+", "\n\n", texto)
    return texto.strip()


MAX_HTML = 250_000


def limpiar_html(contenido: str):
    """HTML del correo listo para mostrarse aislado (sin scripts, formularios ni eventos). None si es muy grande."""
    if not contenido:
        return None
    h = re.sub(r"(?is)<(script|iframe|object|embed|noscript)\b[^>]*>.*?</\1\s*>", "", contenido)
    h = re.sub(r"(?is)</?(script|iframe|object|embed|form|meta|base|link|noscript)\b[^>]*>", "", h)
    h = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", h)
    h = re.sub(r"(?i)(href|src)\s*=\s*([\"']?)\s*javascript:[^\"'>\s]*\2", r"\1=\2#\2", h)
    return h if len(h) <= MAX_HTML else None


def direcciones(campo) -> list:
    """'"Juan" <juan@x.cl>, ana@y.cl' -> [('Juan','juan@x.cl'), ('','ana@y.cl')]"""
    if not campo:
        return []
    if isinstance(campo, list):
        campo = ",".join(str(x) for x in campo)
    campo = html.unescape(str(campo))
    return [(n.strip().strip('"'), a.strip().lower()) for n, a in getaddresses([campo]) if "@" in a]
