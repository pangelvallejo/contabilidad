"""PDF de cotización: cada descripción con sus valores en la misma página (revisión adversarial)."""
import re, zlib
from datetime import date
from decimal import Decimal as D

from app import exportar
from app.models import Cotizacion, LineaCotizacion
from tests.test_informes import _cliente


def _paginas(pdf: bytes):
    textos = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            t = zlib.decompress(m.group(1)).decode("latin-1")
        except Exception:
            continue
        if "Tj" in t or "TJ" in t:
            textos.append(t)
    return textos


def _cot(s, n, palabras=25):
    c = _cliente(s)
    cot = Cotizacion(numero="COT-0001", fecha=date(2026, 4, 1), cliente_id=c.id, titulo="Propuesta")
    for i in range(n):
        cot.lineas.append(LineaCotizacion(descripcion=("Etapa %d: " % i) + "analisis detallado " * palabras, valor=D("1000000"), iva_pct=D("19")))
    s.add(cot); s.commit()
    return cot


def test_cada_descripcion_con_sus_valores_en_la_misma_pagina(s):
    cot = _cot(s, 30)
    pdf = exportar.cotizacion_pdf(exportar._empresa_dict(s), cot)
    paginas = _paginas(pdf)
    assert len(paginas) >= 2
    for i, p in enumerate(paginas):
        etapas = len(re.findall(r"Etapa \d+:", p))
        valores = p.count("1.000.000")  # el valor sin IVA de cada línea
        assert etapas == valores, (i, etapas, valores)
