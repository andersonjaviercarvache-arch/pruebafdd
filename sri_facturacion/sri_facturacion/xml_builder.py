# -*- coding: utf-8 -*-
"""
Construcción de los XML de Factura, Comprobante de Retención y Guía de
Remisión, siguiendo el orden exacto de etiquetas del ANEXO 1 (versión
1.0.0) de la Ficha Técnica de Comprobantes Electrónicos - Esquema Off-line.

El orden de las etiquetas importa: el esquema XSD del SRI es una secuencia
(xs:sequence), así que un XML con las etiquetas en otro orden será
rechazado con el error 35 "Documento inválido".

Estos builders generan el XML SIN FIRMAR. La firma XAdES-BES se aplica
después, con xades_signer.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from lxml import etree
from typing import Optional

from clave_acceso import generar_clave_acceso
import catalogos as cat


def _fmt(n: float) -> str:
    """Formato numérico estándar SRI: punto decimal, 2 decimales."""
    return f"{float(n):.2f}"


def _fecha(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _sub(parent, tag, text=None, **attrib):
    el = etree.SubElement(parent, tag, **attrib) if attrib else etree.SubElement(parent, tag)
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
    establecimiento: str          # 3 dígitos, ej "001"
    punto_emision: str            # 3 dígitos, ej "001"
    nombre_comercial: Optional[str] = None
    dir_establecimiento: Optional[str] = None
    contribuyente_especial: Optional[str] = None   # nro resolución, si aplica
    obligado_contabilidad: Optional[bool] = None    # None si no aplica


@dataclass
class ImpuestoLinea:
    codigo: str             # tabla 16 (CODIGO_IMPUESTO)
    codigo_porcentaje: str   # tabla 17 (IVA) o tabla 18 (ICE) según impuesto
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
    forma_pago: str          # tabla 24
    total: float
    plazo: Optional[int] = None
    unidad_tiempo: Optional[str] = None


@dataclass
class DatosFactura:
    emisor: Emisor
    secuencial: str                    # 9 dígitos
    fecha_emision: date
    tipo_identificacion_comprador: str  # tabla 6
    razon_social_comprador: str
    identificacion_comprador: str
    detalles: list[DetalleFactura]
    pagos: list[Pago]
    ambiente: str = "1"                 # "1" pruebas, "2" producción
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
    codigo: str                 # tabla 19
    codigo_retencion: str        # tabla 20
    base_imponible: float
    porcentaje_retener: float
    valor_retenido: float
    cod_doc_sustento: str         # tabla 3
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
    periodo_fiscal: str            # mm/aaaa
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
    _sub(it, "tipoEmision", cat.TIPO_EMISION["NORMAL"])
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
    """Devuelve (xml_bytes, clave_acceso)."""
    clave = generar_clave_acceso(
        fecha_emision=d.fecha_emision,
        tipo_comprobante=cat.TIPO_COMPROBANTE["FACTURA"],
        ruc=d.emisor.ruc,
        ambiente=d.ambiente,
        establecimiento=d.emisor.establecimiento,
        punto_emision=d.emisor.punto_emision,
        secuencial=d.secuencial,
        codigo_numerico=d.codigo_numerico,
    )

    root = etree.Element("factura", id="comprobante", version="1.0.0")
    _info_tributaria(root, d.emisor, d.ambiente, cat.TIPO_COMPROBANTE["FACTURA"],
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

    # Agrupar impuestos por (codigo, codigoPorcentaje) para el resumen totalConImpuestos
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

    xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=None)
    return xml_bytes, clave


def build_retencion(d: DatosRetencion) -> tuple[bytes, str]:
    clave = generar_clave_acceso(
        fecha_emision=d.fecha_emision,
        tipo_comprobante=cat.TIPO_COMPROBANTE["RETENCION"],
        ruc=d.emisor.ruc,
        ambiente=d.ambiente,
        establecimiento=d.emisor.establecimiento,
        punto_emision=d.emisor.punto_emision,
        secuencial=d.secuencial,
        codigo_numerico=d.codigo_numerico,
    )

    root = etree.Element("comprobanteRetencion", id="comprobante", version="1.0.0")
    _info_tributaria(root, d.emisor, d.ambiente, cat.TIPO_COMPROBANTE["RETENCION"],
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

    xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=None)
    return xml_bytes, clave


def build_guia_remision(d: DatosGuiaRemision) -> tuple[bytes, str]:
    clave = generar_clave_acceso(
        fecha_emision=d.fecha_ini_transporte,
        tipo_comprobante=cat.TIPO_COMPROBANTE["GUIA_REMISION"],
        ruc=d.emisor.ruc,
        ambiente=d.ambiente,
        establecimiento=d.emisor.establecimiento,
        punto_emision=d.emisor.punto_emision,
        secuencial=d.secuencial,
        codigo_numerico=d.codigo_numerico,
    )

    root = etree.Element("guiaRemision", id="comprobante", version="1.0.0")
    _info_tributaria(root, d.emisor, d.ambiente, cat.TIPO_COMPROBANTE["GUIA_REMISION"],
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

    xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=None)
    return xml_bytes, clave
