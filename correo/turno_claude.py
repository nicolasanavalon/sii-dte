"""Herramienta de la sesión programada de Claude (ver correo/TURNO_CLAUDE.md).

  python correo/turno_claude.py claves DIR            -> crea llave efímera en DIR, imprime la pública
  python correo/turno_claude.py abrir PENDIENTES DIR   -> descifra a DIR/pendientes.json
  python correo/turno_claude.py leer DIR               -> vista compacta para leer y resumir (stdout)
  python correo/turno_claude.py validar DIR            -> revisa que DIR/respuesta.json esté completo
  python correo/turno_claude.py db DIR                 -> arma DIR/db/*.json y DIR/db/lotes.json para ArtifactData
  python correo/turno_claude.py sellar DIR SALIDA      -> sella DIR/respuesta.json para GitHub Actions
"""
import base64
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cripto  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

MAX_DOC = 230_000          # bytes por documento de la base de datos (límite 256 KiB)
MAX_LOTE = 900_000         # bytes por lote de escritura (límite 1 MiB)


def _priv(d):
    return X25519PrivateKey.from_private_bytes(base64.b64decode((Path(d) / "llave.priv").read_text()))


def _json(d, nombre):
    return json.loads((Path(d) / nombre).read_text())


def claves(d):
    Path(d).mkdir(parents=True, exist_ok=True)
    k = X25519PrivateKey.generate()
    raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    p = Path(d) / "llave.priv"
    p.write_text(base64.b64encode(raw).decode())
    p.chmod(0o600)
    print(cripto.llave_publica_b64(k))


def abrir(pendientes, d):
    datos = cripto.abrir_sello(json.loads(Path(pendientes).read_text()), _priv(d))
    (Path(d) / "pendientes.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1))
    n = sum(len(h["mensajes"]) for h in datos["hilos"])
    nuevos = sum(len(c["ids_nuevos"]) for c in datos["contactos"])
    print(f"Turno {datos['turno']}: {len(datos['hilos'])} conversaciones ({n} correos, {nuevos} sin resumen), "
          f"{len(datos['contactos'])} fichas por actualizar; quedan {datos['quedan_despues']} conversaciones.")


def _limpio(t):
    t = re.sub(r"[​-‏﻿͏ ]{3,}", " ", t or "")
    t = re.sub(r"(?m)^\s*(Por tu seguridad|Nunca |•|Si no deseas|Este correo|Para asegurar).*$", "", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def leer(d):
    p = _json(d, "pendientes.json")
    print(f"HOY: {p['hoy']}")
    for c in p["contactos"]:
        f = c["ficha_actual"]
        print(f"\n##### FICHA {c['email']} | nuevos: {', '.join(c['ids_nuevos'])}\n"
              f"{json.dumps(f, ensure_ascii=False)[:900] if f else '(contacto nuevo)'}")
    for h in p["hilos"]:
        print(f"\n===== HILO {h['hilo']} | {h['asunto']} | contacto {h['contacto']}"
              f"\nresumen_actual: {h['resumen_actual'] or '(ninguno)'}")
        for m in h["mensajes"]:
            marca = "NUEVO" if m["nuevo"] else "ya resumido: " + ((m.get("analisis") or {}).get("resumen") or "")[:160]
            print(f"-- {m['id']} {m['fecha']} {m['direccion'][:3]} | {m['de']} -> {', '.join(m['para'])} | {m['asunto']} | {marca}")
            print(_limpio(m["texto"]))
            for a in m["archivos"]:
                print(f"   [ADJUNTO {a['nombre']}]\n   " + (_limpio(a["texto"])[:2000] or "(sin texto legible)").replace("\n", "\n   "))


def validar(d, salir=True):
    p, r = _json(d, "pendientes.json"), _json(d, "respuesta.json")
    errores = []
    esperados = {(c["email"], i) for c in p["contactos"] for i in c["ids_nuevos"]}
    hechos = {(x.get("email"), str(y.get("id"))) for x in r.get("resultados", []) for y in x.get("correos", [])}
    errores += [f"falta analizar el correo {i} de {e}" for e, i in sorted(esperados - hechos)]
    for x in r.get("resultados", []):
        for k in ("nombre", "tipo", "resumen", "pendientes", "datos_clave", "plazos"):
            if k not in (x.get("contacto") or {}):
                errores.append(f"{x.get('email')}: falta contacto.{k}")
    hilos_r = {h.get("hilo"): h for h in r.get("hilos", [])}
    for h in p["hilos"]:
        if not (hilos_r.get(h["hilo"]) or {}).get("resumen"):
            errores.append(f"falta el resumen de la conversación {h['hilo']} ({h['asunto']})")
    if errores:
        print("FALTA COMPLETAR:", *errores, sep="\n  ")
        if salir:
            sys.exit(1)
        return False
    print(f"OK: {len(hechos)} correos, {len(r.get('resultados', []))} fichas y {len(hilos_r)} conversaciones.")
    return True


def _recortar(doc):
    """Deja el documento bajo MAX_DOC recortando primero adjuntos y luego textos de correos antiguos."""
    tam = lambda: len(json.dumps(doc, ensure_ascii=False).encode())
    msgs = doc["mensajes"]
    for limite in (20000, 8000, 3000, 800, 0):
        if tam() <= MAX_DOC:
            return doc
        for m in msgs:
            for a in m["archivos"]:
                a["texto"] = a["texto"][:limite]
        for m in msgs[:-1]:
            m["texto"] = (m["texto"] or "")[:max(limite, 600)]
    msgs[-1]["texto"] = msgs[-1]["texto"][:MAX_DOC // 3]
    return doc


def db(d):
    """Arma un documento por conversación y uno por contacto, y los agrupa en lotes de escritura."""
    p, r = _json(d, "pendientes.json"), _json(d, "respuesta.json")
    out = Path(d) / "db"
    out.mkdir(exist_ok=True)
    analisis = {str(y["id"]): y for x in r.get("resultados", []) for y in x.get("correos", [])}
    fichas = dict(p["db_fichas"])
    for x in r.get("resultados", []):
        fichas[x["email"]] = {**fichas.get(x["email"], {}), **x["contacto"], "email": x["email"],
                              "actualizado": datetime.now().isoformat(timespec="minutes")}
    hilos_r = {h["hilo"]: h for h in r.get("hilos", [])}
    escritos = []
    for hid, msgs in p["db_hilos"].items():
        ms = []
        for m in msgs:
            a = analisis.get(m["id"])
            if a:
                a = {k: a.get(k) for k in ("resumen", "importancia", "requiere_respuesta", "accion")}
            ms.append({**m, "analisis": a or m.get("analisis")})
        h = hilos_r.get(hid) or p["db_hilos_previos"].get(hid) or {}
        ult = ms[-1]
        doc = _recortar({
            "hilo": hid, "asunto": ult["asunto"], "contacto": ult["contacto"],
            "nombre_contacto": (fichas.get(ult["contacto"]) or {}).get("nombre") or ult["contacto"],
            "ultima_fecha": ult["fecha"], "primera_fecha": ms[0]["fecha"], "n_mensajes": len(ms),
            "n_archivos": sum(len(m["archivos"]) for m in ms),
            "importancia": max((((m.get("analisis") or {}).get("importancia")) or "baja" for m in ms),
                               key=["baja", "media", "alta"].index),
            "por_responder": any((m.get("analisis") or {}).get("requiere_respuesta") and not m["enviado"] for m in ms),
            "resumen": h.get("resumen", ""), "puntos_clave": h.get("puntos_clave", []), "mensajes": ms,
        })
        (out / f"h_{hid}.json").write_text(json.dumps(doc, ensure_ascii=False))
        escritos.append(("hilos", hid, out / f"h_{hid}.json"))
    for email, f in fichas.items():
        if not f:
            continue
        cid = re.sub(r"[^A-Za-z0-9_\-.~:@+]", "_", email)[:180] or "desconocido"
        (out / f"c_{cid}.json").write_text(json.dumps({**f, "email": email}, ensure_ascii=False))
        escritos.append(("contactos", cid, out / f"c_{cid}.json"))
    # Solo los turnos escriben en la base y cada escritura sube la versión en 1: así se sabe cuál pinchar.
    versiones = p.get("db_versiones", {})
    nuevas = {}
    lotes, actual, tam = [], [], 0
    for col, did, ruta in escritos:
        t = ruta.stat().st_size
        if actual and (tam + t > MAX_LOTE or len(actual) >= 50):
            lotes.append(actual)
            actual, tam = [], 0
        clave = f"{col}/{did}"
        w = {"op": "set", "collection": col, "doc_id": did, "file_path": str(ruta)}
        if versiones.get(clave):
            w["if_version"] = versiones[clave]
        nuevas[clave] = versiones.get(clave, 0) + 1
        actual.append(w)
        tam += t
    if actual:
        lotes.append(actual)
    (out / "lotes.json").write_text(json.dumps(lotes, indent=1))
    r["versiones"] = nuevas
    r["exportados"] = list(p["db_hilos"])
    (Path(d) / "respuesta.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print(f"{len(escritos)} documentos en {len(lotes)} lotes -> {out / 'lotes.json'}")
    for i, lote in enumerate(lotes):
        print(f"  lote {i + 1}: {len(lote)} documentos")


def sellar(d, salida):
    validar(d)
    p, r = _json(d, "pendientes.json"), _json(d, "respuesta.json")
    r["turno"] = p["turno"]
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    Path(salida).write_text(json.dumps(cripto.sellar(r, p["clave_accion"])))
    print(f"Respuesta sellada en {salida} (exportados: {len(r.get('exportados', []))} conversaciones)")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["?"]
    {"claves": claves, "abrir": abrir, "leer": leer, "sellar": sellar, "validar": validar, "db": db}.get(
        cmd, lambda *a: sys.exit(__doc__))(*args)
