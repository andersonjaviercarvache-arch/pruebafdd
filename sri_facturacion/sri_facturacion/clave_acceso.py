# -*- coding: utf-8 -*-
"""
Generación de la clave de acceso (49 dígitos) según Tabla 1 de la Ficha
Técnica de Comprobantes Electrónicos - Esquema Off-line.

Estructura (48 dígitos + 1 dígito verificador):
  1. Fecha de emisión        ddmmaaaa      (8)
  2. Tipo de comprobante     tabla 3       (2)
  3. Número de RUC           1234567890001 (13)
  4. Tipo de ambiente        tabla 4       (1)
  5. Serie (estab+ptoEmi)    001001        (6)
  6. Número comprobante      000000001     (9)
  7. Código numérico         numérico      (8)
  8. Tipo de emisión         tabla 2       (1)
  9. Dígito verificador (módulo 11)        (1)
"""
import random
import datetime


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
