"""Hallazgos de la revisión adversarial completa de la 1.4.5 (versión 1.4.6)."""
import email
import io
import os
import stat
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart

from app import bancos, config, contab, correo, exogena, exportar, importacion, impuestos, informes, sistema
from app.models import (Asiento, Banco, CategoriaGasto, Cuenta, DocumentoVenta, Gasto, MovimientoBanco, PagoGasto,
                        PagoImpuesto, Recaudo, Tercero)
from app.reportes import saldo_cuenta
from tests.test_contabilidad import ARRENDADOR, GRAN_CONTRIBUYENTE, _escenario, cuadra, importar
from tests.test_informes import _cliente, _factura, _gasto
from ubl import factura_compra, factura_venta

D = Decimal


# ------------------------------------------------------------------ reteIVA: cada pago reclasifica lo que falte

def test_reteiva_posterior_la_reclasifica_el_pago_complementario(cliente_web, s):
    _escenario(s)
    banco = s.query(Banco).first()
    p1 = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=date(2026, 11, 19), valor_simple=D("885000"),
                      valor_iva=D("2328000"), banco=banco)
    s.add(p1)
    s.flush()
    contab.contabilizar_pago_impuesto(s, p1)
    s.commit()
    contab.set_config(s, "periodo_bloqueado_hasta", "")
    importar(s, "3.xml", factura_venta("ALC-3", "2026-10-20", GRAN_CONTRIBUYENTE, 1_000_000, reteiva=28500))
    s.commit()
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == D("28500")  # la retención nueva queda pendiente
    p2 = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=date(2026, 11, 25), valor_simple=D("10000"),
                      valor_iva=D("5000"), banco=banco)
    s.add(p2)
    s.flush()
    contab.contabilizar_pago_impuesto(s, p2)
    s.commit()
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == 0  # el complementario aplica solo los 28.500 que faltaban
    a2 = s.query(Asiento).filter_by(origen=f"impuesto:{p2.id}").one()
    assert [m.credito for m in a2.lineas if m.cuenta == contab.CTA_RETEIVA] == [D("28500")]
    # Eliminar el complementario no toca el principal: los 28.500 quedan pendientes para el próximo pago
    p2id = p2.id
    cliente_web.post(f"/impuestos/pago/{p2id}/eliminar", follow_redirects=True)
    s.expire_all()
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == D("28500")
    cuadra(s)


# ------------------------------------------------------------------ formularios y mensajes

def test_explicar_unique_y_tope_redondeado(cliente_web, s):
    from app.routes import explicar
    from sqlalchemy.exc import IntegrityError
    e = IntegrityError("INSERT INTO documentos_venta ...", {}, Exception("UNIQUE constraint failed: documentos_venta.cufe"))
    assert explicar(e) == "Ya existe un registro con ese mismo dato (cufe)."
    assert "sqlalche" not in explicar(e)
    c = _cliente(s)
    s.commit()
    r = cliente_web.post("/ventas/nueva", data={"numero": "FV-1", "fecha": "2026-03-05", "cliente_id": c.id,
                                                "base": "9.999.999.999.999,999", "iva": "0"}, follow_redirects=True)
    assert "demasiado grande" in r.get_data(as_text=True) and s.query(DocumentoVenta).count() == 0


def test_nc_por_el_total_de_una_factura_con_reteiva(cliente_web, s):
    s.add(Tercero(nit=GRAN_CONTRIBUYENTE[0], dv=GRAN_CONTRIBUYENTE[1], nombre=GRAN_CONTRIBUYENTE[2], es_cliente=True,
                  aplica_reteiva=True))
    s.commit()
    importar(s, "f.xml", factura_venta("ALC-9", "2026-03-01", GRAN_CONTRIBUYENTE, 1_000_000, reteiva=28500))
    f = s.query(DocumentoVenta).one()
    assert f.reteiva_aplica
    r = cliente_web.post("/ventas/nueva", data={"tipo": "NC", "numero": "NC-9", "fecha": "2026-03-05", "cliente_id": f.cliente_id,
                                                "base": "1.000.000", "iva": "190.000", "referencia_id": f.id}, follow_redirects=True)
    assert "supera el saldo" not in r.get_data(as_text=True)
    assert s.query(DocumentoVenta).filter_by(tipo="NC").count() == 1


def test_pago_de_gasto_valida_la_cuenta(cliente_web, s):
    prov = Tercero(nit="800111222", nombre="Proveedor", es_proveedor=True)
    s.add(prov)
    s.flush()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    g = _gasto(s, cat, D("500000"), date(2026, 3, 1), forma="credito", proveedor=prov)
    s.commit()
    gid = g.id
    for cuenta in ("2205", "130505", "999999", ""):
        r = cliente_web.post(f"/gastos/{gid}", data={"accion": "pago", "pago_valor": "100.000", "pago_fecha": "2026-03-10",
                                                     "pago_cuenta": cuenta}, follow_redirects=True)
        assert "Elija la cuenta" in r.get_data(as_text=True), cuenta
    assert s.query(PagoGasto).count() == 0
    r = cliente_web.post(f"/gastos/{gid}", data={"accion": "pago", "pago_valor": "100.000", "pago_fecha": "2026-03-10",
                                                 "pago_cuenta": s.query(Banco).first().cuenta}, follow_redirects=True)
    assert "Pago registrado" in r.get_data(as_text=True)


def test_nc_de_proveedor_sobre_factura_pagada_se_registra_de_contado(cliente_web, s):
    prov = Tercero(nit="800111222", nombre="Proveedor", es_proveedor=True)
    s.add(prov)
    s.flush()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    banco = s.query(Banco).first()
    g = _gasto(s, cat, D("500000"), date(2026, 3, 1), forma="credito", proveedor=prov)
    p = PagoGasto(fecha=date(2026, 3, 5), cuenta_pago=banco.cuenta, valor=D("500000"))
    g.pagos.append(p)
    s.flush()
    contab.contabilizar_pago_gasto(s, p)
    s.commit()
    gid, pid, cid = g.id, prov.id, cat.id
    r = cliente_web.post("/gastos/nuevo", data={"tipo_soporte": "NC", "fecha": "2026-03-20", "categoria_id": cid, "proveedor_id": pid,
                                                "subtotal": "200.000", "iva": "0", "forma_pago": "contado",
                                                "cuenta_pago": banco.cuenta, "referencia_id": gid}, follow_redirects=True)
    assert "Gasto registrado" in r.get_data(as_text=True)
    nc = s.query(Gasto).filter_by(tipo_soporte="NC").one()
    assert nc.forma_pago == "contado" and nc.cuenta_pago == banco.cuenta  # reintegro al banco, no a la cuenta por pagar
    assert s.get(Gasto, gid).saldo == D("-200000") or saldo_cuenta(s, "2205") == 0
    cuadra(s)


def test_retainer_invalido_conserva_lo_escrito(cliente_web, s):
    r = cliente_web.post("/terceros/nuevo", data={"nit": "900555666", "nombre": "Nuevo Cliente SAS", "email": "x@y.co",
                                                  "es_cliente": "on", "retainer_mensual": "abc"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "no es un número válido" in html and "ConversionSyntax" not in html
    assert 'value="Nuevo Cliente SAS"' in html and 'value="x@y.co"' in html
    assert s.query(Tercero).filter_by(nit="900555666").count() == 0


def test_editar_gasto_en_periodo_bloqueado_sin_tocar_el_asiento(cliente_web, s):
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    banco = s.query(Banco).first()
    g = _gasto(s, cat, D("119000"), date(2026, 3, 10), banco=banco)
    s.commit()
    contab.set_config(s, "periodo_bloqueado_hasta", "2026-04-30")
    s.commit()
    gid, cid = g.id, cat.id
    base = {"tipo_soporte": "FE", "numero": "P119000", "fecha": "2026-03-10", "categoria_id": cid, "forma_pago": "contado",
            "cuenta_pago": banco.cuenta}
    # mismo asiento (IVA no descontable), distinto reparto base/IVA: efecto en la exógena -> rechazado
    r = cliente_web.post(f"/gastos/{gid}", data={**base, "subtotal": "100.000", "iva": "19.000"}, follow_redirects=True)
    assert "bloqueado" in r.get_data(as_text=True) and s.get(Gasto, gid).subtotal == D("119000")
    # dejarlo en cero y moverlo al periodo bloqueado: rechazado
    r = cliente_web.post(f"/gastos/{gid}", data={**base, "fecha": "2026-03-05", "subtotal": "0", "iva": "0"}, follow_redirects=True)
    assert "bloqueado" in r.get_data(as_text=True) and s.get(Gasto, gid).fecha == date(2026, 3, 10)
    # solo la descripción: permitido
    r = cliente_web.post(f"/gastos/{gid}", data={**base, "subtotal": "119.000", "iva": "0", "descripcion": "Taxi"}, follow_redirects=True)
    assert "Cambios guardados" in r.get_data(as_text=True) and s.get(Gasto, gid).descripcion == "Taxi"


# ------------------------------------------------------------------ bancos

def test_naturaleza_no_invierte_valores_ya_firmados_y_nan_se_omite():
    filas = bancos.leer_tabla("e.csv", "Fecha;Descripcion;Valor;Tipo\n05/03/2026;PAGO PSE CLIENTE;1.500.000;Pago\n06/03/2026;RETIRO;-200.000;Retiro\n".encode())
    enc, mapa = bancos.detectar_columnas(filas)
    assert [l.valor for l in bancos.extraer(filas, mapa, enc)[0]] == [D("1500000"), D("-200000")]
    filas = bancos.leer_tabla("e.csv", "Fecha;Descripcion;Valor\n05/03/2026;VACIA;nan\n06/03/2026;ABONO;100\n".encode())
    enc, mapa = bancos.detectar_columnas(filas)
    lineas, omitidas = bancos.extraer(filas, mapa, enc)
    assert [l.valor for l in lineas] == [D("100")] and omitidas == 1


def test_cuenta_de_ingreso_desactivada_se_conserva_al_editar(cliente_web, s):
    b = s.query(Banco).first()
    oi_cuenta = "429595"
    assert s.get(Cuenta, oi_cuenta) is not None
    r = cliente_web.post("/bancos/ingresos/nuevo", data={"fecha": "2026-04-30", "banco_id": b.id, "cuenta": oi_cuenta,
                                                         "concepto": "Reintegro", "valor": "9.000", "descuentos": "0"}, follow_redirects=True)
    assert "Ingreso registrado" in r.get_data(as_text=True)
    from app.models import OtroIngreso
    oi = s.query(OtroIngreso).one()
    s.get(Cuenta, oi_cuenta).activa = False
    s.commit()
    html = cliente_web.get(f"/bancos/ingresos/{oi.id}").get_data(as_text=True)
    assert f'value="{oi_cuenta}" selected' in html or f'value="{oi_cuenta}"' in html


# ------------------------------------------------------------------ importación y correo

def test_existe_distingue_tipo_y_buscar_tercero_no_recorta_cedulas(s):
    importar(s, "f.xml", factura_compra("1001", "2026-03-01", ARRENDADOR, 100000, 19000, cufe="cufe-a"))
    res = importar(s, "nc.xml", factura_compra("1001", "2026-03-01", ARRENDADOR, 50000, 9500, cufe="cufe-b", tipo="CreditNote",
                                               referencia="1001"))
    assert res[0].ok, res[0].mensaje  # misma numeración para FV y NC el mismo día: no es un duplicado
    # Cédula de 10 dígitos con DV digitado por error: no se confunde con un NIT de 9 dígitos
    s.add(Tercero(nit="1020304057", dv="7", tipo_doc="13", nombre="Juan Pérez", es_proveedor=True))
    s.commit()
    assert importacion.buscar_tercero(s, "102030405") is None
    s.add(Tercero(nit="900.123.456-7", dv="7", tipo_doc="31", nombre="Con puntos", es_proveedor=True))
    s.add(Tercero(nit="8001112223", dv="3", tipo_doc="31", nombre="Con DV pegado", es_proveedor=True))
    s.commit()
    assert importacion.buscar_tercero(s, "900123456").nombre == "Con puntos"
    assert importacion.buscar_tercero(s, "800111222").nombre == "Con DV pegado"


def test_mensaje_nc_sin_factura_no_se_repite(s):
    res = importar(s, "nc.xml", factura_compra("NCX-1", "2026-03-01", ARRENDADOR, 10000, 1900, tipo="CreditNote"))
    assert res[0].ok and res[0].mensaje.count("sin factura asociada") == 1


def _mensaje(adjuntos):
    msg = MIMEMultipart()
    msg["Subject"] = "Facturas"
    for nombre, contenido in adjuntos:
        parte = MIMEApplication(contenido, _subtype=nombre.rsplit(".", 1)[-1])
        parte.add_header("Content-Disposition", "attachment", filename=nombre)
        msg.attach(parte)
    return email.message_from_bytes(msg.as_bytes())


def test_correo_nombres_repetidos_y_pdf_con_zip(s):
    x1 = factura_compra("INM-1", "2026-03-01", ARRENDADOR, 100000, 19000).encode()
    x2 = factura_compra("INM-2", "2026-03-02", ARRENDADOR, 200000, 38000).encode()
    adj = correo._adjuntos(_mensaje([("documento.xml", x1), ("documento.xml", x2), ("documento.pdf", b"%PDF-1.4 x")]))
    assert len(adj) == 1
    nombres = zipfile.ZipFile(io.BytesIO(adj[0][1])).namelist()
    assert len(nombres) == 3 and len(set(nombres)) == 3
    res = importacion.importar_archivo(s, *adj[0], origen="correo")
    assert sorted(r.ok for r in res) == [True, True]
    # Factura.zip (solo XML) + Factura.pdf suelto: el PDF queda como soporte
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fv3.xml", factura_compra("INM-3", "2026-03-03", ARRENDADOR, 300000, 57000).encode())
    adj = correo._adjuntos(_mensaje([("Factura.zip", buf.getvalue()), ("fv3.pdf", b"%PDF-1.4 y")]))
    assert len(adj) == 1 and adj[0][0] == "Factura.zip"
    res = importacion.importar_archivo(s, *adj[0], origen="correo")
    assert res[0].ok and s.query(Gasto).filter_by(numero="INM-3").one().soporte_archivo


def test_correo_cabeceras_por_lotes_y_fallos_no_bloquean(monkeypatch, s):
    llamadas = []

    class Imap:
        def select(self, *a, **k):
            return "OK", [b"1"]

        def uid(self, orden, *args):
            llamadas.append((orden, args[0] if args else None))
            if orden == "search":
                return "OK", [b" ".join(str(i).encode() for i in range(1, 61))]
            uid = args[0]
            if isinstance(uid, str):
                partes = []
                for u in uid.split(","):
                    partes.append((f"1 (UID {u} BODY[HEADER.FIELDS (MESSAGE-ID)] {{0}}".encode(), f"Message-ID: <m{u}@x>\r\n\r\n".encode()))
                    partes.append(b")")
                return "OK", partes
            if int(uid.decode()) <= 50:
                return "NO", [None]  # el cuerpo de los 50 primeros no se puede descargar
            return "OK", [(b"1 (BODY[] {0}", f"Message-ID: <m{uid.decode()}@x>\r\nSubject: s\r\n\r\n".encode()), b")"]

        def logout(self):
            pass

    monkeypatch.setattr(correo, "conectar", lambda session: (Imap(), {"correo_carpeta": "INBOX", "correo_dias": "30", "correo_filtro": ""}))
    res = correo.revisar(s, limite=50)
    assert res.error is None
    assert sum(1 for o, a in llamadas if o == "fetch" and isinstance(a, str)) == 1  # un solo FETCH de cabeceras
    from app.models import CorreoProcesado
    assert s.query(CorreoProcesado).filter(CorreoProcesado.resultado.like("No se pudo descargar%")).count() == 50
    res = correo.revisar(s, limite=50)
    assert res.revisados == 10  # los 10 mensajes sanos se procesan en la segunda revisión
    llamadas.clear()
    correo.revisar(s, limite=50)
    assert sum(1 for o, a in llamadas if o == "fetch") == 1  # ya todo procesado: solo el lote de cabeceras


# ------------------------------------------------------------------ informes, exógena y PDF

def test_exogena_y_cxp_descuentan_nc_a_credito_sin_asociar(s):
    prov = Tercero(nit="800111222", nombre="Proveedor", es_proveedor=True)
    s.add(prov)
    s.flush()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    _gasto(s, cat, D("1190000"), date(2026, 3, 1), forma="credito", proveedor=prov)
    nc = Gasto(tipo_soporte="NC", numero="NC-S", fecha=date(2026, 3, 10), categoria=cat, proveedor=prov, subtotal=D("190000"),
               iva=0, iva_descontable=False, total=D("190000"), forma_pago="credito")
    s.add(nc)
    s.flush()
    contab.contabilizar_gasto(s, nc)
    s.commit()
    hojas, incompletos = exogena.generar(s, 2026)
    assert [fila[-1] for fila in hojas["1009 Cuentas por pagar"][1]] == [D("1000000")]
    assert saldo_cuenta(s, "2205", date(2026, 12, 31)) == D("-1000000")
    assert any("sin factura asociada" in t.nombre for t, _ in incompletos)
    filas, totales = informes.cuentas_por_pagar_edades(s, date(2026, 12, 31))
    assert totales["total"] == D("1000000")


def test_informe_conciliacion_linea_registrada_despues_del_corte(s):
    b = s.query(Banco).first()
    c = _cliente(s)
    rec = Recaudo(fecha=date(2026, 4, 1), cliente_id=c.id, banco_id=b.id, valor=D("500000"))
    s.add(rec)
    s.flush()
    contab.contabilizar_recaudo(s, rec)
    s.add(MovimientoBanco(banco_id=b.id, fecha=date(2026, 3, 31), descripcion="CONSIGNACION", valor=D("500000"), huella="h",
                          estado="conciliado", origen_tipo="recaudo", origen_id=rec.id))
    s.commit()
    inf = informes.informe_conciliacion(s, b, date(2026, 3, 31), D("500000"))
    assert inf["total_pendientes"] == D("500000") and inf["diferencia"] == 0 and len(inf["posteriores"]) == 1


def test_pdf_generico_con_nombre_larguisimo(cliente_web, s):
    prov = Tercero(nit="800111222", nombre="PATRIMONIO AUTÓNOMO FIDEICOMISO " + "CONSORCIO LARGO " * 9, es_proveedor=True)
    s.add(prov)
    s.flush()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    _gasto(s, cat, D("1190000"), date(2026, 3, 1), forma="credito", proveedor=prov)
    s.commit()
    r = cliente_web.get("/informes/cuentas-por-pagar?corte=2026-04-30&pdf=1")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")


def test_cotizacion_descripcion_muy_larga(cliente_web, s):
    c = _cliente(s)
    s.commit()
    r = cliente_web.post("/cotizaciones/nueva", data={"fecha": "2026-04-01", "cliente_id": c.id, "titulo": "T",
                                                      "descripcion": ["x" * 2001], "valor": ["100"]}, follow_redirects=True)
    assert "demasiado larga" in r.get_data(as_text=True)
    from app.models import Cotizacion, LineaCotizacion
    cot = Cotizacion(numero="COT-0001", fecha=date(2026, 4, 1), cliente_id=c.id, titulo="Propuesta")
    cot.lineas.append(LineaCotizacion(descripcion="palabra " * 700, valor=D("1000000"), iva_pct=D("19")))  # > 1 página
    cot.lineas.append(LineaCotizacion(descripcion="Última etapa", valor=D("2000000"), iva_pct=D("19")))
    s.add(cot)
    s.commit()
    pdf = exportar.cotizacion_pdf(exportar._empresa_dict(s), cot)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") <= 3


def test_limite_simple_inferior_a_100000_uvt(s):
    c = _cliente(s)
    _factura(s, c, "FV-1", impuestos.uvt(2026, s) * 100000, date(2026, 2, 1))
    s.commit()
    assert impuestos.declaracion_simple(s, 2026).supera_limite  # exactamente 100.000 UVT ya no es "inferior"


# ------------------------------------------------------------------ configuración, sistema y seguridad

def test_respaldos_a_conservar_invalido_no_se_guarda(cliente_web, s):
    contab.set_config(s, "respaldos_a_conservar", "3")
    s.commit()
    r = cliente_web.post("/configuracion/", data={"accion": "empresa", "respaldos_a_conservar": "x"}, follow_redirects=True)
    assert "debe ser un número entero" in r.get_data(as_text=True)
    assert contab.config(s, "respaldos_a_conservar") == "3"
    r = cliente_web.post("/configuracion/", data={"accion": "empresa", "carpeta_respaldo": "~nadie/Respaldos"}, follow_redirects=True)
    assert r.status_code == 200


def test_validar_respaldo_con_carpeta_temporal_rara(s, monkeypatch, tmp_path):
    from app import respaldo
    real = respaldo.crear_respaldo(s).read_bytes()
    rara = tmp_path / "num#1?x"
    rara.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(rara))
    sistema.validar_respaldo(real)  # antes: 'faltan sus tablas' por la URI cortada en '#'


def test_archivo_carpeta_da_404_y_clave_secreta_0600(cliente_web, app):
    (config.ADJUNTOS_DIR / "gastos").mkdir(parents=True, exist_ok=True)
    assert cliente_web.get("/archivo/gastos").status_code == 404
    assert cliente_web.get("/archivo/gastos/").status_code == 404
    archivo = config.DATOS_DIR / "clave.secreta"
    os.chmod(archivo, 0o644)
    sistema.clave_secreta()
    assert stat.S_IMODE(archivo.stat().st_mode) == 0o600


def test_poda_de_intentos_no_libera_bloqueados():
    control = sistema._IntentosAcceso()
    for _ in range(5):
        control.fallo("10.0.0.9")
    assert control.bloqueado("10.0.0.9")
    for i in range(1100):
        control.fallo(f"10.1.{i // 250}.{i % 250}")
    assert control.bloqueado("10.0.0.9")
