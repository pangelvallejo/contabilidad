"""Lectura del Formulario 2593 (recibo electrónico del SIMPLE) que descarga el portal de la DIAN.

El PDF lo genera la DIAN con iText: primero vienen todos los rótulos del formulario y después los valores
diligenciados, siempre en el mismo orden (casillas 1 a 81). Se leen los valores en ese orden y se validan con las
sumas del propio formulario; si algo no cuadra, el archivo se rechaza en vez de registrar cifras equivocadas.
"""
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

CERO = Decimal("0")

# Posición de cada casilla en la lista de valores de la página 1, contada desde la casilla 39.
CASILLAS = {39: 0, 40: 1, 41: 2, 42: 3, 43: 4, 44: 5, 45: 6, 46: 7, 47: 8, 48: 9, 49: 10, 50: 11, 51: 12, 52: 13,
            53: 14, 54: 15, 55: 16, 56: 17, 57: 18}
CASILLAS.update({n: 19 + (n - 58) for n in range(58, 80)})  # 58 a 79 consecutivas
MARCA = "nacional al consumo"  # último rótulo de la página 1: después vienen los valores


@dataclass
class Recibo2593:
    anio: int
    bimestre: int
    numero: str
    nit: str
    ingresos: Decimal          # casilla 39: ingresos brutos del bimestre
    tarifa: Decimal            # casilla 36: tarifa SIMPLE consolidada (%)
    anticipo: Decimal          # casilla 42: anticipo SIMPLE antes de descuentos
    componente_ica: Decimal    # casilla 76: ICA consolidado a pagar
    aportes_pension: Decimal   # descuento por aportes a pensión del empleador (casilla 44 - casilla 49)
    simple_a_pagar: Decimal    # casillas 77 + 78: anticipo SIMPLE nacional (y consumo) a pagar
    iva_a_pagar: Decimal       # casilla 79
    recibo_490: str | None = None
    fecha_pago: date | None = None
    advertencias: list = field(default_factory=list)

    @property
    def total(self):
        return self.componente_ica + self.simple_a_pagar + self.iva_a_pagar


def _numero(texto: str) -> Decimal:
    t = texto.strip().replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        raise ValueError(f"valor '{texto}' no numérico")
    return Decimal(t)


def texto_pdf(contenido: bytes) -> list[str]:
    try:
        import logging
        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)  # los PDF de la DIAN traen referencias cruzadas imperfectas
    except ImportError as e:  # instalación sin la librería: el usuario puede registrar el pago a mano
        raise ValueError("Falta la librería para leer PDF (pypdf). Actualice el programa o registre el pago a mano.") from e
    try:
        lector = PdfReader(io.BytesIO(contenido))
        return [p.extract_text() or "" for p in lector.pages]
    except Exception as e:  # noqa: BLE001 — PDF dañado o protegido
        raise ValueError("El archivo no se pudo abrir como PDF.") from e


def leer_2593(contenido: bytes) -> Recibo2593:
    """Interpreta el PDF del Formulario 2593. Lanza ValueError con un mensaje entendible si no lo es."""
    paginas = texto_pdf(contenido)
    if not paginas or "2593" not in "".join(paginas[:1]) + "".join(paginas):
        raise ValueError("No parece un Formulario 2593 de la DIAN.")
    p1 = paginas[0]
    if MARCA not in p1 or "Anticipo SIMPLE" not in p1:
        raise ValueError("No parece un Formulario 2593 (recibo electrónico del SIMPLE) descargado del portal de la DIAN.")
    valores = [v.strip() for v in p1.split(MARCA)[-1].split("\n") if v.strip()]
    try:
        cab = re.fullmatch(r"((?:\d\s*){4})\s+(\d)", valores[0])
        if not cab or not re.fullmatch(r"2593\d{10}", valores[1]):
            raise ValueError("encabezado")
        anio = int(re.sub(r"\s", "", cab.group(1)))
        bimestre = int(cab.group(2))
        numero = valores[1]
        nit = re.sub(r"\s", "", valores[2])
        tarifa = _numero(valores[14].split()[0])
        cifras = [_numero(v) for v in valores[15:15 + len(CASILLAS)]]
        c = {n: cifras[i] for n, i in CASILLAS.items()}
        resto = valores[15 + len(CASILLAS):]
    except (IndexError, ValueError) as e:
        raise ValueError("El PDF tiene la forma de un 2593 pero sus valores no se pudieron leer; registre el pago a mano.") from e
    if not 1 <= bimestre <= 6 or not 2019 <= anio <= 2100:
        raise ValueError("El año o el bimestre del formulario no son válidos.")
    # Comprobaciones con las sumas del propio formulario
    if c[42] != c[43] + c[44] or c[41] != c[39] - c[40]:
        raise ValueError("Las cifras leídas del 2593 no cuadran entre sí (casillas 39 a 44); registre el pago a mano.")
    if c[76] != c[43] or c[77] != c[56]:
        raise ValueError("Las cifras leídas del 2593 no cuadran con los valores a pagar (casillas 76 y 77).")
    r = Recibo2593(anio=anio, bimestre=bimestre, numero=numero, nit=nit, ingresos=c[39], tarifa=tarifa,
                   anticipo=c[42], componente_ica=c[76], aportes_pension=max(c[44] - c[49], CERO),
                   simple_a_pagar=c[77] + c[78], iva_a_pagar=c[79])
    # Casillas 80 y 81: número y fecha del recibo 490 con que se pagó (solo en el formulario ya pagado)
    m = re.search(r"\b(49\d{11})\s+((?:\d\s*){8})", " ".join(resto[:3]))
    if m:
        f = re.sub(r"\s", "", m.group(2))
        try:
            r.recibo_490, r.fecha_pago = m.group(1), date(int(f[:4]), int(f[4:6]), int(f[6:8]))
        except ValueError:
            r.recibo_490 = m.group(1)
    if r.fecha_pago is None and r.total > 0:
        r.advertencias.append("El formulario no trae la fecha de pago (casilla 81); confírmela.")
    return r
