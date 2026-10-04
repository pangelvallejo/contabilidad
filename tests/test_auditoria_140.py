"""Hallazgos de la auditoría final de la 1.4.0."""
from datetime import date
from decimal import Decimal

from app import contab, exogena, informes, planeacion
from app.models import (Asunto, Banco, CategoriaGasto, Cotizacion, DocumentoVenta, Gasto, OtroIngreso,
                        PagoImpuesto, Tercero)
from tests.test_informes import _cliente, _escenario, _factura


def test_flujo_iva_de_compras_y_pagos_de_impuestos(s):
    banco = s.query(Banco).first()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    g = Gasto(tipo_soporte="FE", numero="X", fecha=date(2026, 2, 1), categoria=cat, subtotal=100000, iva=19000,
              iva_descontable=True, total=119000, forma_pago="contado", cuenta_pago=banco.cuenta)
    s.add(g)
    s.flush()
    contab.contabilizar_gasto(s, g)
    p = PagoImpuesto(formulario="2593", anio=2026, bimestre=1, fecha=date(2026, 3, 10), valor_simple=100000,
                     valor_iva=50000, banco_id=banco.id)
    s.add(p)
    s.flush()
    s.refresh(p)
    contab.contabilizar_pago_impuesto(s, p)
    s.commit()
    fe = informes.flujo_de_efectivo(s, date(2026, 1, 1), date(2026, 12, 31))
    conceptos = {n: v for sec in fe["secciones"] for n, v in sec["filas"]}
    assert conceptos["Pagos a proveedores y gastos (incluido su IVA)"] == Decimal("-119000")  # el IVA va con la compra
    assert conceptos["Impuestos pagados (recibos 2593, declaraciones)"] == Decimal("-150000")  # anticipo SIMPLE + IVA
    assert "Recaudos de clientes" not in conceptos and fe["cuadre"] == 0


def test_patrimonio_no_duplica_el_resultado_tras_el_cierre(s):
    c = _cliente(s)
    _factura(s, c, "FV-1", Decimal("1000000"), date(2026, 5, 1))
    s.commit()
    antes = informes.cambios_patrimonio(s, 2026)[1]["final"]
    planeacion.cerrar_anio(s, 2026)
    s.commit()
    despues = informes.cambios_patrimonio(s, 2026)[1]["final"]
    assert antes == despues == Decimal("1000000")


def test_entradas_raras_no_dan_500(cliente_web, s):
    for r in ["/informes/conciliacion?saldo_extracto=abc", "/informes/conciliacion?saldo_extracto=NaN",
              "/informes/flujo-efectivo?desde=0001-01-01&hasta=0001-12-31",
              "/informes/comparativo?desde=0001-01-01&hasta=0001-12-31", "/informes/comparativo-balance?corte=0001-01-01"]:
        assert cliente_web.get(r).status_code == 200, r
    c = _cliente(s)
    s.commit()
    r = cliente_web.post("/cotizaciones/nueva", data={"fecha": "2026-04-01", "cliente_id": c.id, "titulo": "T",
                                                      "validez_dias": "99999999999", "descripcion": ["A"], "valor": ["100"]},
                         follow_redirects=True)
    assert "entre 1 y 365" in r.get_data(as_text=True) and s.query(Cotizacion).count() == 0
    r = cliente_web.post("/cotizaciones/nueva", data={"fecha": "2026-04-01", "cliente_id": c.id, "titulo": "T",
                                                      "descripcion": ["A"], "valor": ["abc"]}, follow_redirects=True)
    assert "no es un número válido" in r.get_data(as_text=True)
    r = cliente_web.post("/cotizaciones/nueva", data={"accion": "enviada"}, follow_redirects=True)
    assert "Primero guarde" in r.get_data(as_text=True)
    r = cliente_web.post("/informes/asuntos", data={"id": "999", "accion": "borrar"}, follow_redirects=True)
    assert "ya no existe" in r.get_data(as_text=True)
    r = cliente_web.post("/informes/presupuesto", data={"anio": "abc", "ingresos": "1"}, follow_redirects=True)
    assert "año no es válido" in r.get_data(as_text=True)
    r = cliente_web.post("/informes/regimenes", data={"ica_por_mil": "-5"}, follow_redirects=True)
    assert "entre 0 y 100" in r.get_data(as_text=True)


def test_cotizacion_facturada_solo_con_factura_del_cliente_y_sin_editar(cliente_web, s):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    otro = _cliente(s, "900333")
    f_otro = _factura(s, otro, "FV-O", Decimal("100"), date(2026, 3, 1))
    s.commit()
    cliente_web.post("/cotizaciones/nueva", data={"fecha": "2026-04-01", "cliente_id": c.id, "asunto_id": asunto.id,
                                                  "titulo": "T", "descripcion": ["A"], "valor": ["100"]})
    cot = s.query(Cotizacion).one()
    cliente_web.post(f"/cotizaciones/{cot.id}", data={"accion": "enviada"})
    cliente_web.post(f"/cotizaciones/{cot.id}", data={"accion": "aceptada"})
    r = cliente_web.post(f"/cotizaciones/{cot.id}", data={"accion": "facturada", "factura_id": f_otro.id}, follow_redirects=True)
    assert "mismo cliente" in r.get_data(as_text=True) and s.get(DocumentoVenta, f_otro.id).asunto_id is None
    r = cliente_web.post(f"/cotizaciones/{cot.id}", data={"fecha": "2026-04-01", "cliente_id": c.id, "titulo": "Cambiado",
                                                           "descripcion": ["A"], "valor": ["100"]}, follow_redirects=True)
    assert "Reabrir" in r.get_data(as_text=True) and s.get(Cotizacion, cot.id).titulo == "T"


def test_nc_hereda_asunto_y_exogena_4001(cliente_web, s):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    r = cliente_web.post("/ventas/nueva", data={"tipo": "NC", "numero": "NC-1", "fecha": "2026-03-01", "cliente_id": c.id,
                                                "referencia_id": f1.id, "base": "100000", "iva": "19000"}, follow_redirects=True)
    assert "registrada" in r.get_data(as_text=True)
    nc = s.query(DocumentoVenta).filter_by(tipo="NC").one()
    assert nc.asunto_id == asunto.id
    filas, _ = informes.rentabilidad(s, date(2026, 1, 1), date(2026, 12, 31))
    assert filas[0]["asuntos"][0]["ingresos"] == Decimal("900000")
    oi = OtroIngreso(fecha=date(2026, 5, 1), banco_id=banco.id, cuenta="415595", concepto="x", valor=1000, tercero=prov)
    s.add(oi)
    s.commit()
    hojas, _ = exogena.generar(s, 2026)
    assert any(f[0] == "4001" and f[2] == "800999" for f in hojas["1007 Ingresos"][1])


def test_punto_de_indiferencia_coherente(s):
    c = _cliente(s)
    _factura(s, c, "FV-1", Decimal("10000000"), date(2026, 1, 15))
    s.commit()
    cr = informes.comparar_regimenes(s, 2026, date(2026, 12, 31))
    m = cr["margen_indiferencia"] / 100
    ordinario_en_m = Decimal("0.35") * (cr["ingresos"] * m - cr["ica"]) + cr["ica"]
    assert abs(ordinario_en_m - cr["simple"]) <= cr["ingresos"] * Decimal("0.35") * Decimal("0.001")  # redondeo a 0,1 %


def test_correo_valida_destinatario(s):
    from app import correo
    contab.set_config(s, "correo_usuario", "x@gmail.com")
    contab.set_config(s, "correo_clave", "k")
    s.commit()
    import pytest
    with pytest.raises(ValueError, match="no es un correo válido"):
        correo.enviar(s, "malo\\nBcc: otro@x.com", "A", "B")
    assert Asunto is not None and Tercero is not None
