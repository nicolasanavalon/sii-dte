"""Análisis de correos con Claude: resumen por correo + memoria acumulada por contacto."""
import json
import os

import anthropic

MODELO = os.environ.get("CORREO_MODELO", "claude-opus-5-5")
ESFUERZO = os.environ.get("CORREO_ESFUERZO", "medium")  # low | medium | high
MAX_TEXTO = 6000  # caracteres de cada correo que se envían a Claude

SISTEMA = """Eres el asistente personal de correo de una persona que tiene una empresa en Chile.
Tu trabajo: leer sus correos con UN contacto y mantener una ficha clara de esa relación,
para que la persona no olvide nada importante.

Recibirás la ficha actual del contacto (puede estar vacía) y correos nuevos, en orden cronológico.
Devuelve la ficha ACTUALIZADA (no solo lo nuevo: integra lo anterior con lo nuevo) y un análisis
breve de cada correo nuevo.

Criterios:
- Escribe en español de Chile, directo y concreto. Nada de relleno.
- "resumen" del contacto: 2 a 5 frases con la historia y el estado actual de la relación.
- "pendientes": cosas concretas que la persona debe hacer o responder. Quita las que ya se resolvieron.
- "datos_clave": datos que hay que tener muy claros: montos, precios, números de factura/OC,
  RUT, cuentas bancarias, direcciones, acuerdos, condiciones de pago, contraseñas NO (nunca copies
  contraseñas ni códigos de verificación). Máximo 12; conserva los vigentes de la ficha anterior.
- "plazos": fechas límite, reuniones, vencimientos. Fecha en formato AAAA-MM-DD si se puede deducir
  (usa la fecha de hoy como referencia para "el viernes", "fin de mes", etc.); si no, deja "".
  Elimina plazos que ya pasaron hace más de 7 días.
- "tipo": persona (alguien que escribe en persona), empresa (proveedor/cliente que escribe como
  empresa), notificacion (avisos automáticos: bancos, SII, plataformas), publicidad (marketing).
- Importancia de cada correo: alta = requiere acción pronto, dinero, plazos, problemas o algo que
  la persona realmente debe ver; media = información útil; baja = publicidad, avisos rutinarios.
- "requiere_respuesta": true solo si el correo espera una respuesta de la persona y aún no la hay
  (si en los correos enviados posteriores ya respondió, es false).
- "accion": qué debería hacer la persona, en una frase corta; "" si nada.
- Para publicidad y notificaciones rutinarias, sé muy breve."""

ESQUEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["contacto", "correos"],
    "properties": {
        "contacto": {
            "type": "object",
            "additionalProperties": False,
            "required": ["nombre", "tipo", "resumen", "pendientes", "datos_clave", "plazos"],
            "properties": {
                "nombre": {"type": "string"},
                "tipo": {"type": "string", "enum": ["persona", "empresa", "notificacion", "publicidad"]},
                "resumen": {"type": "string"},
                "pendientes": {"type": "array", "items": {"type": "string"}},
                "datos_clave": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["dato", "valor"],
                    "properties": {"dato": {"type": "string"}, "valor": {"type": "string"}}}},
                "plazos": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["fecha", "descripcion"],
                    "properties": {"fecha": {"type": "string"}, "descripcion": {"type": "string"}}}},
            },
        },
        "correos": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "resumen", "importancia", "requiere_respuesta", "accion"],
            "properties": {
                "id": {"type": "string"},
                "resumen": {"type": "string"},
                "importancia": {"type": "string", "enum": ["alta", "media", "baja"]},
                "requiere_respuesta": {"type": "boolean"},
                "accion": {"type": "string"},
            }}},
    },
}

_cliente = None


def _cli():
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic(max_retries=4)
    return _cliente


def analizar_contacto(ficha: dict, correos: list, hoy: str, fechas_txt) -> dict:
    """ficha: ficha actual del contacto ({} si es nuevo). correos: mensajes nuevos (dicts del estado)."""
    bloques = []
    for m in correos:
        texto = (m.get("texto") or m.get("vista_previa") or "")[:MAX_TEXTO]
        bloques.append(
            f'<correo id="{m["id"]}" fecha="{fechas_txt(m["fecha"])}" '
            f'direccion="{"ENVIADO por la persona" if m["enviado"] else "RECIBIDO"}">\n'
            f'De: {m["de_nombre"]} <{m["de"]}>\nPara: {", ".join(m["para"])}\n'
            f'Asunto: {m["asunto"]}\nAdjuntos: {"sí" if m.get("adjuntos") else "no"}\n\n{texto}\n</correo>')
    ficha_txt = json.dumps({k: ficha.get(k) for k in
                            ("nombre", "tipo", "resumen", "pendientes", "datos_clave", "plazos")},
                           ensure_ascii=False, indent=1) if ficha else "(contacto nuevo, sin ficha)"
    usuario = (f"Fecha de hoy: {hoy}\n\n<ficha_actual>\n{ficha_txt}\n</ficha_actual>\n\n"
               f"<correos_nuevos>\n" + "\n\n".join(bloques) + "\n</correos_nuevos>")

    resp = _cli().beta.messages.create(
        model=MODELO,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SISTEMA,
        output_config={"effort": ESFUERZO, "format": {"type": "json_schema", "schema": ESQUEMA}},
        messages=[{"role": "user", "content": usuario}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude no pudo analizar estos correos (rechazo).")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("La respuesta quedó cortada (max_tokens).")
    texto = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(texto)
