"""PDF sintético con la estructura del Formulario 2593 de la DIAN (rótulos y luego valores, casillas 1 a 81).
Los formularios reales del usuario no se incluyen en el repositorio."""
from fpdf import FPDF


def _m(v):
    return f"{int(v):,}"


def pdf_2593(anio=2026, bimestre=1, numero="25930000000017", nit="9019135774", ingresos=52_000_000, tarifa="5.9",
             ica=1_560_000, pension=210_000, iva=0, recibo_490="4911000000017", fecha_pago="20260521"):
    anticipo = round(ingresos * float(tarifa) / 100 / 1000) * 1000
    nacional = anticipo - ica
    neto = nacional - pension
    c = {39: ingresos, 40: 0, 41: ingresos, 42: anticipo, 43: ica, 44: nacional, 45: pension, 46: 0, 47: pension,
         48: 0, 49: neto, 50: 0, 51: 0, 52: 0, 53: neto, 54: 0, 55: 0, 56: neto, 57: 0}
    c.update({n: 0 for n in range(58, 80)})
    c[74], c[76], c[77], c[79] = iva, ica, neto, iva
    lineas = ["Recibo electronico del SIMPLE 2593", "Anticipo SIMPLE", "Valor del anticipo componente SIMPLE nacional",
              "ICA municipios o distritos", "Liquidacion impuesto", "nacional al consumo",
              f"{' '.join(str(anio))}  {bimestre}", numero, " ".join(nit), "EMPRESA DE PRUEBA S.A.S 3 2"]
    lineas += ["0"] * 10 + [f"{tarifa} 0 0"] + [_m(c[n]) for n in range(39, 80)]
    lineas += [f"{recibo_490} {' '.join(fecha_pago)}" if recibo_490 else "0", "PRIVADA", "2 0 2 6 1 2 3 4"]
    pdf = FPDF()
    pdf.set_font("helvetica", size=8)
    pdf.add_page()
    for linea in lineas:
        pdf.cell(0, 4, linea, new_x="LMARGIN", new_y="NEXT")
    pdf.add_page()
    pdf.cell(0, 4, "Liquidacion del componente ICA consolidado 2593")
    return bytes(pdf.output())
