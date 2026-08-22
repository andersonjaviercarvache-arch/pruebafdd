# -*- coding: utf-8 -*-
"""
Catálogos de códigos del SRI, tomados de la Ficha Técnica de Comprobantes
Electrónicos - Esquema Off-line (versión 2.32, actualizada octubre 2025,
y confirmados vigentes en la versión 2.34 de julio 2026).

IMPORTANTE: las tarifas de ICE y los porcentajes de retención de ISD
cambian con relativa frecuencia por decreto ejecutivo. Antes de facturar
en PRODUCCIÓN, verifica estos valores contra la ficha técnica vigente en
https://www.sri.gob.ec/facturacion-electronica
"""

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
