"""Resúmenes con la IA gratuita de Groq (https://console.groq.com), sin usar Claude.

Groq ofrece un plan gratis con límites por minuto y por día. Si se alcanza un límite, se prueba el
siguiente modelo de la lista y, si todos están agotados, los correos quedan para la próxima corrida.
Nunca hay cobro: el plan gratis solo responde "límite alcanzado".
"""
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.groq.com/openai/v1"
# Orden de preferencia (calidad en español primero). Se usan solo los que la cuenta tenga disponibles.
PREFERIDOS = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
    "llama-3.1-8b-instant",
]


class LimiteDiario(RuntimeError):
    pass


def _clave():
    return (os.environ.get("GROQ_API_KEY") or "").strip()


def disponible():
    return bool(_clave())


def _pedir(ruta, cuerpo=None, timeout=120):
    req = urllib.request.Request(
        API + ruta, data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"Authorization": f"Bearer {_clave()}", "Content-Type": "application/json",
                 "User-Agent": "correo-resumenes/1.0"}, method="POST" if cuerpo is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


_modelos = None


def modelos():
    """Modelos preferidos que la cuenta tiene disponibles (con override por GROQ_MODELO)."""
    global _modelos
    if _modelos is None:
        try:
            ids = {m["id"] for m in _pedir("/models").get("data", [])}
        except Exception:
            ids = set(PREFERIDOS)
        primero = (os.environ.get("GROQ_MODELO") or "").strip()
        lista = ([primero] if primero else []) + [m for m in PREFERIDOS if m != primero]
        _modelos = [m for m in lista if m in ids] or [m for m in ids if "llama" in m][:3]
    return _modelos


_agotados = set()


def chat(sistema, usuario, max_tokens=1600, json_mode=True):
    """Devuelve (texto, modelo). Reintenta ante límites por minuto; cambia de modelo ante límites diarios."""
    ultimo = None
    for modelo in modelos():
        if modelo in _agotados:
            continue
        cuerpo = {"model": modelo, "temperature": 0.2, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]}
        if json_mode:
            cuerpo["response_format"] = {"type": "json_object"}
        for intento in range(5):
            try:
                r = _pedir("/chat/completions", cuerpo)
                return r["choices"][0]["message"]["content"], modelo
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")[:600]
                ultimo = f"{e.code}: {txt}"
                if e.code == 429:
                    espera = float(e.headers.get("retry-after") or 20)
                    if espera > 90 or "per day" in txt.lower() or "(rpd)" in txt.lower() or "(tpd)" in txt.lower():
                        _agotados.add(modelo)       # límite diario: probar el siguiente modelo
                        break
                    time.sleep(espera + 1)
                    continue
                if e.code in (413, 400) and ("too large" in txt.lower() or "context" in txt.lower()):
                    break                           # muy largo para este modelo: probar otro
                if e.code in (404,):
                    break
                if e.code >= 500 and intento < 4:
                    time.sleep(5 * (intento + 1))
                    continue
                break
            except (urllib.error.URLError, TimeoutError) as e:
                ultimo = str(e)
                time.sleep(5 * (intento + 1))
    if modelos() and all(m in _agotados for m in modelos()):
        raise LimiteDiario("Se alcanzó el límite gratis del día en todos los modelos de Groq.")
    raise RuntimeError(f"Groq no respondió: {ultimo}")


def json_de(texto):
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`").split("\n", 1)[-1]
    i, j = texto.find("{"), texto.rfind("}")
    return json.loads(texto[i:j + 1])
