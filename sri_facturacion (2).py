# -*- coding: utf-8 -*-
"""
Facturador Electronico SRI - Ecuador (archivo unico, listo para Streamlit Cloud)

Genera, firma (XAdES-BES) y envia al SRI: Facturas, Comprobantes de
Retencion y Guias de Remision, segun la Ficha Tecnica de Comprobantes
Electronicos - Esquema Off-line (v2.32/2.34).

Ejecutar con:  streamlit run sri_facturacion.py
Dependencias:  SOLO streamlit, cryptography y requests (ver requirements.txt).
               No usa lxml: toda la parte XML se hace con la libreria
               estandar xml.etree.ElementTree.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import requests
import streamlit as st
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes

# ============================================================================
# SECCION 1: CATALOGOS (codigos de tablas del SRI)
# ============================================================================
# Tabla 2 - Tipo de emisión (esquema off-line solo admite "normal")
TIPO_EMISION = {
    "NORMAL": "1",
}

# Tabla 3 - Tipos de comprobante
TIPO_COMPROBANTE = {
    "FACTURA": "01",
    "LIQUIDACION_COMPRA": "03",
    "NOTA_CREDITO": "04",
    "NOTA_DEBITO": "05",
    "GUIA_REMISION": "06",
    "RETENCION": "07",
}

# Tabla 4 - Tipo de ambiente
TIPO_AMBIENTE = {
    "PRUEBAS": "1",
    "PRODUCCION": "2",
}

# Tabla 6 - Tipo de identificación del comprador / sujeto retenido / destinatario
TIPO_IDENTIFICACION = {
    "RUC": "04",
    "CEDULA": "05",
    "PASAPORTE": "06",
    "CONSUMIDOR_FINAL": "07",
    "EXTERIOR": "08",
}

IDENTIFICACION_CONSUMIDOR_FINAL = "9999999999999"  # 13 nueves
RAZON_SOCIAL_PRUEBAS = "PRUEBAS SERVICIO DE RENTAS INTERNAS"

# Tabla 16 - Código de impuesto
CODIGO_IMPUESTO = {
    "IVA": "2",
    "ICE": "3",
    "IRBPNR": "5",
}

# Tabla 17 - Código de porcentaje de IVA (codigoPorcentaje para impuesto IVA)
CODIGO_PORCENTAJE_IVA = {
    "0": "0",
    "12": "2",
    "14": "3",
    "15": "4",
    "5": "5",
    "NO_OBJETO": "6",
    "EXENTO": "7",
    "DIFERENCIADO_8": "8",
    "13": "10",
}
TARIFA_IVA_VALOR = {  # codigoPorcentaje -> tarifa numérica real
    "0": 0, "2": 12, "3": 14, "4": 15, "5": 5,
    "6": 0, "7": 0, "8": 8, "10": 13,
}

# Tabla 19 - Impuesto a retener (para comprobante de retención)
CODIGO_IMPUESTO_RETENCION = {
    "RENTA": "1",
    "IVA": "2",
    "IVA_PRESUNTIVO": "4",
    "ISD": "6",
}

# Tabla 20 - Retención de IVA (codigoRetencion -> porcentaje)
CODIGO_RETENCION_IVA = {
    "10": "9",
    "20": "10",
    "30": "1",
    "50": "11",
    "70": "2",
    "100": "3",
    "0_CERO": "7",       # retención en cero
    "NO_PROCEDE": "8",   # no procede retención
}

# Retención de ISD vigente (verificar antes de producción - cambia por decreto)
CODIGO_RETENCION_ISD_5 = "4586"  # 5% vigente desde mayo 2025 (ficha técnica 2.32)

# Tabla 24 - Formas de pago (SRI, catálogo estable; verificar última versión)
FORMA_PAGO = {
    "SIN_UTILIZACION_SISTEMA_FINANCIERO": "01",
    "COMPENSACION_DEUDAS": "15",
    "TARJETA_DEBITO": "16",
    "DINERO_ELECTRONICO": "17",
    "TARJETA_PREPAGO": "18",
    "TARJETA_CREDITO": "19",
    "OTROS_CON_UTILIZACION_SISTEMA_FINANCIERO": "20",
    "ENDOSO_TITULOS": "21",
}

# Tabla 3 (documentos de sustento en retención) usa los mismos códigos de
# TIPO_COMPROBANTE.

WS_ENDPOINTS = {
    "PRUEBAS": {
        "recepcion": "https://celcer.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline?wsdl",
        "autorizacion": "https://celcer.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl",
    },
    "PRODUCCION": {
        "recepcion": "https://cel.sri.gob.ec/comprobantes-electronicos-ws/RecepcionComprobantesOffline?wsdl",
        "autorizacion": "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl",
    },
}


# ============================================================================
# SECCION 2: CLAVE DE ACCESO (49 digitos, modulo 11)
# ============================================================================


def _modulo11(digits: str) -> str:
    """Calcula el dígito verificador módulo 11 (factor de chequeo 2..7,
    aplicado de derecha a izquierda, según el ejemplo de la ficha técnica)."""
    weights = [2, 3, 4, 5, 6, 7]
    total = 0
    for i, ch in enumerate(reversed(digits)):
        weight = weights[i % len(weights)]
        total += int(ch) * weight
    remainder = total % 11
    result = 11 - remainder
    if result == 11:
        return "0"
    if result == 10:
        return "1"
    return str(result)


def generar_codigo_numerico(seed: int | None = None) -> str:
    """8 dígitos numéricos arbitrarios (potestad del emisor, según 5.2 de la
    ficha técnica). Se recomienda que sea reproducible por comprobante
    (p.ej. derivado del secuencial) para poder reintentar envíos con la
    misma clave de acceso."""
    rnd = random.Random(seed)
    return "".join(str(rnd.randint(0, 9)) for _ in range(8))


def generar_clave_acceso(
    fecha_emision: datetime.date,
    tipo_comprobante: str,   # código de 2 dígitos, tabla 3 (ej. "01")
    ruc: str,                # 13 dígitos
    ambiente: str,           # "1" pruebas / "2" producción, tabla 4
    establecimiento: str,    # 3 dígitos
    punto_emision: str,      # 3 dígitos
    secuencial: str,         # 9 dígitos
    codigo_numerico: str | None = None,
    tipo_emision: str = "1",  # offline solo admite "1" (emisión normal)
) -> str:
    if len(ruc) != 13 or not ruc.isdigit():
        raise ValueError("El RUC debe tener 13 dígitos numéricos")
    if ambiente not in ("1", "2"):
        raise ValueError("Ambiente inválido (usar '1' pruebas o '2' producción)")
    if len(establecimiento) != 3 or len(punto_emision) != 3:
        raise ValueError("Establecimiento y punto de emisión deben tener 3 dígitos")
    if len(secuencial) != 9 or not secuencial.isdigit():
        raise ValueError("El secuencial debe tener 9 dígitos numéricos")
    if len(tipo_comprobante) != 2:
        raise ValueError("El tipo de comprobante debe tener 2 dígitos (tabla 3)")

    if codigo_numerico is None:
        codigo_numerico = generar_codigo_numerico()
    if len(codigo_numerico) != 8 or not codigo_numerico.isdigit():
        raise ValueError("El código numérico debe tener 8 dígitos")

    fecha_str = fecha_emision.strftime("%d%m%Y")
    serie = establecimiento + punto_emision

    base48 = (
        fecha_str
        + tipo_comprobante
        + ruc
        + ambiente
        + serie
        + secuencial
        + codigo_numerico
        + tipo_emision
    )
    if len(base48) != 48:
        raise ValueError(f"La clave base debería tener 48 dígitos, tiene {len(base48)}")

    digito_verificador = _modulo11(base48)
    return base48 + digito_verificador


if __name__ == "__main__":
    # Autoverificación con el ejemplo de la ficha técnica (sección 5.2):
    # cadena 41261533 -> dígito verificador esperado = 6
    assert _modulo11("41261533") == "6", "Fallo en autoverificación del módulo 11"
    print("Autoverificación módulo 11: OK")


# ============================================================================
# SECCION 3: CONSTRUCCION DE XML (Factura, Retencion, Guia de Remision)
# ============================================================================



def _fmt(n: float) -> str:
    return f"{float(n):.2f}"


def _fecha(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _sub(parent, tag, text=None, **attrib):
    el = ET.SubElement(parent, tag, attrib) if attrib else ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


# --------------------------------------------------------------------------
# Estructuras de datos de entrada
# --------------------------------------------------------------------------

@dataclass
class Emisor:
    ruc: str
    razon_social: str
    dir_matriz: str
    establecimiento: str
    punto_emision: str
    nombre_comercial: Optional[str] = None
    dir_establecimiento: Optional[str] = None
    contribuyente_especial: Optional[str] = None
    obligado_contabilidad: Optional[bool] = None


@dataclass
class ImpuestoLinea:
    codigo: str
    codigo_porcentaje: str
    tarifa: float
    base_imponible: float
    valor: float


@dataclass
class DetalleFactura:
    codigo_principal: str
    descripcion: str
    cantidad: float
    precio_unitario: float
    descuento: float
    precio_total_sin_impuesto: float
    impuestos: list[ImpuestoLinea]
    codigo_auxiliar: Optional[str] = None
    detalles_adicionales: dict[str, str] = field(default_factory=dict)


@dataclass
class Pago:
    forma_pago: str
    total: float
    plazo: Optional[int] = None
    unidad_tiempo: Optional[str] = None


@dataclass
class DatosFactura:
    emisor: Emisor
    secuencial: str
    fecha_emision: date
    tipo_identificacion_comprador: str
    razon_social_comprador: str
    identificacion_comprador: str
    detalles: list[DetalleFactura]
    pagos: list[Pago]
    ambiente: str = "1"
    direccion_comprador: Optional[str] = None
    guia_remision: Optional[str] = None
    total_descuento: float = 0.0
    propina: float = 0.0
    valor_ret_iva: Optional[float] = None
    valor_ret_renta: Optional[float] = None
    info_adicional: dict[str, str] = field(default_factory=dict)
    codigo_numerico: Optional[str] = None


@dataclass
class ImpuestoRetenido:
    codigo: str
    codigo_retencion: str
    base_imponible: float
    porcentaje_retener: float
    valor_retenido: float
    cod_doc_sustento: str
    fecha_emision_doc_sustento: date
    num_doc_sustento: Optional[str] = None


@dataclass
class DatosRetencion:
    emisor: Emisor
    secuencial: str
    fecha_emision: date
    tipo_identificacion_sujeto_retenido: str
    razon_social_sujeto_retenido: str
    identificacion_sujeto_retenido: str
    periodo_fiscal: str
    impuestos: list[ImpuestoRetenido]
    ambiente: str = "1"
    info_adicional: dict[str, str] = field(default_factory=dict)
    codigo_numerico: Optional[str] = None


@dataclass
class DetalleGuia:
    descripcion: str
    cantidad: float
    codigo_interno: Optional[str] = None
    codigo_adicional: Optional[str] = None
    detalles_adicionales: dict[str, str] = field(default_factory=dict)


@dataclass
class Destinatario:
    identificacion: str
    razon_social: str
    direccion: str
    motivo_traslado: str
    detalles: list[DetalleGuia]
    doc_aduanero_unico: Optional[str] = None
    cod_estab_destino: Optional[str] = None
    ruta: Optional[str] = None
    cod_doc_sustento: Optional[str] = None
    num_doc_sustento: Optional[str] = None
    num_aut_doc_sustento: Optional[str] = None
    fecha_emision_doc_sustento: Optional[date] = None


@dataclass
class DatosGuiaRemision:
    emisor: Emisor
    secuencial: str
    dir_partida: str
    razon_social_transportista: str
    tipo_identificacion_transportista: str
    ruc_transportista: str
    fecha_ini_transporte: date
    fecha_fin_transporte: date
    placa: str
    destinatarios: list[Destinatario]
    ambiente: str = "1"
    info_adicional: dict[str, str] = field(default_factory=dict)
    codigo_numerico: Optional[str] = None


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def _info_tributaria(root, emisor: Emisor, ambiente: str, cod_doc: str,
                      clave_acceso: str, secuencial: str):
    it = _sub(root, "infoTributaria")
    _sub(it, "ambiente", ambiente)
    _sub(it, "tipoEmision", TIPO_EMISION["NORMAL"])
    _sub(it, "razonSocial", emisor.razon_social)
    if emisor.nombre_comercial:
        _sub(it, "nombreComercial", emisor.nombre_comercial)
    _sub(it, "ruc", emisor.ruc)
    _sub(it, "claveAcceso", clave_acceso)
    _sub(it, "codDoc", cod_doc)
    _sub(it, "estab", emisor.establecimiento)
    _sub(it, "ptoEmi", emisor.punto_emision)
    _sub(it, "secuencial", secuencial)
    _sub(it, "dirMatriz", emisor.dir_matriz)
    return it


def _info_adicional(root, campos: dict[str, str]):
    if not campos:
        return
    ia = _sub(root, "infoAdicional")
    for nombre, valor in campos.items():
        _sub(ia, "campoAdicional", str(valor), nombre=nombre)


def build_factura(d: DatosFactura) -> tuple[bytes, str]:
    clave = generar_clave_acceso(
        fecha_emision=d.fecha_emision,
        tipo_comprobante=TIPO_COMPROBANTE["FACTURA"],
        ruc=d.emisor.ruc,
        ambiente=d.ambiente,
        establecimiento=d.emisor.establecimiento,
        punto_emision=d.emisor.punto_emision,
        secuencial=d.secuencial,
        codigo_numerico=d.codigo_numerico,
    )

    root = ET.Element("factura", {"id": "comprobante", "version": "1.0.0"})
    _info_tributaria(root, d.emisor, d.ambiente, TIPO_COMPROBANTE["FACTURA"],
                      clave, d.secuencial)

    inf = _sub(root, "infoFactura")
    _sub(inf, "fechaEmision", _fecha(d.fecha_emision))
    if d.emisor.dir_establecimiento:
        _sub(inf, "dirEstablecimiento", d.emisor.dir_establecimiento)
    if d.emisor.contribuyente_especial:
        _sub(inf, "contribuyenteEspecial", d.emisor.contribuyente_especial)
    if d.emisor.obligado_contabilidad is not None:
        _sub(inf, "obligadoContabilidad", "SI" if d.emisor.obligado_contabilidad else "NO")
    _sub(inf, "tipoIdentificacionComprador", d.tipo_identificacion_comprador)
    if d.guia_remision:
        _sub(inf, "guiaRemision", d.guia_remision)
    _sub(inf, "razonSocialComprador", d.razon_social_comprador)
    _sub(inf, "identificacionComprador", d.identificacion_comprador)
    if d.direccion_comprador:
        _sub(inf, "direccionComprador", d.direccion_comprador)

    total_sin_impuestos = sum(det.precio_total_sin_impuesto for det in d.detalles)
    _sub(inf, "totalSinImpuestos", _fmt(total_sin_impuestos))
    _sub(inf, "totalDescuento", _fmt(d.total_descuento))

    resumen: dict[tuple, dict] = {}
    for det in d.detalles:
        for imp in det.impuestos:
            key = (imp.codigo, imp.codigo_porcentaje)
            if key not in resumen:
                resumen[key] = {"base": 0.0, "valor": 0.0}
            resumen[key]["base"] += imp.base_imponible
            resumen[key]["valor"] += imp.valor

    tci = _sub(inf, "totalConImpuestos")
    for (codigo, cod_pct), vals in resumen.items():
        ti = _sub(tci, "totalImpuesto")
        _sub(ti, "codigo", codigo)
        _sub(ti, "codigoPorcentaje", cod_pct)
        _sub(ti, "baseImponible", _fmt(vals["base"]))
        _sub(ti, "valor", _fmt(vals["valor"]))

    _sub(inf, "propina", _fmt(d.propina))
    importe_total = total_sin_impuestos - d.total_descuento + sum(v["valor"] for v in resumen.values()) + d.propina
    _sub(inf, "importeTotal", _fmt(importe_total))
    _sub(inf, "moneda", "DOLAR")

    pagos_el = _sub(inf, "pagos")
    for pago in d.pagos:
        p = _sub(pagos_el, "pago")
        _sub(p, "formaPago", pago.forma_pago)
        _sub(p, "total", _fmt(pago.total))
        if pago.plazo is not None:
            _sub(p, "plazo", pago.plazo)
        if pago.unidad_tiempo:
            _sub(p, "unidadTiempo", pago.unidad_tiempo)

    if d.valor_ret_iva is not None:
        _sub(inf, "valorRetIva", _fmt(d.valor_ret_iva))
    if d.valor_ret_renta is not None:
        _sub(inf, "valorRetRenta", _fmt(d.valor_ret_renta))

    detalles_el = _sub(root, "detalles")
    for det in d.detalles:
        de = _sub(detalles_el, "detalle")
        _sub(de, "codigoPrincipal", det.codigo_principal)
        if det.codigo_auxiliar:
            _sub(de, "codigoAuxiliar", det.codigo_auxiliar)
        _sub(de, "descripcion", det.descripcion)
        _sub(de, "cantidad", _fmt(det.cantidad))
        _sub(de, "precioUnitario", _fmt(det.precio_unitario))
        _sub(de, "descuento", _fmt(det.descuento))
        _sub(de, "precioTotalSinImpuesto", _fmt(det.precio_total_sin_impuesto))
        if det.detalles_adicionales:
            da = _sub(de, "detallesAdicionales")
            for nombre, valor in det.detalles_adicionales.items():
                _sub(da, "detAdicional", nombre=nombre, valor=str(valor))
        imps = _sub(de, "impuestos")
        for imp in det.impuestos:
            ie = _sub(imps, "impuesto")
            _sub(ie, "codigo", imp.codigo)
            _sub(ie, "codigoPorcentaje", imp.codigo_porcentaje)
            _sub(ie, "tarifa", _fmt(imp.tarifa))
            _sub(ie, "baseImponible", _fmt(imp.base_imponible))
            _sub(ie, "valor", _fmt(imp.valor))

    _info_adicional(root, d.info_adicional)

    xml_bytes = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return xml_bytes, clave


def build_retencion(d: DatosRetencion) -> tuple[bytes, str]:
    clave = generar_clave_acceso(
        fecha_emision=d.fecha_emision,
        tipo_comprobante=TIPO_COMPROBANTE["RETENCION"],
        ruc=d.emisor.ruc,
        ambiente=d.ambiente,
        establecimiento=d.emisor.establecimiento,
        punto_emision=d.emisor.punto_emision,
        secuencial=d.secuencial,
        codigo_numerico=d.codigo_numerico,
    )

    root = ET.Element("comprobanteRetencion", {"id": "comprobante", "version": "1.0.0"})
    _info_tributaria(root, d.emisor, d.ambiente, TIPO_COMPROBANTE["RETENCION"],
                      clave, d.secuencial)

    inf = _sub(root, "infoCompRetencion")
    _sub(inf, "fechaEmision", _fecha(d.fecha_emision))
    if d.emisor.dir_establecimiento:
        _sub(inf, "dirEstablecimiento", d.emisor.dir_establecimiento)
    if d.emisor.contribuyente_especial:
        _sub(inf, "contribuyenteEspecial", d.emisor.contribuyente_especial)
    if d.emisor.obligado_contabilidad is not None:
        _sub(inf, "obligadoContabilidad", "SI" if d.emisor.obligado_contabilidad else "NO")
    _sub(inf, "tipoIdentificacionSujetoRetenido", d.tipo_identificacion_sujeto_retenido)
    _sub(inf, "razonSocialSujetoRetenido", d.razon_social_sujeto_retenido)
    _sub(inf, "identificacionSujetoRetenido", d.identificacion_sujeto_retenido)
    _sub(inf, "periodoFiscal", d.periodo_fiscal)

    imps = _sub(root, "impuestos")
    for imp in d.impuestos:
        ie = _sub(imps, "impuesto")
        _sub(ie, "codigo", imp.codigo)
        _sub(ie, "codigoRetencion", imp.codigo_retencion)
        _sub(ie, "baseImponible", _fmt(imp.base_imponible))
        _sub(ie, "porcentajeRetener", _fmt(imp.porcentaje_retener))
        _sub(ie, "valorRetenido", _fmt(imp.valor_retenido))
        _sub(ie, "codDocSustento", imp.cod_doc_sustento)
        if imp.num_doc_sustento:
            _sub(ie, "numDocSustento", imp.num_doc_sustento)
        _sub(ie, "fechaEmisionDocSustento", _fecha(imp.fecha_emision_doc_sustento))

    _info_adicional(root, d.info_adicional)

    xml_bytes = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return xml_bytes, clave


def build_guia_remision(d: DatosGuiaRemision) -> tuple[bytes, str]:
    clave = generar_clave_acceso(
        fecha_emision=d.fecha_ini_transporte,
        tipo_comprobante=TIPO_COMPROBANTE["GUIA_REMISION"],
        ruc=d.emisor.ruc,
        ambiente=d.ambiente,
        establecimiento=d.emisor.establecimiento,
        punto_emision=d.emisor.punto_emision,
        secuencial=d.secuencial,
        codigo_numerico=d.codigo_numerico,
    )

    root = ET.Element("guiaRemision", {"id": "comprobante", "version": "1.0.0"})
    _info_tributaria(root, d.emisor, d.ambiente, TIPO_COMPROBANTE["GUIA_REMISION"],
                      clave, d.secuencial)

    inf = _sub(root, "infoGuiaRemision")
    if d.emisor.dir_establecimiento:
        _sub(inf, "dirEstablecimiento", d.emisor.dir_establecimiento)
    _sub(inf, "dirPartida", d.dir_partida)
    _sub(inf, "razonSocialTransportista", d.razon_social_transportista)
    _sub(inf, "tipoIdentificacionTransportista", d.tipo_identificacion_transportista)
    _sub(inf, "rucTransportista", d.ruc_transportista)
    if d.emisor.obligado_contabilidad is not None:
        _sub(inf, "obligadoContabilidad", "SI" if d.emisor.obligado_contabilidad else "NO")
    if d.emisor.contribuyente_especial:
        _sub(inf, "contribuyenteEspecial", d.emisor.contribuyente_especial)
    _sub(inf, "fechaIniTransporte", _fecha(d.fecha_ini_transporte))
    _sub(inf, "fechaFinTransporte", _fecha(d.fecha_fin_transporte))
    _sub(inf, "placa", d.placa)

    dests_el = _sub(root, "destinatarios")
    for dest in d.destinatarios:
        de = _sub(dests_el, "destinatario")
        _sub(de, "identificacionDestinatario", dest.identificacion)
        _sub(de, "razonSocialDestinatario", dest.razon_social)
        _sub(de, "dirDestinatario", dest.direccion)
        _sub(de, "motivoTraslado", dest.motivo_traslado)
        if dest.doc_aduanero_unico:
            _sub(de, "docAduaneroUnico", dest.doc_aduanero_unico)
        if dest.cod_estab_destino:
            _sub(de, "codEstabDestino", dest.cod_estab_destino)
        if dest.ruta:
            _sub(de, "ruta", dest.ruta)
        if dest.cod_doc_sustento:
            _sub(de, "codDocSustento", dest.cod_doc_sustento)
        if dest.num_doc_sustento:
            _sub(de, "numDocSustento", dest.num_doc_sustento)
        if dest.num_aut_doc_sustento:
            _sub(de, "numAutDocSustento", dest.num_aut_doc_sustento)
        if dest.fecha_emision_doc_sustento:
            _sub(de, "fechaEmisionDocSustento", _fecha(dest.fecha_emision_doc_sustento))

        detalles_el = _sub(de, "detalles")
        for det in dest.detalles:
            det_el = _sub(detalles_el, "detalle")
            if det.codigo_interno:
                _sub(det_el, "codigoInterno", det.codigo_interno)
            if det.codigo_adicional:
                _sub(det_el, "codigoAdicional", det.codigo_adicional)
            _sub(det_el, "descripcion", det.descripcion)
            _sub(det_el, "cantidad", _fmt(det.cantidad))
            if det.detalles_adicionales:
                da = _sub(det_el, "detallesAdicionales")
                for nombre, valor in det.detalles_adicionales.items():
                    _sub(da, "detAdicional", nombre=nombre, valor=str(valor))

    _info_adicional(root, d.info_adicional)

    xml_bytes = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return xml_bytes, clave


# ============================================================================
# SECCION 4: FIRMA XADES-BES (sin lxml, con xml.etree.ElementTree)
# ============================================================================


DS_NS = "http://www.w3.org/2000/09/xmldsig#"
XADES_NS = "http://uri.etsi.org/01903/v1.3.2#"
C14N_ALGO = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ENVELOPED_ALGO = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
SHA1_ALGO = "http://www.w3.org/2000/09/xmldsig#sha1"
RSA_SHA1_ALGO = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
SIGNED_PROPS_TYPE = "http://uri.etsi.org/01903#SignedProperties"

ET.register_namespace("ds", DS_NS)
ET.register_namespace("etsi", XADES_NS)


def _ds(tag: str) -> str:
    return f"{{{DS_NS}}}{tag}"


def _etsi(tag: str) -> str:
    return f"{{{XADES_NS}}}{tag}"


def _c14n(node) -> bytes:
    """Canonicaliza un Element serializándolo y aplicando ET.canonicalize (C14N 2.0)."""
    xml_str = ET.tostring(node, encoding="unicode")
    canon = ET.canonicalize(xml_str)
    return canon.encode("utf-8")


def _sha1_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data).digest()).decode("ascii")


def cargar_certificado_p12(p12_bytes: bytes, password: str):
    private_key, certificate, additional = pkcs12.load_key_and_certificates(
        p12_bytes, password.encode("utf-8")
    )
    if private_key is None or certificate is None:
        raise ValueError(
            "No se pudo leer la clave privada o el certificado del archivo .p12. "
            "Verifica la contraseña."
        )
    return private_key, certificate, additional


def firmar_xml(xml_bytes: bytes, p12_bytes: bytes, password: str) -> bytes:
    """Inserta una firma XAdES-BES enveloped dentro del comprobante y
    devuelve el XML firmado como bytes UTF-8."""
    private_key, certificate, _ = cargar_certificado_p12(p12_bytes, password)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("Se esperaba una clave privada RSA en el certificado .p12")

    root = ET.fromstring(xml_bytes)

    doc_id = "comprobante"
    if not root.get("id"):
        root.set("id", doc_id)

    sig_id = f"Signature{uuid.uuid4().hex[:8]}"
    signed_info_id = f"Signature-SignedInfo{uuid.uuid4().hex[:8]}"
    keyinfo_id = f"Certificate{uuid.uuid4().hex[:8]}"
    signed_props_id = f"SignedProperties{uuid.uuid4().hex[:8]}"
    object_id = f"Signature-Object{uuid.uuid4().hex[:8]}"
    ref_doc_id = f"Reference-ID-{uuid.uuid4().hex[:8]}"

    cert_der = certificate.public_bytes(encoding=Encoding.DER)
    cert_b64 = base64.b64encode(cert_der).decode("ascii")
    cert_digest_b64 = _sha1_b64(cert_der)
    issuer_name = certificate.issuer.rfc4514_string()
    serial_number = str(certificate.serial_number)
    signing_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # nodo Signature (vacío por ahora) insertado en el documento
    signature_el = ET.SubElement(root, _ds("Signature"), {"Id": sig_id})

    # ---- 1) Digest del documento completo (transformada enveloped) ----
    doc_for_digest = copy.deepcopy(root)
    sig_in_copy = doc_for_digest.find(_ds("Signature"))
    if sig_in_copy is not None:
        doc_for_digest.remove(sig_in_copy)
    doc_digest = _sha1_b64(_c14n(doc_for_digest))

    # ---- 2) Object con QualifyingProperties (XAdES SignedProperties) ----
    object_el = ET.SubElement(signature_el, _ds("Object"), {"Id": object_id})
    qp = ET.SubElement(object_el, _etsi("QualifyingProperties"), {"Target": f"#{sig_id}"})
    sp = ET.SubElement(qp, _etsi("SignedProperties"), {"Id": signed_props_id})
    ssp = ET.SubElement(sp, _etsi("SignedSignatureProperties"))
    ET.SubElement(ssp, _etsi("SigningTime")).text = signing_time
    sc = ET.SubElement(ssp, _etsi("SigningCertificate"))
    cert_node = ET.SubElement(sc, _etsi("Cert"))
    cd = ET.SubElement(cert_node, _etsi("CertDigest"))
    ET.SubElement(cd, _ds("DigestMethod"), {"Algorithm": SHA1_ALGO})
    ET.SubElement(cd, _ds("DigestValue")).text = cert_digest_b64
    issuer_serial = ET.SubElement(cert_node, _etsi("IssuerSerial"))
    ET.SubElement(issuer_serial, _ds("X509IssuerName")).text = issuer_name
    ET.SubElement(issuer_serial, _ds("X509SerialNumber")).text = serial_number

    signed_props_digest = _sha1_b64(_c14n(sp))

    # ---- 3) SignedInfo (se construye aparte y se inserta al final) ----
    signed_info = ET.Element(_ds("SignedInfo"), {"Id": signed_info_id})
    ET.SubElement(signed_info, _ds("CanonicalizationMethod"), {"Algorithm": C14N_ALGO})
    ET.SubElement(signed_info, _ds("SignatureMethod"), {"Algorithm": RSA_SHA1_ALGO})

    ref1 = ET.SubElement(signed_info, _ds("Reference"), {"Id": ref_doc_id, "URI": f"#{doc_id}"})
    transforms1 = ET.SubElement(ref1, _ds("Transforms"))
    ET.SubElement(transforms1, _ds("Transform"), {"Algorithm": ENVELOPED_ALGO})
    ET.SubElement(ref1, _ds("DigestMethod"), {"Algorithm": SHA1_ALGO})
    ET.SubElement(ref1, _ds("DigestValue")).text = doc_digest

    ref2 = ET.SubElement(signed_info, _ds("Reference"), {"URI": f"#{keyinfo_id}"})
    ET.SubElement(ref2, _ds("DigestMethod"), {"Algorithm": SHA1_ALGO})
    keyinfo_el_tmp = ET.Element(_ds("KeyInfo"), {"Id": keyinfo_id})
    x509_data_tmp = ET.SubElement(keyinfo_el_tmp, _ds("X509Data"))
    ET.SubElement(x509_data_tmp, _ds("X509Certificate")).text = cert_b64
    keyinfo_digest = _sha1_b64(_c14n(keyinfo_el_tmp))
    ET.SubElement(ref2, _ds("DigestValue")).text = keyinfo_digest

    ref3 = ET.SubElement(signed_info, _ds("Reference"),
                          {"Type": SIGNED_PROPS_TYPE, "URI": f"#{signed_props_id}"})
    ET.SubElement(ref3, _ds("DigestMethod"), {"Algorithm": SHA1_ALGO})
    ET.SubElement(ref3, _ds("DigestValue")).text = signed_props_digest

    # ---- 4) Firmar SignedInfo con RSA-SHA1 ----
    signed_info_canon = _c14n(signed_info)
    signature_bytes = private_key.sign(signed_info_canon, padding.PKCS1v15(), hashes.SHA1())
    signature_value_b64 = base64.b64encode(signature_bytes).decode("ascii")

    # ---- 5) Ensamblar el nodo Signature final en el orden correcto ----
    signature_el.insert(0, signed_info)
    sig_value_el = ET.Element(_ds("SignatureValue"))
    sig_value_el.text = signature_value_b64
    signature_el.insert(1, sig_value_el)

    keyinfo_el = ET.Element(_ds("KeyInfo"), {"Id": keyinfo_id})
    x509_data_final = ET.SubElement(keyinfo_el, _ds("X509Data"))
    ET.SubElement(x509_data_final, _ds("X509Certificate")).text = cert_b64
    signature_el.insert(2, keyinfo_el)
    # Object (con QualifyingProperties) ya quedó insertado como último hijo

    xml_firmado = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return xml_firmado


# ============================================================================
# SECCION 5: CLIENTE SOAP DEL SRI
# ============================================================================

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


class SRIError(Exception):
    pass


def _findtext_any_ns(root, tag):
    el = root.find(f".//{{*}}{tag}")
    return el.text if el is not None else None


def enviar_comprobante(xml_firmado: bytes, ambiente: str = "PRUEBAS", timeout: int = 30) -> dict:
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

    root = ET.fromstring(resp.content)
    estado = _findtext_any_ns(root, "estado")

    mensajes = []
    for m in root.findall(".//{*}mensaje"):
        mensajes.append({
            "identificador": m.findtext("{*}identificador") or "",
            "mensaje": m.findtext("{*}mensaje") or "",
            "informacionAdicional": m.findtext("{*}informacionAdicional") or "",
            "tipo": m.findtext("{*}tipo") or "",
        })

    return {"estado": estado, "mensajes": mensajes, "raw": resp.text}


def consultar_autorizacion(clave_acceso: str, ambiente: str = "PRUEBAS", timeout: int = 30) -> dict:
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

    root = ET.fromstring(resp.content)
    estado = _findtext_any_ns(root, "estado")

    mensajes = []
    for m in root.findall(".//{*}mensaje"):
        mensajes.append({
            "identificador": m.findtext("{*}identificador") or "",
            "mensaje": m.findtext("{*}mensaje") or "",
            "tipo": m.findtext("{*}tipo") or "",
        })

    comprobante_el = root.find(".//{*}comprobante")
    xml_autorizado = comprobante_el.text if comprobante_el is not None else None
    fecha_autorizacion = _findtext_any_ns(root, "fechaAutorizacion")

    return {
        "estado": estado,
        "mensajes": mensajes,
        "xml_autorizado": xml_autorizado,
        "fecha_autorizacion": fecha_autorizacion,
    }


def enviar_y_autorizar(xml_firmado: bytes, clave_acceso: str, ambiente: str = "PRUEBAS",
                        espera_seg: int = 3, reintentos: int = 5) -> dict:
    recepcion = enviar_comprobante(xml_firmado, ambiente=ambiente)
    if recepcion["estado"] != "RECIBIDA":
        return {"fase": "RECEPCION", **recepcion}

    for _ in range(reintentos):
        time.sleep(espera_seg)
        autorizacion = consultar_autorizacion(clave_acceso, ambiente=ambiente)
        if autorizacion["estado"] in ("AUTORIZADO", "NO AUTORIZADO", "RECHAZADO"):
            return {"fase": "AUTORIZACION", "recepcion": recepcion, **autorizacion}

    return {"fase": "AUTORIZACION_PENDIENTE", "recepcion": recepcion,
            "mensaje": "El SRI no respondió en el tiempo de espera configurado; "
                       "reintenta la consulta de autorización más tarde."}


# ============================================================================
# SECCION 6: INTERFAZ STREAMLIT
# ============================================================================


st.set_page_config(page_title="Facturador Electrónico SRI", page_icon="🧾", layout="wide")

st.title("🧾 Facturador Electrónico SRI — Ecuador")
st.caption(
    "Genera, firma (XAdES-BES) y envía comprobantes al SRI según la Ficha "
    "Técnica de Comprobantes Electrónicos - Esquema Off-line (v2.32/2.34). "
    "⚠️ Prueba siempre primero en el AMBIENTE DE PRUEBAS."
)

# --------------------------------------------------------------------
# Sidebar: datos del emisor + certificado + ambiente
# --------------------------------------------------------------------
with st.sidebar:
    st.header("Datos del emisor")
    ruc = st.text_input("RUC (13 dígitos)", max_chars=13)
    razon_social = st.text_input("Razón social")
    nombre_comercial = st.text_input("Nombre comercial (opcional)")
    dir_matriz = st.text_input("Dirección matriz")
    dir_establecimiento = st.text_input("Dirección del establecimiento emisor (opcional)")
    col1, col2 = st.columns(2)
    establecimiento = col1.text_input("Cód. establecimiento", value="001", max_chars=3)
    punto_emision = col2.text_input("Punto de emisión", value="001", max_chars=3)
    contribuyente_especial = st.text_input("Nro. resolución contribuyente especial (opcional)")
    obligado_contabilidad = st.selectbox("Obligado a llevar contabilidad", ["No aplica", "SI", "NO"])

    st.divider()
    st.header("Ambiente y certificado")
    ambiente_label = st.radio("Ambiente", ["PRUEBAS", "PRODUCCIÓN"], index=0)
    ambiente = "1" if ambiente_label == "PRUEBAS" else "2"
    ambiente_ws = "PRUEBAS" if ambiente_label == "PRUEBAS" else "PRODUCCION"
    if ambiente_label == "PRODUCCIÓN":
        st.warning("Los comprobantes en PRODUCCIÓN tienen validez tributaria real.")

    p12_file = st.file_uploader("Certificado de firma electrónica (.p12)", type=["p12", "pfx"])
    p12_password = st.text_input("Contraseña del certificado", type="password")

    enviar_al_sri = st.checkbox("Enviar automáticamente al SRI tras firmar", value=True)


def _emisor_obj():
    oc = None
    if obligado_contabilidad == "SI":
        oc = True
    elif obligado_contabilidad == "NO":
        oc = False
    return Emisor(
        ruc=ruc.strip(),
        razon_social=razon_social.strip(),
        dir_matriz=dir_matriz.strip(),
        establecimiento=establecimiento.strip().zfill(3),
        punto_emision=punto_emision.strip().zfill(3),
        nombre_comercial=nombre_comercial.strip() or None,
        dir_establecimiento=dir_establecimiento.strip() or None,
        contribuyente_especial=contribuyente_especial.strip() or None,
        obligado_contabilidad=oc,
    )


def _validar_emisor():
    errores = []
    if len(ruc.strip()) != 13 or not ruc.strip().isdigit():
        errores.append("El RUC debe tener 13 dígitos numéricos.")
    if not razon_social.strip():
        errores.append("Falta la razón social.")
    if not dir_matriz.strip():
        errores.append("Falta la dirección matriz.")
    return errores


def _procesar_comprobante(xml_bytes: bytes, clave: str, tipo: str):
    """Muestra el XML, permite descargarlo, y opcionalmente lo firma y envía."""
    st.success(f"XML de {tipo} generado. Clave de acceso: `{clave}`")
    with st.expander("Ver XML sin firmar"):
        st.code(xml_bytes.decode("utf-8"), language="xml")
    st.download_button(
        f"Descargar XML sin firmar ({tipo})",
        data=xml_bytes,
        file_name=f"{clave}.xml",
        mime="application/xml",
    )

    if not p12_file or not p12_password:
        st.info("Sube el certificado .p12 y su contraseña en la barra lateral para firmar.")
        return

    if st.button(f"Firmar {tipo} con XAdES-BES", key=f"firmar_{clave}"):
        try:
            p12_bytes = p12_file.getvalue()
            xml_firmado = firmar_xml(xml_bytes, p12_bytes, p12_password)
        except Exception as e:
            st.error(f"No se pudo firmar el XML: {e}")
            return

        st.success("XML firmado correctamente.")
        with st.expander("Ver XML firmado"):
            st.code(xml_firmado.decode("utf-8"), language="xml")
        st.download_button(
            "Descargar XML firmado",
            data=xml_firmado,
            file_name=f"{clave}_firmado.xml",
            mime="application/xml",
            key=f"descarga_firmado_{clave}",
        )

        if enviar_al_sri:
            with st.spinner(f"Enviando al SRI (ambiente {ambiente_label})..."):
                try:
                    resultado = enviar_y_autorizar(xml_firmado, clave, ambiente=ambiente_ws)
                except Exception as e:
                    st.error(f"Error de comunicación con el SRI: {e}")
                    return

            fase = resultado.get("fase")
            if fase == "RECEPCION":
                st.error(f"El comprobante fue DEVUELTO en recepción: {resultado.get('mensajes')}")
            elif fase == "AUTORIZACION":
                estado = resultado.get("estado")
                if estado == "AUTORIZADO":
                    st.success(f"✅ Comprobante AUTORIZADO ({resultado.get('fecha_autorizacion')})")
                    if resultado.get("xml_autorizado"):
                        st.download_button(
                            "Descargar XML autorizado por el SRI",
                            data=resultado["xml_autorizado"],
                            file_name=f"{clave}_autorizado.xml",
                            mime="application/xml",
                            key=f"descarga_autorizado_{clave}",
                        )
                else:
                    st.error(f"Estado: {estado}. Mensajes: {resultado.get('mensajes')}")
            else:
                st.warning(resultado.get("mensaje", "El SRI no respondió a tiempo. Consulta más tarde."))


tab_factura, tab_retencion, tab_guia = st.tabs(
    ["📄 Factura", "🧾 Comprobante de Retención", "🚚 Guía de Remisión"]
)

# --------------------------------------------------------------------
# TAB: FACTURA
# --------------------------------------------------------------------
with tab_factura:
    st.subheader("Datos de la factura")
    colf1, colf2 = st.columns(2)
    secuencial_f = colf1.text_input("Secuencial (9 dígitos)", value="000000001", key="sec_f")
    fecha_f = colf2.date_input("Fecha de emisión", value=date.today(), key="fecha_f")

    st.markdown("**Comprador**")
    colc1, colc2, colc3 = st.columns(3)
    tipo_id_map = {"RUC": "04", "Cédula": "05", "Pasaporte": "06", "Consumidor final": "07"}
    tipo_id_comprador = colc1.selectbox("Tipo de identificación", list(tipo_id_map.keys()), key="tid_f")
    if tipo_id_comprador == "Consumidor final":
        identificacion_comprador = IDENTIFICACION_CONSUMIDOR_FINAL
        razon_social_comprador = colc2.text_input("Nombre (opcional para consumidor final)",
                                                    value="CONSUMIDOR FINAL", key="rs_f")
    else:
        identificacion_comprador = colc2.text_input("Identificación", key="id_f")
        razon_social_comprador = colc3.text_input("Razón social / nombres", key="rs_f2")
    direccion_comprador = st.text_input("Dirección del comprador (opcional)", key="dir_f")

    st.markdown("**Detalles (líneas de la factura)**")
    if "detalles_factura" not in st.session_state:
        st.session_state.detalles_factura = []

    with st.form("form_detalle_factura", clear_on_submit=True):
        d1, d2, d3, d4, d5 = st.columns(5)
        cod = d1.text_input("Código")
        desc = d2.text_input("Descripción")
        cant = d3.number_input("Cantidad", min_value=0.0, value=1.0, step=1.0)
        precio = d4.number_input("Precio unitario", min_value=0.0, value=0.0, step=0.01)
        desc_valor = d5.number_input("Descuento", min_value=0.0, value=0.0, step=0.01)
        tarifa_iva_label = st.selectbox(
            "Tarifa de IVA", ["15% (vigente)", "0%", "5%", "No objeto de IVA", "Exento de IVA", "13%", "14%"]
        )
        agregar = st.form_submit_button("Agregar línea")
        if agregar and desc:
            mapa_tarifa = {
                "15% (vigente)": ("4", 15), "0%": ("0", 0), "5%": ("5", 5),
                "No objeto de IVA": ("6", 0), "Exento de IVA": ("7", 0),
                "13%": ("10", 13), "14%": ("3", 14),
            }
            cod_pct, tarifa_val = mapa_tarifa[tarifa_iva_label]
            base = cant * precio - desc_valor
            valor_iva = round(base * tarifa_val / 100, 2)
            st.session_state.detalles_factura.append({
                "codigo": cod or "N/A", "descripcion": desc, "cantidad": cant,
                "precio_unitario": precio, "descuento": desc_valor,
                "base": round(base, 2), "cod_pct": cod_pct, "tarifa": tarifa_val,
                "valor_iva": valor_iva,
            })

    if st.session_state.detalles_factura:
        st.table(st.session_state.detalles_factura)
        if st.button("Vaciar líneas", key="vaciar_f"):
            st.session_state.detalles_factura = []
            st.rerun()

    forma_pago_label = st.selectbox("Forma de pago", list(FORMA_PAGO.keys()), key="fp_f")

    if st.button("Generar XML de Factura", type="primary"):
        errores = _validar_emisor()
        if not st.session_state.detalles_factura:
            errores.append("Agrega al menos una línea de detalle.")
        if not identificacion_comprador:
            errores.append("Falta la identificación del comprador.")
        if errores:
            for e in errores:
                st.error(e)
        else:
            detalles = []
            for item in st.session_state.detalles_factura:
                imp = ImpuestoLinea(
                    codigo=CODIGO_IMPUESTO["IVA"], codigo_porcentaje=item["cod_pct"],
                    tarifa=item["tarifa"], base_imponible=item["base"], valor=item["valor_iva"],
                )
                detalles.append(DetalleFactura(
                    codigo_principal=item["codigo"], descripcion=item["descripcion"],
                    cantidad=item["cantidad"], precio_unitario=item["precio_unitario"],
                    descuento=item["descuento"], precio_total_sin_impuesto=item["base"],
                    impuestos=[imp],
                ))
            total_sin_imp = sum(d.precio_total_sin_impuesto for d in detalles)
            total_iva = sum(d.impuestos[0].valor for d in detalles)
            total = total_sin_imp + total_iva

            datos = DatosFactura(
                emisor=_emisor_obj(), secuencial=secuencial_f.zfill(9), fecha_emision=fecha_f,
                tipo_identificacion_comprador=tipo_id_map[tipo_id_comprador],
                razon_social_comprador=razon_social_comprador or "CONSUMIDOR FINAL",
                identificacion_comprador=identificacion_comprador,
                direccion_comprador=direccion_comprador or None,
                detalles=detalles,
                pagos=[Pago(forma_pago=FORMA_PAGO[forma_pago_label], total=total)],
                ambiente=ambiente,
            )
            xml_bytes, clave = build_factura(datos)
            _procesar_comprobante(xml_bytes, clave, "Factura")

# --------------------------------------------------------------------
# TAB: RETENCIÓN
# --------------------------------------------------------------------
with tab_retencion:
    st.subheader("Datos del comprobante de retención")
    colr1, colr2 = st.columns(2)
    secuencial_r = colr1.text_input("Secuencial (9 dígitos)", value="000000001", key="sec_r")
    fecha_r = colr2.date_input("Fecha de emisión", value=date.today(), key="fecha_r")

    st.markdown("**Sujeto retenido**")
    colsr1, colsr2, colsr3 = st.columns(3)
    tipo_id_sr = colsr1.selectbox("Tipo de identificación", ["RUC", "Cédula", "Pasaporte"], key="tid_r")
    identificacion_sr = colsr2.text_input("Identificación", key="id_r")
    razon_social_sr = colsr3.text_input("Razón social / nombres", key="rs_r")
    periodo_fiscal = st.text_input("Período fiscal (mm/aaaa)", value=fecha_r.strftime("%m/%Y"), key="periodo_r")

    st.markdown("**Retenciones a aplicar**")
    if "retenciones" not in st.session_state:
        st.session_state.retenciones = []

    with st.form("form_retencion", clear_on_submit=True):
        r1, r2, r3 = st.columns(3)
        tipo_impuesto = r1.selectbox("Impuesto", ["RENTA", "IVA", "ISD"])
        base_imp = r2.number_input("Base imponible", min_value=0.0, value=0.0, step=0.01)
        pct_ret = r3.number_input("Porcentaje de retención (%)", min_value=0.0, value=0.0, step=0.5)
        cod_doc_sustento_label = st.selectbox("Documento sustento", ["Factura", "Nota de crédito", "Liquidación de compra"])
        num_doc_sustento = st.text_input("Nro. documento sustento (est-ptoEmi-secuencial)")
        agregar_r = st.form_submit_button("Agregar retención")
        if agregar_r:
            mapa_doc = {"Factura": "01", "Nota de crédito": "04", "Liquidación de compra": "03"}
            mapa_impuesto = {"RENTA": CODIGO_IMPUESTO_RETENCION["RENTA"],
                              "IVA": CODIGO_IMPUESTO_RETENCION["IVA"],
                              "ISD": CODIGO_IMPUESTO_RETENCION["ISD"]}
            valor_ret = round(base_imp * pct_ret / 100, 2)
            st.session_state.retenciones.append({
                "impuesto": mapa_impuesto[tipo_impuesto], "codigo_retencion": "1",
                "base": base_imp, "porcentaje": pct_ret, "valor": valor_ret,
                "cod_doc_sustento": mapa_doc[cod_doc_sustento_label],
                "num_doc_sustento": num_doc_sustento,
            })

    if st.session_state.retenciones:
        st.table(st.session_state.retenciones)
        if st.button("Vaciar retenciones", key="vaciar_r"):
            st.session_state.retenciones = []
            st.rerun()

    if st.button("Generar XML de Retención", type="primary"):
        errores = _validar_emisor()
        if not identificacion_sr:
            errores.append("Falta la identificación del sujeto retenido.")
        if not st.session_state.retenciones:
            errores.append("Agrega al menos una retención.")
        if errores:
            for e in errores:
                st.error(e)
        else:
            impuestos = [
                ImpuestoRetenido(
                    codigo=r["impuesto"], codigo_retencion=r["codigo_retencion"],
                    base_imponible=r["base"], porcentaje_retener=r["porcentaje"],
                    valor_retenido=r["valor"], cod_doc_sustento=r["cod_doc_sustento"],
                    fecha_emision_doc_sustento=fecha_r,
                    num_doc_sustento=r["num_doc_sustento"] or None,
                )
                for r in st.session_state.retenciones
            ]
            datos = DatosRetencion(
                emisor=_emisor_obj(), secuencial=secuencial_r.zfill(9), fecha_emision=fecha_r,
                tipo_identificacion_sujeto_retenido={"RUC": "04", "Cédula": "05", "Pasaporte": "06"}[tipo_id_sr],
                razon_social_sujeto_retenido=razon_social_sr,
                identificacion_sujeto_retenido=identificacion_sr,
                periodo_fiscal=periodo_fiscal, impuestos=impuestos, ambiente=ambiente,
            )
            xml_bytes, clave = build_retencion(datos)
            _procesar_comprobante(xml_bytes, clave, "Retención")

# --------------------------------------------------------------------
# TAB: GUÍA DE REMISIÓN
# --------------------------------------------------------------------
with tab_guia:
    st.subheader("Datos de la guía de remisión")
    colg1, colg2 = st.columns(2)
    secuencial_g = colg1.text_input("Secuencial (9 dígitos)", value="000000001", key="sec_g")
    placa = colg2.text_input("Placa del vehículo", key="placa_g")

    dir_partida = st.text_input("Dirección de partida", key="partida_g")
    colt1, colt2 = st.columns(2)
    fecha_ini = colt1.date_input("Fecha inicio de transporte", value=date.today(), key="fini_g")
    fecha_fin = colt2.date_input("Fecha fin de transporte", value=date.today(), key="ffin_g")

    st.markdown("**Transportista**")
    colr1, colr2, colr3 = st.columns(3)
    tipo_id_transp = colr1.selectbox("Tipo de identificación", ["RUC", "Cédula", "Pasaporte"], key="tid_g")
    ruc_transportista = colr2.text_input("Identificación transportista", key="ruc_transp")
    razon_transportista = colr3.text_input("Razón social transportista", key="rs_transp")

    st.markdown("**Destinatario**")
    cold1, cold2 = st.columns(2)
    id_destinatario = cold1.text_input("Identificación destinatario", key="id_dest")
    rs_destinatario = cold2.text_input("Razón social destinatario", key="rs_dest")
    dir_destinatario = st.text_input("Dirección destinatario", key="dir_dest")
    motivo_traslado = st.text_input("Motivo del traslado", key="motivo_g")

    st.markdown("**Mercadería transportada**")
    if "detalles_guia" not in st.session_state:
        st.session_state.detalles_guia = []
    with st.form("form_detalle_guia", clear_on_submit=True):
        dg1, dg2 = st.columns(2)
        desc_g = dg1.text_input("Descripción de la mercadería")
        cant_g = dg2.number_input("Cantidad", min_value=0.0, value=1.0, step=1.0)
        agregar_g = st.form_submit_button("Agregar mercadería")
        if agregar_g and desc_g:
            st.session_state.detalles_guia.append({"descripcion": desc_g, "cantidad": cant_g})

    if st.session_state.detalles_guia:
        st.table(st.session_state.detalles_guia)
        if st.button("Vaciar mercadería", key="vaciar_g"):
            st.session_state.detalles_guia = []
            st.rerun()

    if st.button("Generar XML de Guía de Remisión", type="primary"):
        errores = _validar_emisor()
        if not placa:
            errores.append("Falta la placa del vehículo.")
        if not dir_partida:
            errores.append("Falta la dirección de partida.")
        if not id_destinatario or not rs_destinatario:
            errores.append("Faltan datos del destinatario.")
        if not st.session_state.detalles_guia:
            errores.append("Agrega al menos un ítem de mercadería.")
        if errores:
            for e in errores:
                st.error(e)
        else:
            detalles_g = [
                DetalleGuia(descripcion=item["descripcion"], cantidad=item["cantidad"])
                for item in st.session_state.detalles_guia
            ]
            destinatario = Destinatario(
                identificacion=id_destinatario, razon_social=rs_destinatario,
                direccion=dir_destinatario, motivo_traslado=motivo_traslado or "Traslado de mercadería",
                detalles=detalles_g,
            )
            datos = DatosGuiaRemision(
                emisor=_emisor_obj(), secuencial=secuencial_g.zfill(9),
                dir_partida=dir_partida,
                razon_social_transportista=razon_transportista,
                tipo_identificacion_transportista={"RUC": "04", "Cédula": "05", "Pasaporte": "06"}[tipo_id_transp],
                ruc_transportista=ruc_transportista,
                fecha_ini_transporte=fecha_ini, fecha_fin_transporte=fecha_fin,
                placa=placa, destinatarios=[destinatario], ambiente=ambiente,
            )
            xml_bytes, clave = build_guia_remision(datos)
            _procesar_comprobante(xml_bytes, clave, "Guía de Remisión")

st.divider()
st.caption(
    "Recuerda: si vas a comercializar este software a terceros, el SRI exige "
    "registrar en tu RUC un establecimiento y actividad económica exclusiva "
    "(códigos CIIU J62021002 o J62021003), según la Resolución "
    "NAC-DGERCGC26-00000027 (julio 2026)."
)
