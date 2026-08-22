# -*- coding: utf-8 -*-
"""
Cliente para los Web Services SOAP de Recepción y Autorización de
Comprobantes Electrónicos del SRI (sección 7 de la Ficha Técnica).

Se usa `requests` con sobres SOAP armados a mano (sin `zeep`) para evitar
una dependencia pesada; los WSDL del SRI son simples.

Endpoints (offline):
  PRUEBAS:
    Recepción:    https://celcer.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline
    Autorización: https://celcer.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline
  PRODUCCIÓN:
    Recepción:    https://cel.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline
    Autorización: https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline
"""
from __future__ import annotations
import base64
import time
import requests
from lxml import etree

ENDPOINTS = {
    "PRUEBAS": {
        "recepcion": "https://celcer.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline",
        "autorizacion": "https://celcer.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline",
    },
    "PRODUCCION": {
        "recepcion": "https://cel.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline",
        "autorizacion": "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline",
    },
}

_NS = {
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "rec": "http://ec.gob.sri.ws.recepcion",
    "aut": "http://ec.gob.sri.ws.autorizacion",
}


class SRIError(Exception):
    pass


def enviar_comprobante(xml_firmado: bytes, ambiente: str = "PRUEBAS", timeout: int = 30) -> dict:
    """Envía el comprobante firmado al WS de Recepción.
    Devuelve dict con 'estado' ('RECIBIDA' o 'DEVUELTA') y 'mensajes' (lista de dicts)."""
    url = ENDPOINTS[ambiente]["recepcion"]
    xml_b64 = base64.b64encode(xml_firmado)

    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                   xmlns:rec="http://ec.gob.sri.ws.recepcion">
  <soapenv:Header/>
  <soapenv:Body>
    <rec:validarComprobante>
      <xml>{xml_b64.decode('ascii')}</xml>
    </rec:validarComprobante>
  </soapenv:Body>
</soapenv:Envelope>"""

    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
    resp = requests.post(url, data=envelope.encode("utf-8"), headers=headers, timeout=timeout)
    resp.raise_for_status()

    root = etree.fromstring(resp.content)
    estado_el = root.find(".//{*}estado")
    estado = estado_el.text if estado_el is not None else None

    mensajes = []
    for m in root.findall(".//{*}mensaje"):
        mensajes.append({
            "identificador": (m.findtext("{*}identificador") or ""),
            "mensaje": (m.findtext("{*}mensaje") or ""),
            "informacionAdicional": (m.findtext("{*}informacionAdicional") or ""),
            "tipo": (m.findtext("{*}tipo") or ""),
        })

    return {"estado": estado, "mensajes": mensajes, "raw": resp.text}


def consultar_autorizacion(clave_acceso: str, ambiente: str = "PRUEBAS", timeout: int = 30) -> dict:
    """Consulta el estado de autorización de un comprobante por su clave de acceso.
    Devuelve dict con 'estado' (AUTORIZADO/NO AUTORIZADO/etc.), 'mensajes' y 'xml_autorizado' (str o None)."""
    url = ENDPOINTS[ambiente]["autorizacion"]

    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                   xmlns:aut="http://ec.gob.sri.ws.autorizacion">
  <soapenv:Header/>
  <soapenv:Body>
    <aut:autorizacionComprobante>
      <claveAccesoComprobante>{clave_acceso}</claveAccesoComprobante>
    </aut:autorizacionComprobante>
  </soapenv:Body>
</soapenv:Envelope>"""

    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
    resp = requests.post(url, data=envelope.encode("utf-8"), headers=headers, timeout=timeout)
    resp.raise_for_status()

    root = etree.fromstring(resp.content)
    estado_el = root.find(".//{*}estado")
    estado = estado_el.text if estado_el is not None else None

    mensajes = []
    for m in root.findall(".//{*}mensaje"):
        mensajes.append({
            "identificador": (m.findtext("{*}identificador") or ""),
            "mensaje": (m.findtext("{*}mensaje") or ""),
            "tipo": (m.findtext("{*}tipo") or ""),
        })

    comprobante_el = root.find(".//{*}comprobante")
    xml_autorizado = comprobante_el.text if comprobante_el is not None else None

    fecha_autorizacion = root.findtext(".//{*}fechaAutorizacion")

    return {
        "estado": estado,
        "mensajes": mensajes,
        "xml_autorizado": xml_autorizado,
        "fecha_autorizacion": fecha_autorizacion,
    }


def enviar_y_autorizar(xml_firmado: bytes, clave_acceso: str, ambiente: str = "PRUEBAS",
                        espera_seg: int = 3, reintentos: int = 5) -> dict:
    """Flujo completo: envía a Recepción y luego consulta Autorización con
    reintentos (la ficha técnica recomienda esperar de forma asíncrona,
    punto 7.4). Máximo teórico de espera del SRI: 24 horas; en la práctica
    suele resolver en segundos."""
    recepcion = enviar_comprobante(xml_firmado, ambiente=ambiente)
    if recepcion["estado"] != "RECIBIDA":
        return {"fase": "RECEPCION", **recepcion}

    for intento in range(reintentos):
        time.sleep(espera_seg)
        autorizacion = consultar_autorizacion(clave_acceso, ambiente=ambiente)
        if autorizacion["estado"] in ("AUTORIZADO", "NO AUTORIZADO", "RECHAZADO"):
            return {"fase": "AUTORIZACION", "recepcion": recepcion, **autorizacion}

    return {"fase": "AUTORIZACION_PENDIENTE", "recepcion": recepcion,
            "mensaje": "El SRI no respondió en el tiempo de espera configurado; "
                       "reintenta la consulta de autorización más tarde."}
