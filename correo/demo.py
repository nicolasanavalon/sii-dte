"""Datos ficticios para ver cómo se ve la plataforma sin conectar Zoho ni Claude."""
import time


def estado_demo():
    ahora = int(time.time() * 1000)
    h = 3600_000
    hoy = time.strftime("%Y-%m-%d")
    en = lambda d: time.strftime("%Y-%m-%d", time.localtime(time.time() + d * 86400))

    def msg(i, horas, contacto, nombre, asunto, texto, enviado=False, imp="media", resp=False, accion="", resumen=""):
        return {"id": str(i), "hilo": "", "fecha": ahora - horas * h,
                "de": "yo@miempresa.cl" if enviado else contacto, "de_nombre": "Yo" if enviado else nombre,
                "para": [contacto] if enviado else ["yo@miempresa.cl"], "cc": [], "asunto": asunto,
                "vista_previa": texto[:120], "texto": texto, "adjuntos": i % 3 == 0, "enviado": enviado,
                "contacto": contacto,
                "analisis": {"resumen": resumen or texto[:140], "importancia": imp,
                             "requiere_respuesta": resp, "accion": accion}}

    mensajes = [
        msg(1, 2, "compras@distribuidoranorte.cl", "Distribuidora Norte", "Cotización 4581 - ajuste de precio",
            "Estimado, le informamos que a partir del 1 de diciembre el precio del producto A sube a $12.500 + IVA. "
            "Si confirma la orden antes del viernes mantenemos el precio actual de $11.200.",
            imp="alta", resp=True, accion="Confirmar la orden antes del viernes para mantener $11.200",
            resumen="Sube el precio del producto A a $12.500 desde el 1/12; si confirmas antes del viernes se mantiene $11.200."),
        msg(2, 30, "compras@distribuidoranorte.cl", "Distribuidora Norte", "Re: Cotización 4581",
            "Hola, ¿me pueden enviar la cotización actualizada por 200 unidades?", enviado=True,
            resumen="Pediste cotización por 200 unidades."),
        msg(3, 5, "maria.soto@clientesur.cl", "María Soto", "Factura 1203 pendiente",
            "Hola, aún no vemos pagada la factura 1203 por $845.000 (vencía el 5). ¿Nos pueden confirmar fecha de pago?",
            imp="alta", resp=True, accion="Responder con la fecha de pago de la factura 1203",
            resumen="Reclama la factura 1203 ($845.000) vencida; pide fecha de pago."),
        msg(4, 50, "contador@estudiocontable.cl", "Pedro Rojas (contador)", "Documentos para F29",
            "Necesito las boletas de honorarios y el libro de compras de octubre antes del día 18 para declarar el F29.",
            imp="alta", resp=False, accion="Enviar boletas y libro de compras de octubre antes del 18",
            resumen="Pide boletas de honorarios y libro de compras de octubre antes del 18 para el F29."),
        msg(5, 8, "noticias@tiendaonline.cl", "Tienda Online", "¡Cyber: 40% en todo!", "Ofertas imperdibles...",
            imp="baja", resumen="Publicidad de Cyber."),
        msg(6, 20, "notificaciones@banco.cl", "Banco", "Transferencia recibida", "Recibiste una transferencia de $320.000 de Comercial Sur.",
            imp="media", resumen="Transferencia recibida de $320.000 de Comercial Sur."),
    ]
    contactos = {
        "compras@distribuidoranorte.cl": {
            "email": "compras@distribuidoranorte.cl", "nombre": "Distribuidora Norte", "tipo": "empresa",
            "resumen": "Proveedor del producto A. Pediste cotización por 200 unidades; responden que el precio sube "
                       "desde diciembre, pero mantienen el actual si confirmas antes del viernes.",
            "pendientes": ["Confirmar orden por 200 unidades antes del viernes"],
            "datos_clave": [{"dato": "Cotización", "valor": "N° 4581"}, {"dato": "Precio actual", "valor": "$11.200 + IVA"},
                            {"dato": "Precio desde 1/12", "valor": "$12.500 + IVA"}],
            "plazos": [{"fecha": en(2), "descripcion": "Confirmar orden para mantener precio"}],
            "actualizado": hoy + "T09:00"},
        "maria.soto@clientesur.cl": {
            "email": "maria.soto@clientesur.cl", "nombre": "María Soto (Cliente Sur)", "tipo": "persona",
            "resumen": "Clienta. Reclama que la factura 1203 está impaga y vencida.",
            "pendientes": ["Responder fecha de pago de la factura 1203"],
            "datos_clave": [{"dato": "Factura", "valor": "N° 1203 — $845.000"}],
            "plazos": [{"fecha": en(-8), "descripcion": "Vencimiento factura 1203"}], "actualizado": hoy + "T09:00"},
        "contador@estudiocontable.cl": {
            "email": "contador@estudiocontable.cl", "nombre": "Pedro Rojas (contador)", "tipo": "persona",
            "resumen": "Tu contador. Prepara el F29 de octubre y necesita documentos.",
            "pendientes": ["Enviar boletas de honorarios de octubre", "Enviar libro de compras de octubre"],
            "datos_clave": [], "plazos": [{"fecha": en(5), "descripcion": "Entregar documentos para F29"}],
            "actualizado": hoy + "T09:00"},
        "noticias@tiendaonline.cl": {"email": "noticias@tiendaonline.cl", "nombre": "Tienda Online", "tipo": "publicidad",
                                     "resumen": "Publicidad.", "pendientes": [], "datos_clave": [], "plazos": []},
        "notificaciones@banco.cl": {"email": "notificaciones@banco.cl", "nombre": "Banco", "tipo": "notificacion",
                                    "resumen": "Avisos de movimientos bancarios.", "pendientes": [],
                                    "datos_clave": [{"dato": "Última transferencia", "valor": "$320.000 de Comercial Sur"}],
                                    "plazos": []},
    }
    return {"version": 1, "cuenta": "yo@miempresa.cl (DEMO)", "propios": ["yo@miempresa.cl"], "ultimo_ms": ahora,
            "ultima_sync": "", "mensajes": {m["id"]: m for m in mensajes}, "contactos": contactos, "errores": []}
