# Facturador Electrónico SRI (Ecuador)

App Streamlit que genera, firma (XAdES-BES) y envía al SRI comprobantes
electrónicos: **Factura**, **Comprobante de Retención** y **Guía de
Remisión**, siguiendo la Ficha Técnica de Comprobantes Electrónicos -
Esquema Off-line (versión 2.32 / 2.34).

## Instalación

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Estructura del proyecto

- `catalogos.py` — códigos de tablas del SRI (tipo de comprobante, IVA, ICE, retenciones, formas de pago).
- `clave_acceso.py` — genera la clave de acceso de 49 dígitos (módulo 11), verificada contra el ejemplo oficial de la ficha técnica.
- `xml_builder.py` — construye el XML de cada comprobante en el orden exacto de etiquetas exigido por el XSD del SRI.
- `xades_signer.py` — firma el XML con el estándar XAdES-BES (enveloped, RSA-SHA1) usando tu certificado `.p12`.
- `sri_client.py` — envía el XML firmado a los Web Services SOAP de Recepción y Autorización del SRI (ambientes de Pruebas y Producción).
- `app.py` — interfaz Streamlit.

## Antes de usar en producción — muy importante

1. **Prueba primero en el ambiente de PRUEBAS** (`celcer.sri.gob.ec`). Los
   comprobantes ahí NO tienen validez tributaria, pero te permiten
   verificar que tu XML y tu firma son aceptados por el SRI real.
2. **La firma XAdES-BES incluida es una implementación de referencia.**
   El validador del SRI es estricto con la canonicalización XML exacta.
   Si el SRI responde con el error 39 ("Firma inválida"), compara tu XML
   firmado contra uno generado por el Facturador SRI gratuito y ajusta lo
   que corresponda en `xades_signer.py`.
3. **Registro como proveedor de software (si vendes esta app a terceros).**
   Desde la Resolución NAC-DGERCGC26-00000027 (28 julio 2026), quien
   desarrolla o comercializa sistemas de facturación electrónica debe
   registrar en su RUC un establecimiento exclusivo con el código CIIU
   `J62021002` (desarrollo propio) o `J62021003` (comercialización de
   sistemas de terceros). Además, cada comprobante emitido con tu
   software deberá incluir el RUC del proveedor en la información
   adicional, conforme a la ficha técnica vigente (plazo: 60 días desde
   la publicación).
4. **Verifica los catálogos de impuestos antes de facturar en real.** Las
   tarifas de ICE y los porcentajes de retención de ISD cambian por
   decreto ejecutivo; los valores en `catalogos.py` corresponden a la
   ficha técnica de octubre 2025 / julio 2026, pero conviene
   contrastarlos contra la versión más reciente publicada en
   https://www.sri.gob.ec/facturacion-electronica
5. **Este código no reemplaza asesoría contable/tributaria.** No es
   asesoría legal ni fiscal; antes de operar en producción, valida el
   esquema completo con tu contador y, si el volumen lo justifica,
   considera contratar un PAC/OCA certificado para el envío al SRI.

## Flujo de uso en la app

1. En la barra lateral: completa los datos del emisor, elige el ambiente
   (Pruebas/Producción) y sube tu certificado `.p12` con su contraseña.
2. En la pestaña correspondiente (Factura / Retención / Guía de
   Remisión): completa el formulario y agrega las líneas de detalle.
3. "Generar XML" construye el comprobante sin firmar y calcula la clave
   de acceso.
4. "Firmar con XAdES-BES" firma el XML con tu certificado.
5. Si tienes marcado "Enviar automáticamente al SRI", la app envía el
   XML firmado al Web Service de Recepción y luego consulta el estado de
   Autorización, reintentando unas cuantas veces.

## Referencias oficiales

- Ficha Técnica de Comprobantes Electrónicos - Esquema Off-line:
  https://www.sri.gob.ec/facturacion-electronica
- Resolución NAC-DGERCGC26-00000027 (registro de proveedores de
  facturación electrónica).
