"""Hallazgos de la auditoría completa de la 1.4.2 (versión 1.4.3)."""
import email
import io
import re
import sqlite3
import zipfile
from datetime import date
from decimal import Decimal
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart

import pytest

from app import bancos, config, contab, correo, exogena, exportar, impuestos, importacion, informes, planeacion, sistema
from app.dian_xml import leer_archivo
from app.models import (Asiento, Banco, CategoriaGasto, Cotizacion, Cuenta, DocumentoVenta, Gasto, MovimientoBanco,
                        OtroIngreso, PagoImpuesto, Recaudo, Tercero)
from tests.test_contabilidad import ARRENDADOR, CLIENTE, _escenario, cuadra, importar
from tests.test_informes import _cliente, _factura, _gasto
from ubl import factura_compra, factura_venta

D = Decimal


# ------------------------------------------------------------------ contabilidad

def test_ediciones_sin_efecto_contable_en_periodo_bloqueado(s):
    c = _cliente(s)
    doc = _factura(s, c, "FV-1", D("1000000"), date(2026, 5, 10))  # sin reteIVA: el asiento RI queda vacío
    banco = s.query(Banco).first()
    rec = Recaudo(fecha=date(2026, 5, 20), cliente_id=c.id, banco_id=banco.id, valor=D("1190000"), referencia="Ref 1")
    s.add(rec)
    s.flush()
    contab.contabilizar_recaudo(s, rec)
    s.commit()
    contab.set_config(s, "periodo_bloqueado_hasta", "2026-06-30")
    doc.notas = "nota nueva"
    contab.contabilizar_venta(s, doc)  # antes: ErrorContable por el RI sin líneas
    rec.referencia = "Ref corregida"
    contab.contabilizar_recaudo(s, rec)  # solo cambia el texto de la línea: no es una modificación contable
    s.commit()
    a = s.query(Asiento).filter_by(origen=f"recaudo:{rec.id}").one()
    assert "Ref corregida" in a.lineas[0].descripcion
    # Un cambio de valor sí se rechaza
    rec.valor = D("500000")
    with pytest.raises(contab.ErrorContable):
        contab.contabilizar_recaudo(s, rec)
    s.rollback()


def test_reteiva_se_reclasifica_una_vez_por_bimestre(cliente_web, s):
    _escenario(s)
    banco = s.query(Banco).first()
    p1 = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=date(2026, 11, 19), valor_simple=D("885000"),
                      valor_iva=D("2328000"), banco=banco)
    s.add(p1)
    s.flush()
    contab.contabilizar_pago_impuesto(s, p1)
    s.commit()
    from app.reportes import saldo_cuenta
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == 0
    p2 = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=date(2026, 11, 25), valor_simple=D("10000"),
                      valor_iva=D("0"), banco=banco)  # pago complementario del mismo bimestre
    s.add(p2)
    s.flush()
    contab.contabilizar_pago_impuesto(s, p2)
    s.commit()
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == 0  # antes quedaba en −142.500
    # Al eliminar el primero, el segundo asume la reclasificación
    contab.set_config(s, "periodo_bloqueado_hasta", "")
    r = cliente_web.post(f"/impuestos/pago/{p1.id}/eliminar", follow_redirects=True)
    assert "Pago eliminado" in r.get_data(as_text=True)
    s.expire_all()
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == 0
    cuadra(s)


def test_valores_enormes_y_redondeo(cliente_web, s):
    with pytest.raises(ValueError, match="demasiado grande"):
        contab.d("1e300")
    with pytest.raises(ValueError):
        contab.d("1000000000000000")
    c = _cliente(s)
    s.commit()
    r = cliente_web.post("/cotizaciones/nueva", data={"fecha": "2026-04-01", "cliente_id": c.id, "titulo": "T",
                                                      "descripcion": ["A"], "valor": ["1e300"]}, follow_redirects=True)
    assert "demasiado grande" in r.get_data(as_text=True) and s.query(Cotizacion).count() == 0
    assert cliente_web.get("/cotizaciones/").status_code == 200
    # Un monto que redondea a cero no crea documentos sin asiento
    banco = s.query(Banco).first()
    r = cliente_web.post("/impuestos/pago", data={"formulario": "2593", "anio": "2026", "bimestre": "1",
                                                  "fecha": "2026-03-10", "valor_simple": "0,004", "valor_iva": "0",
                                                  "banco_id": banco.id}, follow_redirects=True)
    assert "positivos" in r.get_data(as_text=True) and s.query(PagoImpuesto).count() == 0


def test_nota_credito_no_supera_el_saldo(cliente_web, s):
    c = _cliente(s)
    f = _factura(s, c, "FV-1", D("1000000"), date(2026, 3, 1))
    s.commit()
    r = cliente_web.post("/ventas/nueva", data={"tipo": "NC", "numero": "NC-1", "fecha": "2026-03-05", "cliente_id": c.id,
                                                "base": "2.000.000", "iva": "380.000", "referencia_id": f.id},
                         follow_redirects=True)
    assert "supera el saldo" in r.get_data(as_text=True)
    assert s.query(DocumentoVenta).filter_by(tipo="NC").count() == 0


def test_cuenta_de_pago_validada(cliente_web, s):
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    r = cliente_web.post("/gastos/nuevo", data={"tipo_soporte": "RE", "fecha": "2026-03-01", "categoria_id": cat.id,
                                                "subtotal": "100.000", "iva": "0", "forma_pago": "contado",
                                                "cuenta_pago": "999999"}, follow_redirects=True)
    assert "Elija la cuenta" in r.get_data(as_text=True) and s.query(Gasto).count() == 0


def test_cierre_de_anio_futuro_rechazado(cliente_web, s):
    r = cliente_web.post("/contabilidad/cierre", data={"anio": "2031", "accion": "cerrar"}, follow_redirects=True)
    assert "no ha empezado" in r.get_data(as_text=True) and not planeacion.anio_cerrado(s, 2031)


def test_limite_simple_es_el_general(cliente_web, s):
    assert config.SIMPLE_LIMITE_UVT == 100000 and not hasattr(config, "SIMPLE_LIMITE_UVT_PROFESIONALES")
    c = _cliente(s)
    _factura(s, c, "FV-1", impuestos.uvt(2026, s) * 13000, date(2026, 2, 1))  # > 12.000 UVT
    s.commit()
    assert not impuestos.declaracion_simple(s, 2026).supera_limite
    html = cliente_web.get("/impuestos/f260?anio=2026").get_data(as_text=True)
    assert "12.000 UVT" not in html and "Ley 2277" not in cliente_web.get("/impuestos/simple").get_data(as_text=True)


def test_exogena_avisa_gastos_sin_proveedor(s):
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    _gasto(s, cat, D("900000"), date(2026, 3, 1), banco=s.query(Banco).first())
    s.commit()
    _, incompletos = exogena.generar(s, 2026)
    assert any("sin proveedor" in t.nombre for t, _ in incompletos)


# ------------------------------------------------------------------ bancos

def test_numero_y_fecha_de_extractos():
    assert bancos._numero("1,500,000") == D("1500000")
    assert bancos._numero("6,000") == D("6000")
    assert bancos._numero("-1,500,000.25") == D("-1500000.25")
    assert bancos._numero("250,00") == D("250")
    assert bancos._numero("1.500,50") == D("1500.50")
    assert bancos._numero("250.000") == D("250000")
    assert bancos._fecha("01/01/0001") is None and bancos._fecha("31/12/9999") is None
    assert bancos._fecha("15/03/2026") == date(2026, 3, 15)


def test_linea_con_fecha_absurda_no_revienta(cliente_web, s):
    b = s.query(Banco).first()
    s.add(MovimientoBanco(banco_id=b.id, fecha=date(1, 1, 1), descripcion="RARA", valor=D("10"), huella="h0"))
    s.commit()
    assert cliente_web.get(f"/bancos/conciliacion?banco={b.id}").status_code == 200


def test_deshacer_no_borra_documentos_registrados_a_mano(cliente_web, s):
    b = s.query(Banco).first()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    g = _gasto(s, cat, D("70000"), date(2026, 3, 10), banco=b)
    mov = MovimientoBanco(banco_id=b.id, fecha=date(2026, 3, 10), descripcion="PAGO", valor=D("-70000"), huella="h1")
    s.add(mov)
    s.commit()
    # Vincular solo acepta documentos que coinciden y nunca deja creado_aqui
    r = cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "vincular", "origen": f"gasto:{g.id}"},
                         follow_redirects=True)
    assert "vinculado" in r.get_data(as_text=True)
    s.expire_all()
    mov = s.get(MovimientoBanco, mov.id)
    assert mov.estado == "conciliado" and not mov.creado_aqui
    mov.creado_aqui = True  # como lo dejaba una conciliación anterior que creó otro documento
    s.commit()
    bancos.liberar(s, "gasto", g.id)
    s.commit()
    assert mov.estado == "pendiente" and not mov.creado_aqui
    mov.origen_tipo, mov.origen_id, mov.estado = "gasto", g.id, "conciliado"
    s.commit()
    cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "pendiente"})
    s.expire_all()
    assert s.get(Gasto, g.id) is not None  # el gasto manual sobrevive
    # Vincular un documento que no coincide se rechaza
    otro = MovimientoBanco(banco_id=b.id, fecha=date(2026, 3, 10), descripcion="OTRO", valor=D("-99"), huella="h2")
    s.add(otro)
    s.commit()
    r = cliente_web.post(f"/bancos/movimiento/{otro.id}", data={"accion": "vincular", "origen": f"gasto:{g.id}"},
                         follow_redirects=True)
    assert "no coincide" in r.get_data(as_text=True)


def test_deshacer_activo_creado_desde_el_banco_borra_depreciaciones(cliente_web, s):
    b = s.query(Banco).first()
    cat = CategoriaGasto(nombre="Computadores", cuenta="152805", concepto_exogena="5016")
    s.add(cat)
    mov = MovimientoBanco(banco_id=b.id, fecha=date(2026, 1, 15), descripcion="COMPRA PC", valor=D("-2400000"), huella="h1")
    s.add(mov)
    s.commit()
    cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "gasto", "categoria_id": cat.id})
    s.expire_all()
    mov = s.get(MovimientoBanco, mov.id)
    assert mov.creado_aqui and mov.origen_tipo == "gasto"
    gid = mov.origen_id
    planeacion.causar_depreciaciones(s, hasta=date(2026, 6, 30))
    s.commit()
    assert s.query(Asiento).filter(Asiento.origen.like(f"depre:{gid}:%")).count() > 0
    cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "pendiente"})
    s.expire_all()
    assert s.get(Gasto, gid) is None
    assert s.query(Asiento).filter(Asiento.origen.like(f"depre:{gid}:%")).count() == 0
    cuadra(s)


def test_conciliacion_automatica_no_vincula_ambiguos(s):
    b = s.query(Banco).first()
    c = _cliente(s)
    rec = Recaudo(fecha=date(2026, 3, 10), cliente_id=c.id, banco_id=b.id, valor=D("100000"))
    s.add(rec)
    s.flush()
    contab.contabilizar_recaudo(s, rec)
    for h in ("a", "b"):
        s.add(MovimientoBanco(banco_id=b.id, fecha=date(2026, 3, 10), descripcion="CONSIGNACION", valor=D("100000"), huella=h))
    s.commit()
    assert bancos.conciliar_automatico(s, b.id) == 0  # dos líneas compiten por el mismo recaudo
    s.query(MovimientoBanco).filter_by(huella="b").delete()
    s.commit()
    assert bancos.conciliar_automatico(s, b.id) == 1
    m = s.query(MovimientoBanco).one()
    assert m.estado == "conciliado" and not m.creado_aqui


def test_informe_conciliacion_respeta_el_corte(s):
    b = s.query(Banco).first()
    oi = OtroIngreso(fecha=date(2026, 3, 30), banco_id=b.id, cuenta="421005", concepto="Intereses", valor=D("100000"))
    s.add(oi)
    s.flush()
    contab.contabilizar_otro_ingreso(s, oi)
    s.add(MovimientoBanco(banco_id=b.id, fecha=date(2026, 4, 2), descripcion="INTERESES", valor=D("100000"), huella="h",
                          estado="conciliado", origen_tipo="otroingreso", origen_id=oi.id))
    s.commit()
    inf = informes.informe_conciliacion(s, b, date(2026, 3, 31), None)
    assert inf["total_en_libros"] == D("100000")  # la línea del extracto es posterior al corte
    assert inf["extracto_esperado"] == inf["saldo_libros"] - D("100000")
    inf_abril = informes.informe_conciliacion(s, b, date(2026, 4, 30), None)
    assert inf_abril["total_en_libros"] == 0


def test_ingreso_conciliado_no_cambia_valor_ni_cuenta_de_honorarios(cliente_web, s):
    b = s.query(Banco).first()
    mov = MovimientoBanco(banco_id=b.id, fecha=date(2026, 4, 30), descripcion="ABONO INTERESES", valor=D("5000"), huella="h1")
    s.add(mov)
    s.commit()
    cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "ingreso", "cuenta": "421005"})
    s.expire_all()
    oi = s.get(OtroIngreso, s.get(MovimientoBanco, mov.id).origen_id)
    datos = {"fecha": "2026-04-30", "banco_id": b.id, "cuenta": "421005", "concepto": "Intereses abril", "valor": "9.000",
             "descuentos": "0"}
    r = cliente_web.post(f"/bancos/ingresos/{oi.id}", data=datos, follow_redirects=True)
    assert "deshaga la conciliación" in r.get_data(as_text=True)
    r = cliente_web.post(f"/bancos/ingresos/{oi.id}", data={**datos, "valor": "5.000"}, follow_redirects=True)
    assert "Ingreso actualizado" in r.get_data(as_text=True)
    r = cliente_web.post("/bancos/ingresos/nuevo", data={**datos, "cuenta": "415595"}, follow_redirects=True)
    assert "se registran con factura" in r.get_data(as_text=True)
    # Un banco inactivo sigue apareciendo en el formulario del ingreso que ya lo usa
    b.activo = False
    s.commit()
    assert f'value="{b.id}"' in cliente_web.get(f"/bancos/ingresos/{oi.id}").get_data(as_text=True)
    assert cliente_web.get("/bancos/conciliacion").status_code == 200  # sin bancos activos no da 404


# ------------------------------------------------------------------ importación y correo

def test_zip_corrupto_y_xml_incompleto_no_revientan(s):
    res = leer_archivo("x.zip", b"PK\x03\x04" + b"\x00" * 60)
    assert res[0].error and "ZIP" in res[0].error
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("f.xml", b"<Invoice/>" * 50)
    datos = bytearray(buf.getvalue())
    datos[40] ^= 0xFF  # daña el contenido comprimido: CRC incorrecto
    res = leer_archivo("x.zip", bytes(datos))
    assert all(r.error for r in res)
    xml = factura_compra("INM-1", "2026-03-01", ARRENDADOR, 100000, 19000)
    sin_fecha = re.sub(r"<cbc:IssueDate>[^<]*</cbc:IssueDate>", "", xml)
    res = leer_archivo("f.xml", sin_fecha.encode())
    assert res[0].error and "incompleto" in res[0].error
    assert all(r.ok is False for r in importacion.importar_archivo(s, "f.xml", sin_fecha.encode()))


def test_factura_manual_sin_cufe_no_se_duplica_al_importar(s):
    t = Tercero(nit="900.123.456-7", dv="7", nombre="Cliente con puntos", es_cliente=True)
    s.add(t)
    s.flush()
    _factura(s, t, "ALC-1", D("10000000"), date(2026, 9, 15))
    s.commit()
    res = importar(s, "1.xml", factura_venta("ALC-1", "2026-09-15", CLIENTE, 10_000_000))
    assert not res[0].ok and "Ya estaba registrado" in res[0].mensaje
    assert s.query(DocumentoVenta).count() == 1
    # Y un NIT escrito con puntos no genera un segundo tercero
    importar(s, "2.xml", factura_venta("ALC-2", "2026-09-16", CLIENTE, 1_000_000))
    assert s.query(Tercero).filter(Tercero.nombre.like("%Cliente%")).count() == 1
    assert s.get(DocumentoVenta, 2).cliente_id == t.id


def test_correo_decodifica_asuntos_raros_y_empaqueta_adjuntos_sueltos(s):
    assert isinstance(correo._decodificar("=?unknown-8bit?q?Factura_=F3?="), str)
    assert isinstance(correo._decodificar("=?x-charset-inventado?b?SG9sYQ==?="), str)
    msg = MIMEMultipart()
    msg["Subject"] = "Factura"
    xml = factura_compra("INM-77", "2026-03-01", ARRENDADOR, 1_000_000, 190_000).encode()
    for nombre, contenido, sub in (("fv77.xml", xml, "xml"), ("fv77.pdf", b"%PDF-1.4 fake", "pdf")):
        parte = MIMEApplication(contenido, _subtype=sub)
        parte.add_header("Content-Disposition", "attachment", filename=nombre)
        msg.attach(parte)
    adjuntos = correo._adjuntos(email.message_from_bytes(msg.as_bytes()))
    assert len(adjuntos) == 1 and adjuntos[0][0].endswith(".zip")
    res = importacion.importar_archivo(s, *adjuntos[0], origen="correo")
    assert res[0].ok
    g = s.query(Gasto).one()
    assert g.soporte_archivo and g.soporte_archivo.endswith(".pdf")


def test_correo_procesa_los_mas_antiguos_primero(monkeypatch, s):
    """Con más mensajes que el límite, los antiguos no se quedan sin importar nunca."""
    class Imap:
        def select(self, *a, **k):
            return "OK", [b"1"]

        def uid(self, orden, *args):
            if orden == "search":
                return "OK", [b" ".join(str(i).encode() for i in range(1, 121))]
            uid = args[0]
            cuerpo = f"Message-ID: <m{uid.decode()}@x>\r\nSubject: s{uid.decode()}\r\n\r\n".encode()
            return "OK", [(b"1 (BODY[HEADER] {0}", cuerpo), b")"]

        def logout(self):
            pass

    monkeypatch.setattr(correo, "conectar", lambda session: (Imap(), {"correo_carpeta": "INBOX", "correo_dias": "30",
                                                                    "correo_filtro": ""}))
    res = correo.revisar(s, limite=50)
    assert res.error is None and res.revisados == 50
    from app.models import CorreoProcesado
    clave = lambda n: correo._clave_mensaje(f"<m{n}@x>", b"0")  # noqa: E731
    ids = {m.mensaje_id for m in s.query(CorreoProcesado)}
    assert clave(1) in ids and clave(50) in ids and clave(51) not in ids
    res = correo.revisar(s, limite=50)
    assert res.revisados == 50 and clave(100) in {m.mensaje_id for m in s.query(CorreoProcesado)}


# ------------------------------------------------------------------ informes y PDF

def test_periodo_comparable_meses_completos():
    assert informes.periodo_comparable(date(2026, 4, 1), date(2026, 4, 30), "anterior") == (date(2026, 3, 1), date(2026, 3, 31))
    assert informes.periodo_comparable(date(2028, 1, 1), date(2028, 12, 31), "anterior") == (date(2027, 1, 1), date(2027, 12, 31))
    assert informes.periodo_comparable(date(2026, 1, 1), date(2026, 3, 31), "anterior") == (date(2025, 10, 1), date(2025, 12, 31))
    assert informes.periodo_comparable(date(2026, 4, 10), date(2026, 4, 20), "anterior") == (date(2026, 3, 30), date(2026, 4, 9))


def test_pdf_cotizacion_con_descripciones_largas(s):
    from app.models import LineaCotizacion
    c = _cliente(s)
    cot = Cotizacion(numero="COT-0001", fecha=date(2026, 4, 1), cliente_id=c.id, titulo="Propuesta")
    for i in range(30):
        cot.lineas.append(LineaCotizacion(descripcion=("Etapa %d: " % i) + "análisis detallado " * 25, valor=D("1000000"),
                                          iva_pct=D("19")))
    s.add(cot)
    s.commit()
    pdf = exportar.cotizacion_pdf(exportar._empresa_dict(s), cot)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") >= 2 or pdf.count(b"/Type /Page") >= 2


# ------------------------------------------------------------------ seguridad y sistema

def test_acceso_con_clave_con_tilde_y_freno_de_intentos(cliente_web, s):
    contab.set_config(s, "acceso_red", "si")
    contab.set_config(s, "clave_acceso", "contraseña segura ñ")
    s.commit()
    remoto = {"REMOTE_ADDR": "192.168.1.77"}
    r = cliente_web.post("/acceso", data={"clave": "contraseña segura ñ"}, environ_base=remoto)
    assert r.status_code == 302  # antes: 500 por comparar str con caracteres no ASCII
    assert cliente_web.get("/salir", environ_base=remoto).status_code == 405
    assert cliente_web.post("/salir", environ_base=remoto).status_code == 302
    otro = {"REMOTE_ADDR": "192.168.1.78"}
    for _ in range(5):
        r = cliente_web.post("/acceso", data={"clave": "mal"}, environ_base=otro)
        assert r.status_code == 200 and "incorrecta" in r.get_data(as_text=True)
    r = cliente_web.post("/acceso", data={"clave": "contraseña segura ñ"}, environ_base=otro)
    assert "Demasiados intentos" in r.get_data(as_text=True)
    sistema.intentos_acceso.limpiar("192.168.1.78")
    r = cliente_web.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_entradas_raras_en_rutas_no_dan_500(cliente_web, s):
    assert cliente_web.post("/informes/reembolsables", data={"id": ["²", "٣", "abc"]}).status_code == 302
    assert cliente_web.post("/gastos/revisar", data={"id": ["²"]}).status_code in (200, 302)
    assert cliente_web.get("/ventas/99999999999999999999").status_code == 404
    assert cliente_web.get("/gastos/99999999999999999999").status_code == 404
    assert cliente_web.get("/archivo/" + "a" * 300 + ".pdf").status_code == 404
    r = cliente_web.post("/contabilidad/puc", data={"codigo": "٥١٠٥٠٥", "nombre": "x"}, follow_redirects=True)
    assert r.status_code == 200 and s.get(Cuenta, "٥١٠٥٠٥") is None


def test_restauracion_rechaza_respaldos_invalidos(s, app):
    def zip_con(contenido):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("contabilidad.db", contenido)
        return buf.getvalue()
    with pytest.raises(ValueError, match="no es un ZIP"):
        sistema.validar_respaldo(b"basura")
    with pytest.raises(ValueError, match="no contiene"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("otro.txt", b"x")
        sistema.validar_respaldo(buf.getvalue())
    with pytest.raises(ValueError, match="no es una base de datos SQLite"):
        sistema.validar_respaldo(zip_con(b"no soy sqlite"))
    vacia = io.BytesIO()
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE x (a)")
    vacia.write(b"".join(con.iterdump()).encode() if False else b"")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ruta = f"{tmp}/v.db"
        c2 = sqlite3.connect(ruta)
        c2.execute("CREATE TABLE x (a)")
        c2.commit()
        c2.close()
        with pytest.raises(ValueError, match="faltan sus tablas"):
            sistema.validar_respaldo(zip_con(open(ruta, "rb").read()))
    datos = zip_con(b"SQLite format 3\x00" + b"\x00" * 4000)
    with pytest.raises(ValueError):
        sistema.validar_respaldo(datos)
    # Un respaldo real del programa pasa
    from app import respaldo
    real = respaldo.crear_respaldo(s).read_bytes()
    sistema.validar_respaldo(real)
    # Y un pendiente dañado no aparta la base actual
    pendiente = config.DATOS_DIR / sistema.ARCHIVO_RESTAURAR
    pendiente.write_bytes(zip_con(b"no soy sqlite"))
    assert sistema.aplicar_restauracion_pendiente() is None
    assert not pendiente.exists() and config.DB_PATH.exists()
    assert list(config.DATOS_DIR.glob("restaurar_rechazado_*.zip"))


def test_descarga_de_actualizacion_sin_octet_stream(monkeypatch, s):
    peticiones = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"zip"

    monkeypatch.setattr(sistema, "urlopen", lambda req, timeout=0: peticiones.append(req) or Resp())
    monkeypatch.setattr(sistema, "preparar_actualizacion", lambda contenido, destino: destino)
    sistema.descargar_actualizacion(s, {"url_zip": "https://api.github.com/repos/x/y/zipball/v1.4.3"})
    assert peticiones[0].get_header("Accept") == "*/*"  # con application/octet-stream GitHub responde 415


def test_carpetas_invalidas_no_rompen_configuracion(cliente_web, s):
    larga = "C:\\" + "x" * 300
    r = cliente_web.post("/configuracion/", data={"accion": "empresa", "carpeta_respaldo": larga, "carpeta_vigilada": larga},
                         follow_redirects=True)
    assert r.status_code == 200 and "no es válida" in r.get_data(as_text=True)
    assert contab.config(s, "carpeta_respaldo") is None
    contab.set_config(s, "carpeta_respaldo", larga)  # guardada por una versión anterior
    contab.set_config(s, "carpeta_vigilada", larga)
    s.commit()
    assert cliente_web.get("/configuracion/").status_code == 200
    assert sistema.respaldos_disponibles(s) == []
    from app import vigilancia
    assert vigilancia.revisar(s) == []


# ------------------------------------------------------------------ auditor de código

def test_exogena_1009_descuenta_nc_de_proveedor_de_contado(cliente_web, s):
    prov = Tercero(nit="800111222", nombre="Proveedor", es_proveedor=True)
    s.add(prov)
    s.flush()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    f = _gasto(s, cat, D("1190000"), date(2026, 3, 1), forma="credito", proveedor=prov)
    s.commit()
    fid, pid, cid = f.id, prov.id, cat.id
    # NC registrada a mano "de contado" (valor por defecto del formulario) sobre la factura a crédito
    r = cliente_web.post("/gastos/nuevo", data={"tipo_soporte": "NC", "fecha": "2026-03-05", "categoria_id": cat.id,
                                                "proveedor_id": pid, "subtotal": "238.000", "iva": "0",
                                                "forma_pago": "contado", "cuenta_pago": "110505", "referencia_id": fid},
                         follow_redirects=True)
    assert r.status_code == 200
    nc = s.query(Gasto).filter_by(tipo_soporte="NC").one()
    f = s.get(Gasto, fid)
    assert nc.forma_pago == "credito" and nc.referencia_id == fid  # rebaja la cuenta por pagar, no el banco
    assert f.saldo == D("952000") == f.saldo_al(date(2026, 12, 31))
    hojas, _ = exogena.generar(s, 2026)
    filas = hojas["1009 Cuentas por pagar"][1]
    assert [fila[-1] for fila in filas] == [D("952000")]
    # Una NC mayor que lo pendiente se rechaza
    r = cliente_web.post("/gastos/nuevo", data={"tipo_soporte": "NC", "fecha": "2026-03-06", "categoria_id": cid,
                                                "proveedor_id": pid, "subtotal": "2.000.000", "iva": "0",
                                                "forma_pago": "credito", "referencia_id": fid}, follow_redirects=True)
    assert "supera el valor pendiente" in r.get_data(as_text=True)


def test_retainer_invalido_avisa_y_formatos_en_pesos(cliente_web, s):
    c = _cliente(s)
    s.commit()
    cid, nit, nombre = c.id, c.nit, c.nombre
    r = cliente_web.post(f"/terceros/{cid}", data={"nit": nit, "nombre": nombre, "es_cliente": "on",
                                                   "retainer_mensual": "abc"}, follow_redirects=True)
    assert "no es un número válido" in r.get_data(as_text=True) and s.get(Tercero, cid).retainer_mensual is None
    r = cliente_web.post("/configuracion/", data={"accion": "empresa", "respaldos_a_conservar": "x"}, follow_redirects=True)
    assert "debe ser un número entero" in r.get_data(as_text=True)
    from app import bitacora
    assert bitacora._texto(D("1000000.5")) == "1.000.000,50"
