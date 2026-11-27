"""Cuentas en participación (ingresos certificados por el socio gestor) y lectura del Formulario 2593 en PDF."""
import io
import re
from datetime import date
from decimal import Decimal as D

import pytest

from recibo_2593_sintetico import pdf_2593


def _contrato(s, nit="900111222"):
    from app.models import ContratoParticipacion, Tercero
    g = Tercero(nit=nit, nombre="GESTOR DE PRUEBA S.A.S.", tipo_doc="31")
    s.add(g)
    s.flush()
    k = ContratoParticipacion(nombre="Contrato de prueba", gestor=g, porcentaje=D("40"))
    s.add(k)
    s.commit()
    return k


def _banco(s):
    from app.models import Banco
    return s.query(Banco).order_by(Banco.id).first()


def _saldo(s, cuenta, corte=date(2026, 12, 31)):
    from app import reportes
    return reportes.saldo_cuenta(s, cuenta, corte)


# ------------------------------------------------------------------ cuentas en participación

def test_certificacion_reconoce_ingreso_sin_iva_y_suma_al_simple(cliente_web, s):
    from app import exogena, impuestos
    from app.models import CertificacionParticipacion
    k = _contrato(s)
    r = cliente_web.post("/participacion/certificacion/nueva", data={
        "contrato_id": k.id, "fecha": "2026-02-28", "periodo_desde": "2026-01-01", "periodo_hasta": "2026-02-28",
        "ingresos_contrato": "75.000.000", "porcentaje": "40", "valor": "30.000.000", "referencia": "CERT-1"})
    assert r.status_code == 302
    c = s.query(CertificacionParticipacion).one()
    assert c.valor == D("30000000") and c.saldo == D("30000000")
    assert _saldo(s, "138010") == D("30000000")
    assert _saldo(s, "415510") == D("-30000000")  # naturaleza crédito
    # SIMPLE: hace parte de los ingresos brutos; IVA: no genera
    assert impuestos.ingresos_brutos(s, date(2026, 1, 1), date(2026, 2, 28)) == D("30000000")
    assert impuestos.resumen_iva(s, date(2026, 1, 1), date(2026, 2, 28)).iva_generado_neto == 0
    hojas, _ = exogena.generar(s, 2026)
    assert any(f[0] == "4001" and f[2] == "900111222" and D("30000000") in [D(str(x)) for x in f if isinstance(x, (int, D))]
               for f in hojas["1007 Ingresos"][1])


def test_pagos_del_gestor_y_validaciones(cliente_web, s):
    from app.models import CertificacionParticipacion
    k = _contrato(s)
    cliente_web.post("/participacion/certificacion/nueva", data={"contrato_id": k.id, "fecha": "2026-04-30",
                                                                 "valor": "10.000.000"})
    c = s.query(CertificacionParticipacion).one()
    b = _banco(s)
    # pago mayor que el saldo: rechazado
    cliente_web.post(f"/participacion/certificacion/{c.id}", data={"accion": "pago", "pago_fecha": "2026-05-10",
                                                                    "banco_id": b.id, "pago_valor": "12.000.000"})
    c = s.get(CertificacionParticipacion, c.id)
    assert not c.pagos
    cliente_web.post(f"/participacion/certificacion/{c.id}", data={"accion": "pago", "pago_fecha": "2026-05-10",
                                                                    "banco_id": b.id, "pago_valor": "6.000.000"})
    c = s.get(CertificacionParticipacion, c.id)
    assert c.cobrado == D("6000000") and c.saldo == D("4000000")
    assert _saldo(s, b.cuenta) == D("6000000") and _saldo(s, "138010") == D("4000000")
    # no se puede bajar el valor por debajo de lo cobrado ni borrar con pagos
    cliente_web.post(f"/participacion/certificacion/{c.id}", data={"contrato_id": k.id, "fecha": "2026-04-30",
                                                                    "valor": "5.000.000"})
    cliente_web.post(f"/participacion/certificacion/{c.id}", data={"accion": "borrar"})
    c = s.get(CertificacionParticipacion, c.id)
    assert c.valor == D("10000000")
    # borrar el pago y luego la certificación deja la contabilidad en cero
    cliente_web.post(f"/participacion/certificacion/{c.id}", data={"accion": "borrar_pago", "pago_id": c.pagos[0].id})
    cliente_web.post(f"/participacion/certificacion/{c.id}", data={"accion": "borrar"})
    s.expire_all()
    assert s.query(CertificacionParticipacion).count() == 0
    assert _saldo(s, "138010") == 0 and _saldo(s, "415510") == 0 and _saldo(s, b.cuenta) == 0


def test_base_caja_cuenta_lo_transferido_por_el_gestor(s):
    from app import contab, impuestos
    from app.models import CertificacionParticipacion, PagoParticipacion
    k = _contrato(s)
    c = CertificacionParticipacion(contrato=k, fecha=date(2026, 2, 28), valor=D("20000000"))
    s.add(c)
    s.flush()
    contab.contabilizar_certificacion_participacion(s, c)
    p = PagoParticipacion(fecha=date(2026, 3, 15), banco_id=_banco(s).id, valor=D("8000000"))
    c.pagos.append(p)
    s.flush()
    contab.contabilizar_pago_participacion(s, p)
    contab.set_config(s, "simple_base", "caja")
    s.commit()
    assert impuestos.ingresos_brutos(s, date(2026, 1, 1), date(2026, 2, 28)) == 0
    assert impuestos.ingresos_brutos(s, date(2026, 3, 1), date(2026, 4, 30)) == D("8000000")


def test_conciliacion_crea_y_deshace_el_pago_del_gestor(cliente_web, s):
    from app import contab
    from app.models import CertificacionParticipacion, MovimientoBanco, PagoParticipacion
    k = _contrato(s)
    c = CertificacionParticipacion(contrato=k, fecha=date(2026, 6, 30), valor=D("7500000"))
    s.add(c)
    s.flush()
    contab.contabilizar_certificacion_participacion(s, c)
    b = _banco(s)
    mov = MovimientoBanco(banco_id=b.id, fecha=date(2026, 7, 5), descripcion="TRANSF GESTOR", valor=D("7500000"),
                          huella="p1")
    s.add(mov)
    s.commit()
    assert "Pago cuentas en participación" in cliente_web.get(f"/bancos/conciliacion?banco={b.id}").get_data(as_text=True)
    cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "participacion", "certificacion_id": c.id})
    mov, c = s.get(MovimientoBanco, mov.id), s.get(CertificacionParticipacion, c.id)
    assert mov.estado == "conciliado" and mov.origen_tipo == "pagoparticipacion"
    assert c.saldo == 0 and _saldo(s, "138010") == 0
    cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "pendiente"})
    mov = s.get(MovimientoBanco, mov.id)
    assert mov.estado == "pendiente" and s.query(PagoParticipacion).count() == 0 and _saldo(s, "138010") == D("7500000")


def test_pantallas_de_participacion(cliente_web, s):
    k = _contrato(s)
    cliente_web.post("/participacion/certificacion/nueva", data={"contrato_id": k.id, "fecha": "2026-02-28",
                                                                 "valor": "1.000.000"})
    for ruta in ("/participacion/", "/participacion/?xlsx=1", f"/participacion/contrato/{k.id}",
                 "/participacion/contrato/nuevo", "/participacion/certificacion/1", "/participacion/certificacion/nueva",
                 "/", "/contabilidad/paquete"):
        assert cliente_web.get(ruta).status_code == 200, ruta
    # el contrato con certificaciones no se puede borrar
    cliente_web.post(f"/participacion/contrato/{k.id}", data={"accion": "borrar"})
    from app.models import ContratoParticipacion
    assert s.query(ContratoParticipacion).count() == 1


def test_sin_contrato_lleva_a_crearlo(cliente_web):
    r = cliente_web.get("/participacion/certificacion/nueva")
    assert r.status_code == 302 and "/participacion/contrato" in r.headers["Location"]
    r = cliente_web.post("/participacion/contrato/nuevo", data={"nombre": "AM", "gestor_id": "nuevo",
                                                                 "nuevo_nit": "900222333", "nuevo_nombre": "GESTOR"})
    assert r.status_code == 302


# ------------------------------------------------------------------ Formulario 2593 en PDF

def test_lee_el_formulario_2593():
    from app import recibos_dian
    r = recibos_dian.leer_2593(pdf_2593())
    assert (r.anio, r.bimestre, r.numero) == (2026, 1, "25930000000017")
    assert r.ingresos == D("30000000") and r.tarifa == D("5.9") and r.anticipo == D("1770000")
    assert r.componente_ica == D("900000") and r.aportes_pension == D("100000") and r.simple_a_pagar == D("770000")
    assert r.total == D("1670000") and r.fecha_pago == date(2026, 5, 21) and r.recibo_490 == "4911000000017"
    sin_pago = recibos_dian.leer_2593(pdf_2593(recibo_490=None))
    assert sin_pago.fecha_pago is None and sin_pago.advertencias


def test_rechaza_pdf_que_no_es_2593():
    from fpdf import FPDF
    from app import recibos_dian
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=10)
    pdf.cell(0, 5, "Factura electronica de venta")
    with pytest.raises(ValueError):
        recibos_dian.leer_2593(bytes(pdf.output()))
    with pytest.raises(ValueError):
        recibos_dian.leer_2593(b"no es un pdf")


def _leer(cliente_web, pdfs):
    datos = {"accion": "leer", "pdf": [(io.BytesIO(p), f"r{i}.pdf") for i, p in enumerate(pdfs)]}
    html = cliente_web.post("/impuestos/recibos", data=datos, content_type="multipart/form-data").get_data(as_text=True)
    filas = re.findall(r'name="fila" value="(\d+)"><input type="hidden" name="archivo_\d+" value="([^"]+)"', html)
    fechas = dict(re.findall(r'name="fecha_(\d+)" value="([^"]*)"', html))
    ingresos = dict(re.findall(r'name="ingreso_(\d+)" value="([^"]*)"', html))
    return html, filas, fechas, ingresos


def _registrar(cliente_web, s, filas, fechas, ingresos, **extra):
    form = {"accion": "registrar", "banco_id": str(_banco(s).id), "contrato_id": "nuevo", "gestor_nit": "900333444",
            "gestor_nombre": "AM DE PRUEBA S.A.S.", "fila": [i for i, _ in filas]}
    for i, a in filas:
        form.update({f"archivo_{i}": a, f"fecha_{i}": fechas.get(i, ""), f"pagar_{i}": "on", f"reconocer_{i}": "on",
                     f"ingreso_{i}": ingresos.get(i, "0")})
    form.update(extra)
    return cliente_web.post("/impuestos/recibos", data=form, follow_redirects=True).get_data(as_text=True)


def test_adjuntar_2593_registra_pago_gasto_e_ingreso(cliente_web, s):
    from app import contab, impuestos
    from app.models import CertificacionParticipacion, PagoImpuesto
    pdfs = [pdf_2593(),
            pdf_2593(bimestre=2, numero="25930000000024", ingresos=90_000_000, tarifa="7.3", ica=2_700_000,
                     pension=500_000, recibo_490="4911000000024", fecha_pago="20260616")]
    html, filas, fechas, ingresos = _leer(cliente_web, pdfs)
    assert len(filas) == 2 and "Enero-Febrero" in html
    assert fechas == {"0": "2026-05-21", "1": "2026-06-16"}
    assert ingresos["0"] in ("30000000", "30000000.00", "30.000.000")
    _registrar(cliente_web, s, filas, fechas, ingresos)
    s.expire_all()
    pagos = s.query(PagoImpuesto).order_by(PagoImpuesto.bimestre).all()
    assert [(p.bimestre, p.fecha, p.total) for p in pagos] == [(1, date(2026, 5, 21), D("1670000")),
                                                               (2, date(2026, 6, 16), D("6070000"))]
    assert pagos[0].ingresos_declarados == D("30000000") and pagos[0].aportes_pension == D("100000")
    assert pagos[0].archivo and pagos[0].recibo_490 == "4911000000017"
    certs = s.query(CertificacionParticipacion).order_by(CertificacionParticipacion.fecha).all()
    assert [(c.fecha, c.valor) for c in certs] == [(date(2026, 2, 28), D("30000000")), (date(2026, 4, 30), D("90000000"))]
    assert certs[0].contrato.gestor.nit == "900333444"
    # gasto por impuesto reconocido al pagar; ingresos del SIMPLE iguales a los declarados
    assert _saldo(s, "540505") == D("7740000") and _saldo(s, "135595") == 0
    assert impuestos.ingresos_brutos(s, date(2026, 1, 1), date(2026, 2, 28)) == D("30000000")
    assert contab.periodo_bloqueado_hasta(s) == date(2026, 4, 30)
    # adjuntarlos otra vez no duplica nada
    html, filas, fechas, ingresos = _leer(cliente_web, pdfs)
    assert html.count("ya registrado") == 2 and html.count("ya reconocido") == 2
    _registrar(cliente_web, s, filas, fechas, ingresos, mover_bloqueo="on")
    s.expire_all()
    assert s.query(PagoImpuesto).count() == 2 and s.query(CertificacionParticipacion).count() == 2


def test_adjuntar_2593_respeta_el_bloqueo(cliente_web, s):
    from app import contab
    from app.models import PagoImpuesto
    contab.set_config(s, "periodo_bloqueado_hasta", "2026-06-30")
    s.commit()
    html, filas, fechas, ingresos = _leer(cliente_web, [pdf_2593()])
    assert "Permitir registrar en bimestres bloqueados" in html
    html = _registrar(cliente_web, s, filas, fechas, ingresos)
    assert "No se registró nada" in html
    s.expire_all()
    assert s.query(PagoImpuesto).count() == 0
    _registrar(cliente_web, s, filas, fechas, ingresos, mover_bloqueo="on")
    s.expire_all()
    assert s.query(PagoImpuesto).count() == 1
    assert contab.periodo_bloqueado_hasta(s) == date(2026, 6, 30)  # vuelve a quedar como estaba


def test_ingreso_sugerido_descuenta_lo_facturado(cliente_web, s):
    from app import contab
    from app.models import CertificacionParticipacion
    k = _contrato(s)
    c = CertificacionParticipacion(contrato=k, fecha=date(2026, 1, 31), valor=D("12000000"))
    s.add(c)
    s.flush()
    contab.contabilizar_certificacion_participacion(s, c)
    s.commit()
    _, _, _, ingresos = _leer(cliente_web, [pdf_2593()])
    assert ingresos["0"].replace(".", "").split(",")[0] in ("18000000", "1800000000")


def test_formulario_en_cero_cumple_el_bimestre(cliente_web, s):
    from app.models import PagoImpuesto
    html, filas, fechas, ingresos = _leer(cliente_web, [pdf_2593(anio=2026, bimestre=3, numero="25930000000031",
                                                                 ingresos=0, ica=0, pension=0, recibo_490=None)])
    assert "en cero" in html
    _registrar(cliente_web, s, filas, fechas, ingresos)
    s.expire_all()
    p = s.query(PagoImpuesto).one()
    assert p.total == 0 and p.archivo


def test_causacion_anual_ajusta_el_gasto_ya_reconocido(s):
    from app import contab, impuestos
    from app.models import CertificacionParticipacion, PagoImpuesto
    k = _contrato(s)
    c = CertificacionParticipacion(contrato=k, fecha=date(2026, 2, 28), valor=D("30000000"))
    s.add(c)
    s.flush()
    contab.contabilizar_certificacion_participacion(s, c)
    p = PagoImpuesto(formulario="2593", anio=2026, bimestre=1, fecha=date(2026, 5, 21), valor_simple=D("1670000"),
                     valor_iva=0, banco_id=_banco(s).id, aportes_pension=D("100000"))
    s.add(p)
    s.flush()
    contab.contabilizar_pago_impuesto(s, p)
    assert _saldo(s, "540505") == D("1670000")
    dec = impuestos.causar_simple_anual(s, 2026)
    assert dec.descuento_pension == D("100000")
    assert _saldo(s, "540505") == dec.impuesto_neto
    assert abs(_saldo(s, "240405")) == dec.impuesto_neto - D("1670000")


# ------------------------------------------------------------------ regresiones de la revisión

def test_pago_antiguo_conserva_la_cuenta_de_anticipo_al_recontabilizar(s):
    from app import contab
    from app.models import PagoImpuesto
    b = _banco(s)
    p = PagoImpuesto(formulario="2593", anio=2025, bimestre=3, fecha=date(2025, 7, 15), valor_simple=D("500000"),
                     valor_iva=0, banco_id=b.id)
    s.add(p)
    s.flush()
    # asiento como lo dejaba la versión 1.5
    contab.guardar_asiento(s, origen=f"impuesto:{p.id}", tipo="IM", fecha=p.fecha, descripcion="2593", tercero_id=None,
                           lineas=[("135595", p.valor_simple, 0, None, None), (b.cuenta, 0, p.valor_simple, None, None)])
    contab.contabilizar_pago_impuesto(s, p)
    assert _saldo(s, "135595", date(2025, 12, 31)) == D("500000") and _saldo(s, "540505", date(2025, 12, 31)) == 0


def test_base_caja_no_duplica_ingreso_ya_certificado(cliente_web, s):
    from app import contab
    from app.models import CertificacionParticipacion
    k = _contrato(s)
    contab.set_config(s, "simple_base", "caja")
    c = CertificacionParticipacion(contrato=k, fecha=date(2026, 2, 15), valor=D("30000000"), referencia="CERT-1")
    s.add(c)
    s.flush()
    contab.contabilizar_certificacion_participacion(s, c)
    s.commit()
    html, filas, fechas, ingresos = _leer(cliente_web, [pdf_2593()])
    assert ingresos["0"].replace(".", "").split(",")[0] in ("0", "000")
    html = _registrar(cliente_web, s, filas, fechas, {"0": "30.000.000"})
    assert "No se registró nada" in html
    assert s.query(CertificacionParticipacion).count() == 1


def test_dos_formularios_del_mismo_bimestre_no_se_marcan_ambos(cliente_web, s):
    html, filas, _, ingresos = _leer(cliente_web, [pdf_2593(), pdf_2593(numero="25930000000099", recibo_490="4911000000099")])
    assert len(filas) == 2 and "otro formulario del mismo bimestre" in html
    assert html.count('name="pagar_') == 2 and html.count("checked> registrar") == 1


def test_completar_pago_conserva_banco_y_fecha(cliente_web, s):
    from app import contab
    from app.models import Banco, PagoImpuesto
    otro = s.query(Banco).order_by(Banco.id.desc()).first()
    p = PagoImpuesto(formulario="2593", anio=2026, bimestre=1, fecha=date(2026, 5, 20), valor_simple=D("1670000"),
                     valor_iva=0, banco_id=otro.id)
    s.add(p)
    s.flush()
    contab.contabilizar_pago_impuesto(s, p)
    s.commit()
    html, filas, fechas, ingresos = _leer(cliente_web, [pdf_2593()])
    assert "completar pago" in html
    _registrar(cliente_web, s, filas, fechas, ingresos)
    p = s.get(PagoImpuesto, p.id)
    assert p.banco_id == otro.id and p.fecha == date(2026, 5, 20) and p.numero_formulario == "25930000000017"
    assert s.query(PagoImpuesto).count() == 1


def test_porcentaje_con_tres_decimales(cliente_web, s):
    from app.models import ContratoParticipacion
    k = _contrato(s)
    for _ in range(2):  # se guarda y se vuelve a guardar con lo que muestra el formulario
        k = s.get(ContratoParticipacion, k.id)
        pct = (str(k.porcentaje) if k.porcentaje != D("40") else "33,333").rstrip("0").rstrip(".")
        cliente_web.post(f"/participacion/contrato/{k.id}", data={"nombre": k.nombre, "gestor_id": k.gestor_id,
                                                                  "porcentaje": pct, "activo": "on"})
    assert s.get(ContratoParticipacion, k.id).porcentaje == D("33.333")


def test_por_cobrar_al_corte(s):
    from app import contab
    from app.models import CertificacionParticipacion, PagoParticipacion
    from app.routes.participacion import resumen
    k = _contrato(s)
    c = CertificacionParticipacion(contrato=k, fecha=date(2026, 12, 31), valor=D("10000000"))
    s.add(c)
    s.flush()
    contab.contabilizar_certificacion_participacion(s, c)
    c.pagos.append(PagoParticipacion(fecha=date(2027, 1, 10), banco_id=_banco(s).id, valor=D("10000000")))
    s.flush()
    assert resumen(s, date(2026, 1, 1), date(2026, 12, 31))["por_cobrar"] == D("10000000")
    assert resumen(s, date(2027, 1, 1), date(2027, 12, 31))["por_cobrar"] == 0
