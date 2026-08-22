# -*- coding: utf-8 -*-
"""
Facturador Electrónico SRI - Ecuador
Genera, firma (XAdES-BES) y envía al SRI: Facturas, Comprobantes de
Retención y Guías de Remisión, siguiendo la Ficha Técnica de
Comprobantes Electrónicos - Esquema Off-line (v2.32/2.34).

Ejecutar con:  streamlit run app.py
"""
import streamlit as st
from datetime import date, datetime

import catalogos as cat
from xml_builder import (
    Emisor, DetalleFactura, ImpuestoLinea, Pago, DatosFactura,
    ImpuestoRetenido, DatosRetencion,
    DetalleGuia, Destinatario, DatosGuiaRemision,
    build_factura, build_retencion, build_guia_remision,
)
from xades_signer import firmar_xml
from sri_client import enviar_y_autorizar

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
        identificacion_comprador = cat.IDENTIFICACION_CONSUMIDOR_FINAL
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

    forma_pago_label = st.selectbox("Forma de pago", list(cat.FORMA_PAGO.keys()), key="fp_f")

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
                    codigo=cat.CODIGO_IMPUESTO["IVA"], codigo_porcentaje=item["cod_pct"],
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
                pagos=[Pago(forma_pago=cat.FORMA_PAGO[forma_pago_label], total=total)],
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
            mapa_impuesto = {"RENTA": cat.CODIGO_IMPUESTO_RETENCION["RENTA"],
                              "IVA": cat.CODIGO_IMPUESTO_RETENCION["IVA"],
                              "ISD": cat.CODIGO_IMPUESTO_RETENCION["ISD"]}
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
