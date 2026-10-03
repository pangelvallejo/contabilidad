"""Exportación a Excel y PDF (estado de cuenta)."""
import io
from datetime import date
from decimal import Decimal

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .formato import fecha, pesos

FORMATO_PESOS = '#,##0;[Red]-#,##0'


def excel(hojas: dict) -> bytes:
    """hojas: {nombre: (encabezados, filas)}."""
    wb = Workbook()
    wb.remove(wb.active)
    for nombre, (encabezados, filas) in hojas.items():
        ws = wb.create_sheet(nombre[:31])
        ws.append(encabezados)
        for celda in ws[1]:
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = PatternFill("solid", fgColor="1F3A5F")
            celda.alignment = Alignment(wrap_text=True, vertical="center")
        for fila in filas:
            ws.append([float(v) if isinstance(v, Decimal) else v for v in fila])
        for i, enc in enumerate(encabezados, start=1):
            letra = get_column_letter(i)
            ancho = max([len(str(enc))] + [len(str(f[i - 1])) for f in filas[:200] if i - 1 < len(f)])
            ws.column_dimensions[letra].width = min(max(10, ancho + 2), 60)
            for fila_celdas in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                c = fila_celdas[0]
                if isinstance(c.value, float):
                    c.number_format = FORMATO_PESOS
                elif isinstance(c.value, date):
                    c.number_format = "dd/mm/yyyy"
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _latin1(texto):
    texto = str(texto or "").replace("–", "-").replace("—", "-").replace("“", '"').replace("”", '"')
    return texto.encode("latin-1", "replace").decode("latin-1")


def estado_de_cuenta_pdf(empresa: dict, cliente, filas, total, corte: date) -> bytes:
    """filas: lista de dicts con numero, fecha, vencimiento, total, reteiva, abonos, saldo, dias."""
    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 7, _latin1(empresa["nombre"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 9)
    pdf.cell(0, 5, _latin1(f"NIT {empresa['nit']}  {empresa.get('direccion') or ''}  {empresa.get('ciudad') or ''}"),
             new_x="LMARGIN", new_y="NEXT")
    if empresa.get("email") or empresa.get("telefono"):
        pdf.cell(0, 5, _latin1(f"{empresa.get('email') or ''}  {empresa.get('telefono') or ''}"),
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 7, "ESTADO DE CUENTA", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, _latin1(f"Cliente: {cliente.nombre}   NIT {cliente.nit_completo}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _latin1(f"Fecha de corte: {fecha(corte)}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    columnas = [("Factura", 24, "L"), ("Fecha", 21, "C"), ("Vence", 21, "C"), ("Valor", 28, "R"),
                ("ReteIVA", 22, "R"), ("Abonos y NC", 28, "R"), ("Saldo", 28, "R"), ("Días venc.", 18, "R")]
    pdf.set_font("helvetica", "B", 8)
    pdf.set_fill_color(31, 58, 95)
    pdf.set_text_color(255)
    for titulo, ancho, al in columnas:
        pdf.cell(ancho, 7, _latin1(titulo), border=0, align=al, fill=True)
    pdf.ln()
    pdf.set_text_color(0)
    pdf.set_font("helvetica", "", 8)
    for i, f in enumerate(filas):
        pdf.set_fill_color(242, 245, 249)
        valores = [f["numero"], fecha(f["fecha"]), fecha(f["vencimiento"]), pesos(f["total"]),
                   pesos(f["reteiva"]), pesos(f["abonos"]), pesos(f["saldo"]),
                   str(f["dias"]) if f["dias"] > 0 else "-"]
        for (titulo, ancho, al), v in zip(columnas, valores):
            pdf.cell(ancho, 6, _latin1(v), align=al, fill=i % 2 == 0)
        pdf.ln()
    pdf.set_font("helvetica", "B", 10)
    pdf.ln(2)
    pdf.cell(sum(c[1] for c in columnas[:6]), 7, "TOTAL A PAGAR", align="R")
    pdf.cell(columnas[6][1], 7, _latin1(pesos(total)), align="R")
    pdf.ln(12)
    pdf.set_font("helvetica", "", 8)
    pdf.multi_cell(0, 4, _latin1(
        "Si ya realizó el pago, por favor haga caso omiso de este estado de cuenta y envíenos el soporte. "
        "Si nos practicó retención de IVA, le agradecemos remitir el certificado correspondiente."))
    return bytes(pdf.output())
