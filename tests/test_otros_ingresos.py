"""Saldos por banco e ingresos sin factura (intereses, reintegros)."""
from datetime import date
from decimal import Decimal

from app import bancos, contab, exogena, impuestos
from app.models import Asiento, Banco, MovimientoBanco, OtroIngreso, Tercero


def test_saldo_por_banco_y_tablero(cliente_web, s):
    b1, b2 = s.query(Banco).order_by(Banco.id).limit(2).all()
    r = cliente_web.post("/bancos/ingresos/nuevo", data={"fecha": "2026-03-31", "banco_id": b1.id, "cuenta": "421005",
                                                        "concepto": "Intereses marzo", "valor": "12.345,67",
                                                        "descuentos": "49", "tercero_id": "nuevo",
                                                        "nuevo_nit": "860007335", "nuevo_nombre": "Banco Caja Social"},
                         follow_redirects=True)
    assert "Ingreso registrado" in r.get_data(as_text=True)
    oi = s.query(OtroIngreso).one()
    assert oi.neto == Decimal("12296.67") and oi.tercero.nit == "860007335"
    a = s.query(Asiento).filter_by(origen=f"otroingreso:{oi.id}").one()
    lineas = {(m.cuenta, m.debito, m.credito) for m in a.lineas}
    assert (b1.cuenta, Decimal("12296.67"), 0) in lineas
    assert (contab.CTA_GASTOS_BANCARIOS, Decimal("49"), 0) in lineas
    assert ("421005", 0, Decimal("12345.67")) in lineas
    filas, total = bancos.saldos_bancos(s, date(2026, 12, 31))
    saldos = {f["banco"].id: f["saldo"] for f in filas}
    assert saldos[b1.id] == Decimal("12296.67") and saldos[b2.id] == 0 and total == Decimal("12296.67")
    assert bancos.saldos_bancos(s, date(2026, 3, 30))[1] == 0  # antes de la fecha no hay saldo
    html = cliente_web.get("/bancos/?corte=2026-12-31").get_data(as_text=True)
    assert "Intereses marzo" in html and "12.296,67" in html
    html = cliente_web.get("/").get_data(as_text=True)
    assert "Saldo en bancos" in html
    html = cliente_web.get(f"/bancos/movimientos?banco={b1.id}&desde=2026-01-01&hasta=2026-12-31").get_data(as_text=True)
    assert "Intereses marzo" in html
    assert cliente_web.get(f"/bancos/movimientos?banco={b1.id}&desde=2026-01-01&hasta=2026-12-31&xlsx=1").status_code == 200
    assert cliente_web.get("/bancos/ingresos?anio=2026&xlsx=1").status_code == 200
    # Hace parte de los ingresos brutos del SIMPLE y de la exógena (concepto 4003), sin IVA
    assert impuestos.ingresos_brutos(s, date(2026, 1, 1), date(2026, 12, 31)) == Decimal("12345.67")
    assert impuestos.resumen_iva(s, date(2026, 1, 1), date(2026, 12, 31)).iva_generado_neto == 0
    hojas, incompletos = exogena.generar(s, 2026)
    filas_1007 = hojas["1007 Ingresos"][1]
    assert any(f[0] == "4003" and f[2] == "860007335" and f[-2] == Decimal("12345.67") for f in filas_1007)


def test_editar_eliminar_y_validaciones(cliente_web, s):
    b = s.query(Banco).first()
    r = cliente_web.post("/bancos/ingresos/nuevo", data={"fecha": "2026-02-10", "banco_id": b.id, "cuenta": "425050",
                                                        "concepto": "Reintegro", "valor": "100000", "descuentos": "100000"},
                         follow_redirects=True)
    assert "menores que el ingreso" in r.get_data(as_text=True) and s.query(OtroIngreso).count() == 0
    r = cliente_web.post("/bancos/ingresos/nuevo", data={"fecha": "2026-02-10", "banco_id": b.id, "cuenta": "530505",
                                                        "concepto": "x", "valor": "100"}, follow_redirects=True)
    assert "no es válida" in r.get_data(as_text=True)
    cliente_web.post("/bancos/ingresos/nuevo", data={"fecha": "2026-02-10", "banco_id": b.id, "cuenta": "425050",
                                                    "concepto": "Reintegro", "valor": "100000"})
    oi = s.query(OtroIngreso).one()
    assert oi.tercero_id is None
    hojas, incompletos = exogena.generar(s, 2026)
    assert any("sin tercero" in t.nombre for t, _ in incompletos)
    r = cliente_web.post(f"/bancos/ingresos/{oi.id}", data={"fecha": "2026-02-11", "banco_id": b.id, "cuenta": "425050",
                                                           "concepto": "Reintegro gastos", "valor": "150000"},
                         follow_redirects=True)
    assert "Ingreso actualizado" in r.get_data(as_text=True)
    a = s.query(Asiento).filter_by(origen=f"otroingreso:{oi.id}").one()
    assert a.fecha == date(2026, 2, 11) and sum(m.debito for m in a.lineas) == Decimal("150000")
    r = cliente_web.post(f"/bancos/ingresos/{oi.id}", data={"accion": "borrar"}, follow_redirects=True)
    assert "Ingreso eliminado" in r.get_data(as_text=True)
    assert s.query(OtroIngreso).count() == 0 and s.query(Asiento).filter_by(origen=f"otroingreso:{oi.id}").count() == 0
    # periodo bloqueado
    contab.set_config(s, "periodo_bloqueado_hasta", "2026-06-30")
    s.commit()
    r = cliente_web.post("/bancos/ingresos/nuevo", data={"fecha": "2026-02-10", "banco_id": b.id, "cuenta": "421005",
                                                        "concepto": "Intereses", "valor": "100"}, follow_redirects=True)
    assert "bloquead" in r.get_data(as_text=True).lower() and s.query(OtroIngreso).count() == 0


def test_conciliacion_crea_y_deshace_otro_ingreso(cliente_web, s):
    b = s.query(Banco).first()
    mov = MovimientoBanco(banco_id=b.id, fecha=date(2026, 4, 30), descripcion="ABONO INTERESES", valor=Decimal("5000"),
                         huella="h1", estado="pendiente")
    s.add(mov)
    s.commit()
    mid = mov.id
    cliente_web.post(f"/bancos/movimiento/{mid}", data={"accion": "ingreso", "cuenta": "421005"})
    mov = s.get(MovimientoBanco, mid)
    assert mov.estado == "conciliado" and mov.origen_tipo == "otroingreso"
    oi = s.get(OtroIngreso, mov.origen_id)
    assert oi.valor == Decimal("5000") and oi.concepto == "ABONO INTERESES"
    # un ingreso registrado a mano aparece como candidato para otra línea igual
    mov2 = MovimientoBanco(banco_id=b.id, fecha=date(2026, 5, 31), descripcion="ABONO INTERESES", valor=Decimal("7000"),
                          huella="h2", estado="pendiente")
    s.add(mov2)
    oi2 = OtroIngreso(fecha=date(2026, 5, 31), banco_id=b.id, cuenta="421005", concepto="Intereses mayo", valor=7000)
    s.add(oi2)
    s.commit()
    cands = bancos.candidatos(s, mov2)
    assert ("otroingreso", oi2.id) in {(t, i) for t, i, *_ in cands}
    # deshacer borra el ingreso creado desde la conciliación y su asiento
    cliente_web.post(f"/bancos/movimiento/{mid}", data={"accion": "pendiente"})
    assert s.get(MovimientoBanco, mid).estado == "pendiente"
    assert s.get(OtroIngreso, oi.id) is None and s.query(Asiento).filter_by(origen=f"otroingreso:{oi.id}").count() == 0
    # el préstamo del socio sigue siendo un asiento (cuenta de pasivo)
    cliente_web.post(f"/bancos/movimiento/{mid}", data={"accion": "ingreso", "cuenta": "235505"})
    assert s.get(MovimientoBanco, mid).origen_tipo == "asiento"
    assert s.query(Tercero).count() >= 0
