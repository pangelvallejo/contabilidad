"""Informes gerenciales nuevos: flujo de efectivo, comparativos, CxP, rentabilidad, presupuesto, cotizaciones, correo."""
from datetime import date
from decimal import Decimal

from app import contab, informes
from app.models import (Asunto, Banco, CategoriaGasto, Cotizacion, DocumentoVenta, Gasto, LineaVenta, PagoGasto,
                        Tercero)


def _cliente(s, nit="900111222", email=None):
    t = Tercero(nit=nit, nombre="Cliente " + nit, es_cliente=True, email=email)
    s.add(t)
    s.flush()
    return t


def _factura(s, cliente, numero, base, fecha, asunto=None):
    doc = DocumentoVenta(tipo="FV", numero=numero, fecha=fecha, vencimiento=fecha, cliente=cliente, subtotal=base,
                         base_gravada=base, iva=base * Decimal("0.19"), total=base * Decimal("1.19"), asunto=asunto)
    doc.lineas.append(LineaVenta(descripcion="Honorarios", base=base, iva_pct=19, iva=doc.iva))
    s.add(doc)
    s.flush()
    contab.contabilizar_venta(s, doc)
    return doc


def _gasto(s, cat, total, fecha, forma="contado", banco=None, asunto=None, reembolsable=False, proveedor=None):
    g = Gasto(tipo_soporte="FE", numero=f"P{total}", fecha=fecha, vencimiento=fecha, categoria=cat, subtotal=total,
              iva=0, iva_descontable=False, total=total, forma_pago=forma, cuenta_pago=banco.cuenta if banco else None,
              asunto=asunto, reembolsable=reembolsable, proveedor=proveedor)
    s.add(g)
    s.flush()
    contab.contabilizar_gasto(s, g)
    return g


def _escenario(s):
    banco = s.query(Banco).first()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    prov = Tercero(nit="800999", nombre="Proveedor X", es_proveedor=True)
    s.add(prov)
    c = _cliente(s)
    asunto = Asunto(cliente=c, nombre="Proceso ABC")
    s.add(asunto)
    s.flush()
    f1 = _factura(s, c, "FV-1", Decimal("1000000"), date(2026, 2, 10), asunto)
    _gasto(s, cat, Decimal("200000"), date(2026, 2, 15), banco=banco, asunto=asunto, reembolsable=True)
    g2 = _gasto(s, cat, Decimal("300000"), date(2026, 3, 1), forma="credito", proveedor=prov)
    s.commit()
    return banco, cat, c, asunto, f1, g2, prov


def test_flujo_efectivo_cuadra_y_clasifica(cliente_web, s):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    r = cliente_web.post(f"/ventas/{f1.id}/pagar", data={"fecha": "2026-03-05", "banco_id": banco.id})
    assert r.status_code == 302
    fe = informes.flujo_de_efectivo(s, date(2026, 1, 1), date(2026, 12, 31))
    assert fe["cuadre"] == 0
    conceptos = {n: v for sec in fe["secciones"] for n, v in sec["filas"]}
    assert conceptos["Recaudos de clientes"] == Decimal("1190000")
    assert conceptos["Pagos a proveedores y gastos"] == Decimal("-200000")
    assert fe["saldo_final"] == Decimal("990000")
    assert cliente_web.get("/informes/flujo-efectivo?desde=2026-01-01&hasta=2026-12-31&pdf=1").status_code == 200
    html = cliente_web.get("/informes/flujo-efectivo?desde=2026-01-01&hasta=2026-12-31").get_data(as_text=True)
    assert "Recaudos de clientes" in html


def test_comparativo_mensual_e_indicadores(cliente_web, s):
    _escenario(s)
    c = informes.comparativo_resultados(s, date(2026, 1, 1), date(2026, 12, 31))
    ing = next(f for f in c["filas"] if f["concepto"] == "Ingresos operacionales")
    assert ing["actual"] == Decimal("1000000") and ing["anterior"] == 0 and ing["vertical_actual"] == Decimal("100.0")
    filas = informes.resultados_mensuales(s, 2026)
    ing = next(f for f in filas if f["concepto"] == "Ingresos operacionales")
    assert ing["meses"][1] == Decimal("1000000") and ing["total"] == Decimal("1000000")
    ind = {i["nombre"]: i["valor"] for i in informes.indicadores(s, 2026, date(2026, 3, 31))}
    assert ind["Margen operacional"] == Decimal("50.0")
    assert ind["Días promedio de cobro (DSO)"] is not None
    pat, total = informes.cambios_patrimonio(s, 2026)
    assert total["final"] == Decimal("500000")
    for r in ["/informes/comparativo?desde=2026-01-01&hasta=2026-12-31&xlsx=1", "/informes/resultados-mensual?anio=2026",
              "/informes/indicadores?anio=2026", "/informes/patrimonio?anio=2026&pdf=1"]:
        assert cliente_web.get(r).status_code == 200


def test_cuentas_por_pagar_y_gastos_proveedor(cliente_web, s):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    filas, tot = informes.cuentas_por_pagar_edades(s, date(2026, 4, 15))
    assert tot["total"] == Decimal("300000") and filas[0]["31-60"] == Decimal("300000")
    assert informes.cuentas_por_pagar_edades(s, date(2026, 2, 28))[1]["total"] == 0  # antes de la factura
    p = PagoGasto(fecha=date(2026, 3, 20), cuenta_pago=banco.cuenta, valor=Decimal("100000"))
    g2.pagos.append(p)
    s.flush()
    contab.contabilizar_pago_gasto(s, p)
    s.commit()
    assert informes.cuentas_por_pagar_edades(s, date(2026, 4, 15))[1]["total"] == Decimal("200000")
    assert informes.cuentas_por_pagar_edades(s, date(2026, 3, 10))[1]["total"] == Decimal("300000")  # pago posterior
    fp, total = informes.gastos_por_proveedor(s, date(2026, 1, 1), date(2026, 12, 31))
    assert total == Decimal("500000") and fp[0]["proveedor"].nit == "800999" and fp[0]["pct"] == Decimal("60.0")
    _, _, tcat = informes.gastos_por_categoria_mensual(s, 2026)
    assert tcat == Decimal("500000")
    for r in ["/informes/cuentas-por-pagar?corte=2026-04-15&pdf=1", f"/informes/proveedor/{prov.id}?pdf=1",
              "/informes/gastos-proveedor?xlsx=1", "/informes/gastos-categoria?anio=2026&xlsx=1"]:
        assert cliente_web.get(r).status_code == 200


def test_rentabilidad_asuntos_y_reembolsables(cliente_web, s):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    filas, tot = informes.rentabilidad(s, date(2026, 1, 1), date(2026, 12, 31))
    assert filas[0]["ingresos"] == Decimal("1000000") and filas[0]["gastos"] == Decimal("200000")
    assert filas[0]["margen_pct"] == Decimal("80.0") and filas[0]["reembolsables"] == Decimal("200000")
    assert filas[0]["asuntos"][0]["asunto"].id == asunto.id
    html = cliente_web.get("/informes/reembolsables").get_data(as_text=True)
    assert "Proceso ABC" in html
    g = s.query(Gasto).filter_by(reembolsable=True).one()
    cliente_web.post("/informes/reembolsables", data={"id": [str(g.id)]})
    assert s.get(Gasto, g.id).reembolsado is True
    assert informes.rentabilidad(s, date(2026, 1, 1), date(2026, 12, 31))[1]["reembolsables"] == 0
    # CRUD de asuntos y validación de cliente en la factura
    r = cliente_web.post("/informes/asuntos", data={"cliente_id": c.id, "nombre": "Consultoría", "referencia": "C-1",
                                                   "honorarios_pactados": "2.500.000"}, follow_redirects=True)
    assert "Asunto guardado" in r.get_data(as_text=True)
    nuevo = s.query(Asunto).filter_by(nombre="Consultoría").one()
    assert nuevo.honorarios_pactados == Decimal("2500000")
    otro = _cliente(s, "900333")
    s.commit()
    r = cliente_web.post(f"/ventas/{f1.id}", data={"accion": "guardar", "asunto_id": nuevo.id, "vencimiento": "2026-03-01"},
                         follow_redirects=True)
    assert "Cambios guardados" in r.get_data(as_text=True) and s.get(DocumentoVenta, f1.id).asunto_id == nuevo.id
    ajeno = Asunto(cliente=otro, nombre="De otro")
    s.add(ajeno)
    s.commit()
    r = cliente_web.post(f"/ventas/{f1.id}", data={"accion": "guardar", "asunto_id": ajeno.id}, follow_redirects=True)
    assert "mismo cliente" in r.get_data(as_text=True)
    r = cliente_web.post("/informes/asuntos", data={"id": nuevo.id, "accion": "borrar"}, follow_redirects=True)
    assert "tiene facturas" in r.get_data(as_text=True)
    assert cliente_web.get(f"/ventas?asunto={nuevo.id}").status_code == 200


def test_presupuesto_vs_real(cliente_web, s):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    r = cliente_web.post("/informes/presupuesto", data={"anio": "2026", "ingresos": "12.000.000", f"categoria:{cat.id}": "2.400.000"},
                         follow_redirects=True)
    assert "Presupuesto 2026 guardado" in r.get_data(as_text=True)
    pr = informes.presupuesto_vs_real(s, 2026, date(2026, 6, 30))
    ing = pr["filas"][0]
    assert ing["anual"] == Decimal("12000000") and ing["esperado"] == Decimal("6000000") and ing["real"] == Decimal("1000000")
    gasto = next(f for f in pr["filas"] if f["clave"] == f"categoria:{cat.id}")
    assert gasto["esperado"] == Decimal("1200000") and gasto["real"] == Decimal("500000")
    assert cliente_web.get("/informes/presupuesto?anio=2026&xlsx=1").status_code == 200


def test_regimenes_y_conciliacion_informe(cliente_web, s):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    cr = informes.comparar_regimenes(s, 2026, date(2026, 12, 31))
    assert cr["simple"] > 0 and cr["ica"] == contab.redondear(cr["ingresos"] * Decimal("9.66") / 1000, "1")
    r = cliente_web.post("/informes/regimenes?anio=2026", data={"ica_por_mil": "4,14"}, follow_redirects=True)
    assert "guardada" in r.get_data(as_text=True) and contab.config(s, "ica_por_mil") == "4.14"
    inf = informes.informe_conciliacion(s, banco, date(2026, 12, 31), Decimal("0"))
    # el gasto de contado está en libros y no en ningún extracto
    assert inf["saldo_libros"] == Decimal("-200000") and inf["total_en_libros"] == Decimal("-200000")
    assert inf["extracto_esperado"] == 0 and inf["diferencia"] == 0
    assert cliente_web.get(f"/informes/conciliacion?banco={banco.id}&saldo_extracto=0&pdf=1").status_code == 200


def test_cotizaciones_flujo_completo(cliente_web, s, monkeypatch):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    c.email = "cliente@ejemplo.com"
    s.commit()
    r = cliente_web.post("/cotizaciones/nueva", data={"fecha": "2026-04-01", "cliente_id": c.id, "asunto_id": asunto.id,
                                                      "titulo": "Asesoría", "validez_dias": "15",
                                                      "descripcion": ["Etapa 1", "Etapa 2", ""], "valor": ["1.000.000", "500.000", ""],
                                                      "iva_pct": ["19", "19", "19"]}, follow_redirects=True)
    assert "COT-0001 creada" in r.get_data(as_text=True)
    cot = s.query(Cotizacion).one()
    assert cot.subtotal == Decimal("1500000") and cot.total == Decimal("1785000") and cot.vence == date(2026, 4, 16)
    assert cliente_web.get(f"/cotizaciones/{cot.id}/pdf").status_code == 200
    enviados = []
    from app import correo
    monkeypatch.setattr(correo, "enviar", lambda session, para, asunto_, cuerpo, adjuntos=(): enviados.append((para, asunto_, adjuntos)))
    contab.set_config(s, "correo_usuario", "x@gmail.com")
    contab.set_config(s, "correo_clave", "clave")
    s.commit()
    r = cliente_web.post(f"/cotizaciones/{cot.id}", data={"accion": "enviar"}, follow_redirects=True)
    assert "enviada a cliente@ejemplo.com" in r.get_data(as_text=True)
    assert enviados[0][0] == "cliente@ejemplo.com" and enviados[0][2][0][0] == "COT-0001.pdf"
    assert s.get(Cotizacion, cot.id).estado == "enviada"
    cliente_web.post(f"/cotizaciones/{cot.id}", data={"accion": "aceptada"})
    r = cliente_web.post(f"/cotizaciones/{cot.id}", data={"accion": "facturada", "factura_id": f1.id}, follow_redirects=True)
    assert s.get(Cotizacion, cot.id).estado == "facturada" and s.get(Cotizacion, cot.id).factura_id == f1.id
    html = cliente_web.get("/cotizaciones/?anio=2026").get_data(as_text=True)
    assert "COT-0001" in html and "Facturada" in html
    r = cliente_web.post("/cotizaciones/nueva", data={"fecha": "2026-04-01", "cliente_id": c.id, "titulo": "Sin líneas",
                                                      "descripcion": [""], "valor": [""]}, follow_redirects=True)
    assert "al menos una línea" in r.get_data(as_text=True)


def test_envio_estado_cuenta_y_recordatorios(cliente_web, s, monkeypatch):
    banco, cat, c, asunto, f1, g2, prov = _escenario(s)
    c.email = "cliente@ejemplo.com"
    contab.set_config(s, "correo_usuario", "x@gmail.com")
    contab.set_config(s, "correo_clave", "clave")
    s.commit()
    enviados = []
    from app import correo
    monkeypatch.setattr(correo, "enviar", lambda session, para, asunto_, cuerpo, adjuntos=(): enviados.append((para, cuerpo, adjuntos)))
    r = cliente_web.post(f"/cartera/{c.id}/enviar?corte=2026-03-31", follow_redirects=True)
    assert "enviado a cliente@ejemplo.com" in r.get_data(as_text=True)
    assert "FV-1" in enviados[0][1] and enviados[0][2][0][2] == "application/pdf"
    r = cliente_web.post("/cartera/recordatorios?corte=2026-03-31", follow_redirects=True)
    assert "Recordatorio enviado a" in r.get_data(as_text=True) and len(enviados) == 2
    # resumen mensual
    from app import tareas
    texto = tareas.texto_resumen(s, 2026, 2)
    assert "Ingresos del mes" in texto and "1.000.000" in texto
    r = cliente_web.post("/configuracion/", data={"accion": ["empresa", "enviar_resumen"], "resumen_correo": "yo@ejemplo.com"},
                         follow_redirects=True)
    assert "enviado a yo@ejemplo.com" in r.get_data(as_text=True)


def test_smtp_arma_el_mensaje(s, monkeypatch):
    from app import correo
    contab.set_config(s, "correo_servidor", "gmail")
    contab.set_config(s, "correo_usuario", "x@gmail.com")
    contab.set_config(s, "correo_clave", "clave")
    s.commit()
    capturado = {}

    class SMTPFalso:
        def __init__(self, host, puerto, timeout=0):
            capturado["host"], capturado["puerto"] = host, puerto

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            capturado["login"] = (u, p)

        def send_message(self, msg):
            capturado["msg"] = msg

    monkeypatch.setattr(correo.smtplib, "SMTP_SSL", SMTPFalso)
    correo.enviar(s, "a@b.com", "Prueba ñ", "Cuerpo", [("x.pdf", b"%PDF", "application/pdf")])
    assert capturado["host"] == "smtp.gmail.com" and capturado["puerto"] == 465 and capturado["login"][0] == "x@gmail.com"
    msg = capturado["msg"]
    assert msg["To"] == "a@b.com" and msg["Bcc"] == "x@gmail.com" and "Prueba" in msg["Subject"]
    assert [p.get_filename() for p in msg.iter_attachments()] == ["x.pdf"]
    assert correo._smtp({"correo_smtp": "mail.firma.co:587", "correo_servidor": ""}) == ("mail.firma.co", 587, False)
