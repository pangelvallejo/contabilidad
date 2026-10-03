"""Formatos de presentación en español de Colombia."""
from datetime import date
from decimal import Decimal

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def pesos(valor, decimales=False, signo=True):
    if valor is None or valor == "":
        return ""
    valor = Decimal(str(valor))
    if not decimales and valor == valor.to_integral_value():
        texto = f"{abs(valor):,.0f}"
    else:
        texto = f"{abs(valor):,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    prefijo = "$ " if signo else ""
    return f"-{prefijo}{texto}" if valor < 0 else f"{prefijo}{texto}"


def numero(valor):
    return pesos(valor, signo=False)


def fecha(valor):
    if not valor:
        return ""
    if isinstance(valor, str):
        valor = date.fromisoformat(valor)
    return valor.strftime("%d/%m/%Y")


def fecha_larga(valor):
    return f"{valor.day} de {MESES[valor.month - 1]} de {valor.year}" if valor else ""


def entrada(valor):
    """Valor para un campo de formulario: 1274400.00 -> 1274400; 10.50 -> 10,50."""
    if isinstance(valor, Decimal):
        if valor == valor.to_integral_value():
            return str(int(valor))
        return str(valor).replace(".", ",")
    return "" if valor is None else valor
