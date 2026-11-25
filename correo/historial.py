"""Índice liviano de TODO el historial (todas las casillas y carpetas), para buscar correos antiguos.

Por cada correo guarda solo remitente, destinatarios, asunto, fecha, carpeta y un extracto (no el texto completo
ni los adjuntos): así cabe el historial entero. Se guarda cifrado en la rama correo-datos (historial.json) y la
página lo descarga solo al buscar. Un correo antiguo se "trae completo" a pedido desde la página.
"""
import json
import os
import time
from pathlib import Path

import cripto

HIST = {}            # id -> entrada
MOVIL = 40000        # correos (los más recientes) del índice liviano para el celular
_cambio = False
_ultimo_guardado = 0.0


def _archivo():
    d = (os.environ.get("CORREO_ARCH_DIR") or os.environ.get("CORREO_DATOS_DIR") or "").strip()
    return Path(d) / "historial.json" if d else None


def cargar(clave):
    global HIST
    a = _archivo()
    if a and a.exists():
        try:
            HIST = {e["i"]: e for e in cripto.descifrar(json.loads(a.read_text(encoding="utf-8")), clave)}
            print(f"Historial: {len(HIST)} correos en el índice.")
        except Exception as e:
            print(f"::warning::No pude leer el historial: {str(e)[:200]}")


def agregar(e):
    global _cambio
    if HIST.get(e["i"]) != e:
        HIST[e["i"]] = e
        _cambio = True


def guardar(clave, cada=300):
    """Escribe historial.json si hubo cambios (como mucho cada `cada` segundos: el archivo es grande)."""
    global _cambio, _ultimo_guardado
    a = _archivo()
    movil = a.with_name("historial_movil.json") if a else None
    if not (a and (_cambio or not movil.exists())) or time.time() - _ultimo_guardado < cada:
        return False
    lista = sorted(HIST.values(), key=lambda e: -e.get("t", 0))
    a.write_text(json.dumps(cripto.cifrar(lista, clave)), encoding="utf-8")
    # Versión para el CELULAR: el índice completo (cientos de miles de correos) no cabe en su memoria y cerraba
    # la página al buscar. Aquí van los más recientes, solo con lo necesario para buscar y mostrar la lista.
    corto = lambda x, n: str(x or "")[:n]
    movil.write_text(json.dumps(cripto.cifrar([
        {"i": e["i"], "a": e.get("a"), "c": e.get("c"), "f": e.get("f"), "fn": corto(e.get("fn"), 40), "e": e.get("e"),
         "t": e.get("t"), "de": corto(e.get("de"), 120), "dn": corto(e.get("dn"), 60), "pa": corto(e.get("pa"), 120),
         "s": corto(e.get("s"), 140), "j": e.get("j")} for e in lista[:MOVIL]], clave)), encoding="utf-8")
    _cambio, _ultimo_guardado = False, time.time()
    print(f"Historial guardado: {len(lista)} correos.")
    return True
