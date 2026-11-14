"""Resúmenes con IA gratuita, sin usar Claude: Gemini de Google (principal, si existe GEMINI_API_KEY)
y Groq (https://console.groq.com) como respaldo.

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
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]


class LimiteDiario(RuntimeError):
    pass


def _clave():
    return (os.environ.get("GROQ_API_KEY") or "").strip()


def disponible():
    return bool(_clave() or _clave_gemini())


def _pedir(ruta, cuerpo=None, timeout=120):
    req = urllib.request.Request(
        API + ruta, data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"Authorization": f"Bearer {_clave()}", "Content-Type": "application/json",
                 "User-Agent": "correo-resumenes/1.0"}, method="POST" if cuerpo is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


_modelos = None
_max_salida = {}
NO_CHAT = ("guard", "whisper", "tts", "playai", "orpheus", "distil", "compound", "embed", "vision-preview")
FUERTES = ("120b", "70b", "maverick", "kimi", "qwen3", "gpt-oss", "235b", "405b", "scout", "32b")

# ---- Gemini (Google AI Studio, plan gratis): IA principal si existe GEMINI_API_KEY ----
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
NO_GEMINI = ("embed", "tts", "image", "live", "audio", "native", "robotics", "computer", "learnlm", "aqa", "veo", "imagen")


def _clave_gemini():
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def es_gemini(modelo):
    return str(modelo).startswith("gemini")


def _orden_gemini(mid):
    """Más nuevo primero; dentro de una versión: flash, pro, flash-lite; las versiones de prueba al final."""
    import re
    v = re.search(r"gemini-(\d+(?:\.\d+)?)", mid)
    version = float(v.group(1)) if v else 0
    nivel = 2 if "lite" in mid else 0 if "flash" in mid else 1
    prueba = 1 if re.search(r"preview|exp|\d{2}-\d{2}", mid) else 0
    return (prueba, -version, nivel)


def _modelos_gemini():
    if not _clave_gemini():
        return []
    try:
        req = urllib.request.Request(f"{GEMINI_API}/models?pageSize=200",
                                     headers={"x-goog-api-key": _clave_gemini(), "User-Agent": "correo-resumenes/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            datos = json.load(r).get("models", [])
    except Exception as e:
        print(f"::warning::No pude listar los modelos de Gemini: {e}")
        return []
    out = []
    for m in datos:
        mid = str(m.get("name", "")).replace("models/", "")
        if (es_gemini(mid) and "generateContent" in (m.get("supportedGenerationMethods") or [])
                and not any(x in mid.lower() for x in NO_GEMINI) and "latest" not in mid):
            out.append(mid)
            _max_salida[mid] = int(m.get("outputTokenLimit") or 8192)
    return sorted(set(out), key=_orden_gemini)[:6]


def modelos():
    """Modelos de chat disponibles: primero Gemini (si hay clave), después Groq como respaldo."""
    global _modelos
    if _modelos is None:
        _modelos = _modelos_gemini() + (_modelos_groq() if _clave() else [])
        print("IA: modelos a usar (en orden):", ", ".join(_modelos) or "(ninguno)")
    return _modelos


def _modelos_groq():
    """Modelos de chat de Groq, ordenados: preferidos, luego los más capaces. Se descartan los que
    solo permiten respuestas muy cortas (menos de 1.000 tokens)."""
    if True:
        datos = []
        try:
            datos = _pedir("/models").get("data", [])
        except Exception as e:
            print(f"::warning::No pude listar los modelos de Groq: {e}")
        utiles = {}
        for m in datos:
            mid = str(m.get("id", ""))
            if not mid or m.get("active") is False or any(x in mid.lower() for x in NO_CHAT):
                continue
            if 0 < int(m.get("context_window") or 0) < 16000:
                continue                                # contexto muy chico para una conversación completa
            salida = int(m.get("max_completion_tokens") or m.get("context_window") or 0)
            if salida and salida < 1000:
                continue
            utiles[mid] = m
            _max_salida[mid] = salida
        primero = (os.environ.get("GROQ_MODELO") or "").strip()
        orden = [x for x in ([primero] if primero else []) + PREFERIDOS if x in utiles]
        resto = sorted((x for x in utiles if x not in orden),
                       key=lambda x: (-sum(f in x.lower() for f in FUERTES), -int(utiles[x].get("context_window") or 0)))
        return (orden + resto)[:6] or ([primero] if primero else PREFERIDOS[:2])


def max_salida(modelo, pedido):
    tope = _max_salida.get(modelo) or pedido
    return max(256, min(pedido, tope))


def _gemini_chat(modelo, sistema, usuario, max_tokens, json_mode):
    cuerpo = {"systemInstruction": {"parts": [{"text": sistema}]},
              "contents": [{"role": "user", "parts": [{"text": usuario}]}],
              "generationConfig": {"temperature": 0.2, "maxOutputTokens": max(4096, max_tokens * 2)}}
    if json_mode:
        cuerpo["generationConfig"]["responseMimeType"] = "application/json"
    req = urllib.request.Request(f"{GEMINI_API}/models/{modelo}:generateContent", data=json.dumps(cuerpo).encode(),
                                 headers={"x-goog-api-key": _clave_gemini(), "Content-Type": "application/json",
                                          "User-Agent": "correo-resumenes/1.0"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.load(r)
    partes = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
    if not texto.strip():
        raise urllib.error.HTTPError("", 400, "respuesta vacía", {}, None)
    return texto


_agotados = {}       # modelo -> hasta cuándo (time.time()) no se vuelve a intentar
MAX_ESPERA = 600     # segundos máximos de espera ante un límite por minuto
TIMEOUT = 180        # segundos máximos por consulta (el vigilante lo baja para no atrasarse)
# Modelo reservado para lo que pides TÚ en la página (Preguntar, Sugerir, Mejorar): los resúmenes automáticos
# no lo usan, así nunca se gastan su cuota gratis del día.
RESERVADOS = {x.strip() for x in (os.environ.get("GROQ_RESERVADO") or "openai/gpt-oss-120b").split(",") if x.strip()}


def reservados():
    """Para la página: Gemini flash/pro (no los "lite") y el mejor de Groq."""
    return [m for m in modelos() if (es_gemini(m) and "lite" not in m) or m in RESERVADOS]


def modelos_fondo():
    """Para los resúmenes automáticos: los modelos que NO están reservados para la página."""
    lista = modelos()
    res = set(reservados())
    fondo = [m for m in lista if m not in res]
    return fondo or lista


def _espera_de(txt, cabecera):
    """Segundos hasta que se libera el límite (Groq lo dice en el texto: 'try again in 12m10.5s')."""
    import re
    m = re.search(r"(?:try again|retry) in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", txt)
    if m and any(m.groups()):
        h, mi, se = (float(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + se
    return float(cabecera or 20)


def _agotado(modelo):
    return _agotados.get(modelo, 0) > time.time()


def chat(sistema, usuario, max_tokens=3000, json_mode=True, reservado=False):
    """Devuelve (texto, modelo). Reintenta ante límites por minuto; cambia de modelo ante límites diarios.
    reservado=True permite usar también el modelo reservado (solo para tareas cortas y poco frecuentes)."""
    ultimo = None
    lista = modelos() if reservado else modelos_fondo()
    for modelo in lista:
        if _agotado(modelo):
            continue
        cuerpo = {"model": modelo, "temperature": 0.2, "max_tokens": max_salida(modelo, max_tokens),
                  "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]}
        if json_mode:
            cuerpo["response_format"] = {"type": "json_object"}
        if "gpt-oss" in modelo:
            cuerpo["reasoning_effort"] = "low"     # menos razonamiento = respuestas más rápidas y livianas
        for intento in range(5):
            try:
                if es_gemini(modelo):
                    return _gemini_chat(modelo, sistema, usuario, max_tokens, json_mode), modelo
                r = _pedir("/chat/completions", cuerpo, timeout=TIMEOUT)
                return r["choices"][0]["message"]["content"], modelo
            except urllib.error.HTTPError as e:
                txt = (e.read().decode("utf-8", "replace") if e.fp else str(e.msg))[:1500]
                ultimo = f"{e.code}: {txt}"
                if e.code == 429:
                    espera = _espera_de(txt, e.headers.get("retry-after"))
                    if espera > 90 or any(x in txt.lower() for x in ("per day", "(rpd)", "(tpd)", "perday")):
                        diario = any(x in txt.lower() for x in ("per day", "(rpd)", "(tpd)", "perday"))
                        _agotados[modelo] = time.time() + max(espera, 1800 if diario else 60)   # no insistir hasta que se libere
                        break
                    if espera > MAX_ESPERA:
                        break                       # (vigilante) no esperar: probar otro modelo o la próxima vuelta
                    time.sleep(espera + 1)
                    continue
                if e.code in (400, 404, 413):
                    break                           # este modelo no sirve para esta petición: probar el siguiente
                if e.code >= 500 and intento < 4:
                    time.sleep(5 * (intento + 1))
                    continue
                break
            except (urllib.error.URLError, OSError, ValueError) as e:   # sin conexión, demora excesiva, respuesta cortada
                ultimo = f"{modelo}: {e}"
                print(f"::warning::IA {modelo}: {str(e)[:150]}", flush=True)
                if intento >= 1:
                    break                           # probar el siguiente modelo
                time.sleep(3)
    if lista and all(_agotado(m) for m in lista):
        raise LimiteDiario("Se alcanzó el límite gratis del día en todos los modelos de Groq.")
    raise RuntimeError(f"Groq no respondió: {ultimo}")


def json_de(texto):
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`").split("\n", 1)[-1]
    i, j = texto.find("{"), texto.rfind("}")
    return json.loads(texto[i:j + 1])
