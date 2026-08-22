# -*- coding: utf-8 -*-
"""
Firma XML bajo el estándar XAdES-BES (enveloped), según sección 6 de la
Ficha Técnica de Comprobantes Electrónicos:
  - Estándar de firma: XAdES-BES v1.3.2
  - Codificación: UTF-8
  - Tipo de firma: ENVELOPED (http://www.w3.org/2000/09/xmldsig#enveloped-signature)
  - Algoritmo de firmado: RSA-SHA1
  - Archivo de certificado: PKCS12 (.p12)

ADVERTENCIA IMPORTANTE
-----------------------
Esta es una implementación de referencia. El validador del SRI es
estricto con la canonicalización XML y el detalle exacto de las
"SignedProperties" de XAdES. Antes de usar en el AMBIENTE DE PRODUCCIÓN:

  1. Prueba exhaustivamente en el ambiente de PRUEBAS del SRI
     (https://celcer.sri.gob.ec) con comprobantes reales.
  2. Si el SRI rechaza la firma (error 39 "Firma inválida") compara el
     XML firmado contra un ejemplo firmado por el Facturador SRI gratuito
     o por una librería XAdES madura (ej. la librería Java oficial que
     usa el SRI: MITyCLibXADES) y ajusta el canonicalizador o el
     contenido de SignedProperties según haga falta.
  3. Considera usar una librería especializada en XAdES si tu volumen de
     facturación es alto (esta implementación no reemplaza un PAC / OCA
     certificado si necesitas soporte comercial).
"""
from __future__ import annotations
import base64
import hashlib
import uuid
from datetime import datetime, timezone
from lxml import etree
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
from cryptography import x509

DS_NS = "http://www.w3.org/2000/09/xmldsig#"
XADES_NS = "http://uri.etsi.org/01903/v1.3.2#"
C14N_ALGO = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ENVELOPED_ALGO = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
SHA1_ALGO = "http://www.w3.org/2000/09/xmldsig#sha1"
RSA_SHA1_ALGO = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
SIGNED_PROPS_TYPE = "http://uri.etsi.org/01903#SignedProperties"


def _c14n(node) -> bytes:
    return etree.tostring(node, method="c14n", exclusive=False, with_comments=False)


def _sha1_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data).digest()).decode("ascii")


def cargar_certificado_p12(p12_bytes: bytes, password: str):
    """Devuelve (private_key, certificate, additional_certs)."""
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

    parser = etree.XMLParser(remove_blank_text=False)
    root = etree.fromstring(xml_bytes, parser=parser)

    doc_id = "comprobante"
    root.set("id", doc_id) if not root.get("id") else None

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

    nsmap = {"ds": DS_NS, "etsi": XADES_NS}
    signature_el = etree.SubElement(root, f"{{{DS_NS}}}Signature", nsmap=nsmap, Id=sig_id)

    # ---- 1) Digest del documento completo (antes de añadir SignedInfo) ----
    # Se calcula sobre el documento tal como queda ANTES de insertar el
    # contenido de SignedInfo (enveloped: se excluye el propio nodo Signature
    # del cálculo mediante la transformada "enveloped-signature").
    doc_for_digest = etree.fromstring(etree.tostring(root))
    # quitar el nodo Signature (vacío) recién insertado, para simular la
    # transformada enveloped-signature
    sig_in_copy = doc_for_digest.find(f"{{{DS_NS}}}Signature")
    if sig_in_copy is not None:
        doc_for_digest.remove(sig_in_copy)
    doc_digest = _sha1_b64(_c14n(doc_for_digest))

    # ---- 2) Nodo Object con QualifyingProperties (XAdES SignedProperties) ----
    object_el = etree.SubElement(signature_el, f"{{{DS_NS}}}Object", Id=object_id)
    qp = etree.SubElement(object_el, f"{{{XADES_NS}}}QualifyingProperties",
                           Target=f"#{sig_id}")
    sp = etree.SubElement(qp, f"{{{XADES_NS}}}SignedProperties", Id=signed_props_id)
    ssp = etree.SubElement(sp, f"{{{XADES_NS}}}SignedSignatureProperties")
    etree.SubElement(ssp, f"{{{XADES_NS}}}SigningTime").text = signing_time
    sc = etree.SubElement(ssp, f"{{{XADES_NS}}}SigningCertificate")
    cert_node = etree.SubElement(sc, f"{{{XADES_NS}}}Cert")
    cd = etree.SubElement(cert_node, f"{{{XADES_NS}}}CertDigest")
    dm = etree.SubElement(cd, f"{{{DS_NS}}}DigestMethod", Algorithm=SHA1_ALGO)
    dv = etree.SubElement(cd, f"{{{DS_NS}}}DigestValue")
    dv.text = cert_digest_b64
    issuer_serial = etree.SubElement(cert_node, f"{{{XADES_NS}}}IssuerSerial")
    etree.SubElement(issuer_serial, f"{{{DS_NS}}}X509IssuerName").text = issuer_name
    etree.SubElement(issuer_serial, f"{{{DS_NS}}}X509SerialNumber").text = serial_number

    signed_props_digest = _sha1_b64(_c14n(sp))

    # ---- 3) SignedInfo ----
    signed_info = etree.Element(f"{{{DS_NS}}}SignedInfo", Id=signed_info_id, nsmap=nsmap)
    etree.SubElement(signed_info, f"{{{DS_NS}}}CanonicalizationMethod", Algorithm=C14N_ALGO)
    etree.SubElement(signed_info, f"{{{DS_NS}}}SignatureMethod", Algorithm=RSA_SHA1_ALGO)

    # Referencia 1: al documento completo (enveloped)
    ref1 = etree.SubElement(signed_info, f"{{{DS_NS}}}Reference",
                             Id=ref_doc_id, URI=f"#{doc_id}")
    transforms1 = etree.SubElement(ref1, f"{{{DS_NS}}}Transforms")
    etree.SubElement(transforms1, f"{{{DS_NS}}}Transform", Algorithm=ENVELOPED_ALGO)
    etree.SubElement(ref1, f"{{{DS_NS}}}DigestMethod", Algorithm=SHA1_ALGO)
    etree.SubElement(ref1, f"{{{DS_NS}}}DigestValue").text = doc_digest

    # Referencia 2: al certificado (KeyInfo)
    ref2 = etree.SubElement(signed_info, f"{{{DS_NS}}}Reference", URI=f"#{keyinfo_id}")
    etree.SubElement(ref2, f"{{{DS_NS}}}DigestMethod", Algorithm=SHA1_ALGO)
    # el digest de KeyInfo se calcula más abajo, una vez construido el nodo
    keyinfo_el_tmp = etree.Element(f"{{{DS_NS}}}KeyInfo", Id=keyinfo_id, nsmap=nsmap)
    x509_data = etree.SubElement(keyinfo_el_tmp, f"{{{DS_NS}}}X509Data")
    etree.SubElement(x509_data, f"{{{DS_NS}}}X509Certificate").text = cert_b64
    keyinfo_digest = _sha1_b64(_c14n(keyinfo_el_tmp))
    etree.SubElement(ref2, f"{{{DS_NS}}}DigestValue").text = keyinfo_digest

    # Referencia 3: a las SignedProperties (XAdES)
    ref3 = etree.SubElement(signed_info, f"{{{DS_NS}}}Reference",
                             Type=SIGNED_PROPS_TYPE, URI=f"#{signed_props_id}")
    etree.SubElement(ref3, f"{{{DS_NS}}}DigestMethod", Algorithm=SHA1_ALGO)
    etree.SubElement(ref3, f"{{{DS_NS}}}DigestValue").text = signed_props_digest

    # ---- 4) Firmar SignedInfo con RSA-SHA1 ----
    signed_info_canon = _c14n(signed_info)
    signature_bytes = private_key.sign(signed_info_canon, padding.PKCS1v15(), hashes.SHA1())
    signature_value_b64 = base64.b64encode(signature_bytes).decode("ascii")

    # ---- 5) Ensamblar el nodo Signature final en el orden correcto ----
    signature_el.insert(0, signed_info)
    sig_value_el = etree.Element(f"{{{DS_NS}}}SignatureValue", nsmap=nsmap)
    sig_value_el.text = signature_value_b64
    signature_el.insert(1, sig_value_el)

    keyinfo_el = etree.Element(f"{{{DS_NS}}}KeyInfo", Id=keyinfo_id, nsmap=nsmap)
    x509_data_final = etree.SubElement(keyinfo_el, f"{{{DS_NS}}}X509Data")
    etree.SubElement(x509_data_final, f"{{{DS_NS}}}X509Certificate").text = cert_b64
    signature_el.insert(2, keyinfo_el)
    # Object (con QualifyingProperties) ya está insertado como último hijo

    xml_firmado = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    return xml_firmado
