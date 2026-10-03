import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from ubl import EMPRESA, documento, factura_compra, factura_venta

CLIENTE = ("900123456", "7", "CLIENTE DEMO S.A.S.")
GRAN_CONTRIBUYENTE = ("860000001", "2", "GRAN CONTRIBUYENTE S.A.")
ARRENDADOR = ("830000001", "5", "INMOBILIARIA DEMO S.A.S.")
CAMARA = ("860007322", "9", "CAMARA DE COMERCIO DE BOGOTA")
D = Decimal


def importar(s, nombre, xml, **kw):
    from app.importacion import importar_archivo
    return importar_archivo(s, nombre, xml.encode() if isinstance(xml, str) else xml, **kw)


def cuadra(s):
    from app.models import Asiento
    for a in s.query(Asiento):
        assert sum(l.debito for l in a.lineas) == sum(l.credito for l in a.lineas), a.descripcion


# ------------------------------------------------------------------ lector XML

def test_lee_contenedor_dian():
    from app.dian_xml import leer_xml
    doc = leer_xml(factura_venta("ALC-1", "2026-09-15", CLIENTE, 10_000_000, vence="2026-10-15"))
    assert doc.tipo == "FV" and doc.numero == "ALC-1" and doc.cufe == "cufe-alc-1"
    assert doc.emisor.nit == "901913577" and doc.receptor.nit == "900123456" and doc.receptor.dv == "7"
    assert doc.iva == D("1900000") and doc.total == D("11900000.00")
    assert doc.vencimiento == date(2026, 10, 15) and doc.validado_dian is True
    assert doc.receptor.cod_municipio == "11001"


def test_lee_retencion_y_nota_credito():
    from app.dian_xml import leer_xml
    doc = leer_xml(factura_venta("ALC-2", "2026-10-01", GRAN_CONTRIBUYENTE, 5_000_000, reteiva=142500))
    assert doc.retenciones == {"ReteIVA": D("142500")}
    nc = leer_xml(documento(tipo="CreditNote", numero="NC-1", cufe="cude-nc1", fecha="2026-09-30", emisor=EMPRESA,
                            receptor=CLIENTE, base=1_000_000, iva=190_000, referencia=("ALC-1", "cufe-alc-1")))
    assert nc.tipo == "NC" and nc.referencia_cufe == "cufe-alc-1" and nc.total == D("1190000.00")


def test_zip_con_pdf():
    from app.dian_xml import leer_archivo
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ad0901913577.xml", factura_venta("ALC-9", "2026-09-01", CLIENTE, 100))
        z.writestr("ad0901913577.pdf", b"%PDF-1.4 prueba")
    leidos = leer_archivo("factura.zip", buf.getvalue())
    assert len(leidos) == 1 and leidos[0].documento.numero == "ALC-9" and leidos[0].pdf.startswith(b"%PDF")


def test_xml_invalido():
    from app.dian_xml import leer_archivo
    assert leer_archivo("x.xml", b"no es xml")[0].error


def test_numeros_colombianos():
    from app.contab import d
    assert d("1.234.567,89") == D("1234567.89")
    assert d("250.000") == D("250000")
    assert d("1274400.00") == D("1274400.00")
    assert d("$ 1.500") == D("1500")


# ------------------------------------------------------------------ ventas

def test_importa_factura_venta_y_contabiliza(s):
    from app.models import DocumentoVenta, Movimiento
    r = importar(s, "f.xml", factura_venta("ALC-1", "2026-09-15", CLIENTE, 10_000_000))
    assert r[0].ok, r[0].mensaje
    doc = s.query(DocumentoVenta).one()
    assert doc.cliente.nombre == "CLIENTE DEMO S.A.S." and doc.cliente.es_cliente
    movs = {m.cuenta: (m.debito, m.credito) for m in s.query(Movimiento)}
    assert movs["130505"] == (D("11900000"), 0)
    assert movs["415595"] == (0, D("10000000"))
    assert movs["240805"] == (0, D("1900000"))
    cuadra(s)
    # duplicado por CUFE
    r = importar(s, "f.xml", factura_venta("ALC-1", "2026-09-15", CLIENTE, 10_000_000))
    assert not r[0].ok and "Ya estaba" in r[0].mensaje


def test_rechaza_documento_de_otra_empresa_salvo_forzado(s):
    otro = ("800000000", "1", "OTRA S.A.S.")
    from ubl import documento as doc
    xml = doc(numero="X-1", cufe="cufe-x1", fecha="2026-09-01", emisor=CAMARA, receptor=otro, base=100, iva=0)
    r = importar(s, "x.xml", xml)
    assert not r[0].ok and "no es de la firma" in r[0].mensaje
    r = importar(s, "x.xml", xml, forzar="gasto")
    assert r[0].ok


def test_reteiva_desde_xml_y_saldo(s):
    from app import cartera
    from app.models import DocumentoVenta
    importar(s, "f.xml", factura_venta("ALC-2", "2026-10-01", GRAN_CONTRIBUYENTE, 5_000_000, reteiva=142500))
    doc = s.query(DocumentoVenta).one()
    assert doc.reteiva_aplica and doc.reteiva_valor == D("142500")
    assert cartera.saldo_documento(s, doc) == D("5950000") - D("142500")
    cuadra(s)


def test_cliente_que_retiene_marca_reteiva_por_defecto(s):
    from app.models import DocumentoVenta, Tercero
    s.add(Tercero(nit=CLIENTE[0], dv="7", nombre=CLIENTE[2], es_cliente=True, aplica_reteiva=True))
    s.commit()
    importar(s, "f.xml", factura_venta("ALC-3", "2026-09-10", CLIENTE, 1_000_000))
    doc = s.query(DocumentoVenta).one()
    assert doc.reteiva_aplica and doc.reteiva_valor == D("28500")


def test_nota_credito_reduce_saldo_e_iva(s):
    from app import cartera, impuestos
    from app.models import DocumentoVenta
    importar(s, "f.xml", factura_venta("ALC-1", "2026-09-15", CLIENTE, 10_000_000))
    nc = documento(tipo="CreditNote", numero="NC-1", cufe="cude-nc1", fecha="2026-09-30", emisor=EMPRESA,
                   receptor=CLIENTE, base=1_000_000, iva=190_000, referencia=("ALC-1", "cufe-alc-1"))
    r = importar(s, "nc.xml", nc)
    assert r[0].ok, r[0].mensaje
    fv = s.query(DocumentoVenta).filter_by(tipo="FV").one()
    assert cartera.saldo_documento(s, fv) == D("10710000")
    iva = impuestos.resumen_iva_bimestre(s, 2026, 5)
    assert iva.iva_generado_neto == D("1710000")
    assert iva.ingresos_gravados == D("9000000")
    cuadra(s)


def test_recaudo_parcial_y_varias_facturas(s):
    from app import cartera, contab
    from app.models import Banco, DocumentoVenta, Recaudo
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    importar(s, "2.xml", factura_venta("ALC-2", "2026-09-05", CLIENTE, 2_000_000))
    banco = s.query(Banco).first()
    rec = Recaudo(fecha=date(2026, 9, 20), cliente_id=s.query(DocumentoVenta).first().cliente_id, banco=banco,
                  valor=D("2000000"))
    s.add(rec)
    sugerencia, resto = cartera.aplicar_automatico(s, rec.cliente_id, rec.valor)
    assert [v for _, _, v in sugerencia] == [D("1190000"), D("810000")] and resto == 0
    cartera.registrar_aplicaciones(rec, {doc: v for doc, _, v in sugerencia})
    s.flush()
    contab.contabilizar_recaudo(s, rec)
    s.commit()
    f1, f2 = s.query(DocumentoVenta).order_by(DocumentoVenta.fecha).all()
    assert cartera.estado_documento(s, f1, date(2026, 9, 21)) == "Pagada"
    assert cartera.estado_documento(s, f2, date(2026, 9, 21)) == "Abonada"
    assert cartera.saldo_documento(s, f2) == D("2380000") - D("810000")
    filas, tot = cartera.cartera_por_edades(s, date(2026, 9, 21))
    assert tot["total"] == D("1570000")
    cuadra(s)


# ------------------------------------------------------------------ gastos

def test_importa_gasto_con_iva_descontable(s):
    from app.models import Gasto, Movimiento
    xml = factura_compra("INM-50", "2026-09-11", ARRENDADOR, 2_000_000, 380_000, vence="2026-10-02",
                         descripcion="ARRENDAMIENTO OFICINA MES DE SEPTIEMBRE 2026")
    r = importar(s, "g.xml", xml)
    assert r[0].ok, r[0].mensaje
    g = s.query(Gasto).one()
    assert g.categoria.nombre == "Arriendo de oficina"
    assert g.iva_descontable and g.forma_pago == "credito" and g.saldo == D("2380000")
    movs = {m.cuenta: (m.debito, m.credito) for m in s.query(Movimiento)}
    assert movs["512010"] == (D("2000000"), 0)
    assert movs["240810"] == (D("380000"), 0)
    assert movs["220505"] == (0, D("2380000"))
    cuadra(s)


def test_gasto_sin_iva_de_camara_de_comercio(s):
    from app.models import Gasto
    xml = factura_compra("CH0948", "2026-09-29", CAMARA, 612_000, 0, forma_pago="1",
                         descripcion="DERECHOS INSCRIPCION SOC. COMERCIALES; IMPUESTO DE REGISTRO(SIN CUANTIA)")
    importar(s, "c.xml", xml)
    g = s.query(Gasto).one()
    assert g.categoria.nombre == "Registro mercantil y cámara de comercio"
    assert g.forma_pago == "contado" and g.iva == 0
    cuadra(s)


def test_gasto_aprende_categoria_del_proveedor(s):
    from app.models import CategoriaGasto, Gasto
    importar(s, "1.xml", factura_compra("P-1", "2026-09-01", ARRENDADOR, 100_000, 19_000, descripcion="Servicio raro"))
    g = s.query(Gasto).one()
    assert not g.revisado
    g.categoria = s.query(CategoriaGasto).filter_by(nombre="Software y nube").one()
    g.revisado = True
    s.commit()
    importar(s, "2.xml", factura_compra("P-2", "2026-09-02", ARRENDADOR, 100_000, 19_000, descripcion="Otro"))
    g2 = s.query(Gasto).filter_by(numero="P-2").one()
    assert g2.categoria.nombre == "Software y nube" and g2.revisado


def test_pago_de_gasto_a_credito(cliente_web, s):
    from app.models import Gasto
    importar(s, "g.xml", factura_compra("INM-50", "2026-09-11", ARRENDADOR, 2_000_000, 380_000, vence="2026-10-02",
                                        descripcion="ARRENDAMIENTO"))
    g = s.query(Gasto).one()
    r = cliente_web.post(f"/gastos/{g.id}", data={"accion": "pago", "pago_fecha": "2026-10-01",
                                                   "pago_cuenta": "11200502", "pago_valor": "2380000"})
    assert r.status_code == 302
    s.expire_all()
    assert s.get(Gasto, g.id).saldo == 0
    cuadra(s)


# ------------------------------------------------------------------ impuestos

def _escenario(s):
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-15", CLIENTE, 10_000_000))
    importar(s, "2.xml", factura_venta("ALC-2", "2026-10-01", GRAN_CONTRIBUYENTE, 5_000_000, reteiva=142500))
    importar(s, "g.xml", factura_compra("INM-50", "2026-09-11", ARRENDADOR, 2_000_000, 380_000, vence="2026-10-02",
                                        descripcion="ARRENDAMIENTO OFICINA"))


def test_iva_bimestral(s):
    from app import impuestos
    _escenario(s)
    r = impuestos.resumen_iva_bimestre(s, 2026, 5)
    assert r.iva_generado_neto == D("2850000")
    assert r.iva_descontable == D("380000")
    assert r.reteiva == D("142500")
    assert r.neto == D("2327500")
    assert r.a_pagar == D("2328000")


def test_recibo_2593_y_pago(s):
    from app import contab, impuestos
    from app.models import Banco, PagoImpuesto
    _escenario(s)
    rec = impuestos.recibo_2593(s, 2026, 5)
    assert rec.ingresos == D("15000000")
    assert rec.tarifa == D("0.059")
    assert rec.anticipo_simple == D("885000")
    assert rec.total == D("885000") + D("2328000")
    assert rec.vencimiento == date(2026, 11, 20)
    p = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=date(2026, 11, 19), valor_simple=D("885000"),
                     valor_iva=D("2328000"), banco=s.query(Banco).first())
    s.add(p)
    s.flush()
    contab.contabilizar_pago_impuesto(s, p)
    s.commit()
    dec = impuestos.declaracion_simple(s, 2026)
    assert dec.anticipos == D("885000") and dec.impuesto == D("885000") and dec.saldo == 0
    impuestos.causar_simple_anual(s, 2026)
    s.commit()
    cuadra(s)


def test_tarifa_bimestral_por_tramos():
    from app import impuestos
    from app.config import SIMPLE_TARIFA_BIMESTRAL
    uvt = impuestos.uvt(2026)
    assert impuestos.tarifa(uvt * 999, 2026, SIMPLE_TARIFA_BIMESTRAL) == D("0.059")
    assert impuestos.tarifa(uvt * 1000, 2026, SIMPLE_TARIFA_BIMESTRAL) == D("0.073")  # límite inclusive
    assert impuestos.tarifa(uvt * 3000, 2026, SIMPLE_TARIFA_BIMESTRAL) == D("0.12")


def test_descuento_medios_electronicos(s):
    from app import cartera, contab, impuestos
    from app.models import Banco, DocumentoVenta, Recaudo
    importar(s, "1.xml", factura_venta("ALC-1", "2026-03-01", CLIENTE, 10_000_000))
    doc = s.query(DocumentoVenta).one()
    rec = Recaudo(fecha=date(2026, 3, 10), cliente_id=doc.cliente_id, banco=s.query(Banco).first(),
                  valor=D("11900000"), medio_electronico=True)
    s.add(rec)
    cartera.registrar_aplicaciones(rec, {doc: D("11900000")})
    s.flush()
    contab.contabilizar_recaudo(s, rec)
    dec = impuestos.declaracion_simple(s, 2026)
    assert dec.ingresos_medios_electronicos == D("10000000")
    assert dec.descuento_medios_electronicos == D("50000")


def test_base_caja(s):
    from app import contab, impuestos
    _escenario(s)
    contab.set_config(s, "simple_base", "caja")
    assert impuestos.ingresos_brutos(s, date(2026, 9, 1), date(2026, 10, 31)) == 0


# ------------------------------------------------------------------ estados financieros

def test_estados_financieros_cuadran(s):
    from app import reportes
    _escenario(s)
    filas, td, tc = reportes.balance_de_prueba(s, date(2026, 1, 1), date(2026, 12, 31))
    assert td == tc and td > 0
    bg = reportes.balance_general(s, date(2026, 12, 31))
    assert bg["cuadre"] == 0
    er = reportes.estado_resultados(s, date(2026, 1, 1), date(2026, 12, 31))
    assert er["ingresos_op"] == D("15000000") and er["gastos_admin"] == D("2000000")


def test_exogena(s):
    from app import exogena
    _escenario(s)
    hojas, incompletos = exogena.generar(s, 2026)
    assert len(hojas["1007 Ingresos"][1]) == 2
    assert hojas["1005 IVA descontable"][1][0][-2] == D("380000")
    assert hojas["1001 Pagos"][1][0][0] == "5005"


def test_respaldo(s, tmp_path):
    from app import contab, respaldo
    contab.set_config(s, "carpeta_respaldo", str(tmp_path / "resp"))
    s.commit()
    ruta = respaldo.crear_respaldo(s)
    assert ruta.exists() and "contabilidad.db" in zipfile.ZipFile(ruta).namelist()
    assert not respaldo.respaldo_pendiente(s)


# ------------------------------------------------------------------ interfaz

def test_flujo_web_completo(cliente_web, s):
    data = {"archivos": [(io.BytesIO(factura_venta("ALC-1", "2026-09-15", CLIENTE, 10_000_000).encode()), "a.xml"),
                         (io.BytesIO(factura_compra("INM-50", "2026-09-11", ARRENDADOR, 2_000_000, 380_000,
                                                    descripcion="ARRENDAMIENTO").encode()), "b.xml")]}
    r = cliente_web.post("/ventas/importar", data=data, content_type="multipart/form-data")
    assert r.status_code == 200 and r.data.decode().count("Importado") == 2

    from app.models import DocumentoVenta
    doc = s.query(DocumentoVenta).one()
    r = cliente_web.post(f"/ventas/{doc.id}", data={"accion": "guardar", "reteiva_aplica": "on",
                                                    "reteiva_valor": "285000", "reteiva_fecha": "2026-09-20",
                                                    "vencimiento": "2026-10-15", "recordar_reteiva": "on"})
    assert r.status_code == 302
    s.expire_all()
    doc = s.get(DocumentoVenta, doc.id)
    assert doc.reteiva_valor == D("285000") and doc.cliente.aplica_reteiva

    r = cliente_web.post("/recaudos/nuevo", data={"cliente_id": doc.cliente_id, "guardar": "1", "fecha": "2026-09-25",
                                                  "banco_id": "1", "valor": "11.615.000",
                                                  f"aplicar_{doc.id}": "11615000", "medio_electronico": "on"})
    assert r.status_code == 302
    r = cliente_web.post("/gastos/nuevo", data={"tipo_soporte": "RE", "fecha": "2026-09-12", "proveedor_id": "nuevo",
                                                "nuevo_nit": "79000000", "nuevo_nombre": "TAXI JUAN",
                                                "nuevo_tipo_doc": "13", "categoria_id": "1", "subtotal": "25.000",
                                                "iva": "0", "otros_impuestos": "0", "forma_pago": "contado",
                                                "cuenta_pago": "110505"})
    assert r.status_code == 302
    r = cliente_web.post("/contabilidad/asiento/nuevo", data={
        "fecha": "2026-01-02", "descripcion": "Aporte de capital",
        "cuenta": ["11200501", "310505"], "tercero": ["", ""], "detalle": ["", ""],
        "debito": ["10.000.000", ""], "credito": ["", "10.000.000"]})
    assert r.status_code == 302

    for ruta in ["/", "/ventas", f"/ventas/{doc.id}", "/recaudos", "/cartera", f"/cartera/{doc.cliente_id}",
                 "/certificados", "/gastos", "/gastos/1", "/terceros", "/terceros/1", "/impuestos/iva",
                 "/impuestos/iva/2026/5", "/impuestos/simple?anio=2026", "/impuestos/f260?anio=2026",
                 "/impuestos/f300?anio=2026", "/impuestos/exogena?anio=2026", "/impuestos/calendario",
                 "/contabilidad/diario?desde=2026-01-01&hasta=2026-12-31", "/contabilidad/mayor?cuenta=13",
                 "/contabilidad/balance-prueba?desde=2026-01-01&hasta=2026-12-31",
                 "/contabilidad/resultados?desde=2026-01-01&hasta=2026-12-31",
                 "/contabilidad/balance?corte=2026-12-31", "/contabilidad/asiento/1", "/configuracion/"]:
        assert cliente_web.get(ruta).status_code == 200, ruta

    pdf = cliente_web.get(f"/cartera/{doc.cliente_id}?pdf=1")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    for ruta in ["/ventas?anio=2026&xlsx=1", "/gastos?anio=2026&xlsx=1", "/impuestos/exogena?anio=2026&xlsx=1",
                 "/impuestos/iva/2026/5?xlsx=1", "/contabilidad/diario?xlsx=1&desde=2026-01-01&hasta=2026-12-31",
                 "/contabilidad/balance-prueba?xlsx=1", "/cartera?xlsx=1"]:
        r = cliente_web.get(ruta)
        assert r.status_code == 200 and r.data[:2] == b"PK", ruta
    from app import reportes
    assert reportes.balance_general(s, date(2026, 12, 31))["cuadre"] == 0


def test_cierre_implicito_anio_siguiente(s):
    from app import reportes
    _escenario(s)  # ingresos 15M, gastos 2M en 2026
    filas, td, tc = reportes.balance_de_prueba(s, date(2027, 1, 1), date(2027, 12, 31))
    saldos = {f.cuenta.codigo: f for f in filas}
    assert saldos["370505"].saldo_inicial == D("13000000")
    assert "4" not in saldos and "5" not in saldos
    activo = saldos["1"].saldo_inicial
    assert activo == saldos["2"].saldo_inicial + saldos["3"].saldo_inicial
    bg = reportes.balance_general(s, date(2027, 6, 30))
    assert bg["anteriores"] == D("13000000") and bg["resultado"] == 0 and bg["cuadre"] == 0


def test_causacion_simple_no_cruza_anticipo_de_enero(s):
    from app import contab, impuestos, reportes
    from app.models import Banco, PagoImpuesto
    importar(s, "1.xml", factura_venta("ALC-1", "2026-11-15", CLIENTE, 10_000_000))
    p = PagoImpuesto(formulario="2593", anio=2026, bimestre=6, fecha=date(2027, 1, 21), valor_simple=D("590000"),
                     valor_iva=D("1900000"), banco=s.query(Banco).first())
    s.add(p)
    s.flush()
    impuestos.causar_simple_anual(s, 2026)
    s.commit()
    contab.contabilizar_pago_impuesto(s, p)
    s.commit()
    assert reportes.saldo_cuenta(s, "135595", date(2026, 12, 31)) == 0
    assert reportes.saldo_cuenta(s, "240405", date(2026, 12, 31)) == D("-590000")  # pasivo al cierre
    assert reportes.saldo_cuenta(s, "240405") == 0  # cancelado con el pago de enero


def test_nc_sin_factura_registrada_se_rechaza(s):
    nc = documento(tipo="CreditNote", numero="NC-9", cufe="cude-nc9", fecha="2026-09-30", emisor=EMPRESA,
                   receptor=CLIENTE, base=1_000_000, iva=190_000, referencia=("ALC-404", "cufe-404"))
    r = importar(s, "nc.xml", nc)
    assert not r[0].ok and "ALC-404" in r[0].mensaje


def test_no_anular_factura_con_recaudo(cliente_web, s):
    from app import cartera, contab
    from app.models import Banco, DocumentoVenta, Recaudo
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    doc = s.query(DocumentoVenta).one()
    rec = Recaudo(fecha=date(2026, 9, 20), cliente_id=doc.cliente_id, banco=s.query(Banco).first(), valor=D("1190000"))
    s.add(rec)
    cartera.registrar_aplicaciones(rec, {doc: D("1190000")})
    s.flush()
    contab.contabilizar_recaudo(s, rec)
    s.commit()
    r = cliente_web.post(f"/ventas/{doc.id}", data={"accion": "guardar", "anulada": "on"})
    assert r.status_code == 302
    from app.db import Session
    Session.remove()
    assert not Session().query(DocumentoVenta).one().anulada


def test_nit_con_formato_y_encoding_interno(s):
    from app import contab
    from app.dian_xml import leer_xml, nit_limpio
    from app.models import Gasto, Tercero
    assert nit_limpio("901.913.577-4", "4") == "901913577"
    assert nit_limpio("9019135774", "4") == "901913577"
    assert nit_limpio("800 123 456") == "800123456"
    contab.set_config(s, "empresa_nit", "901.913.577-4")
    s.commit()
    r = importar(s, "g.xml", factura_compra("P-7", "2026-09-01", ("800 123 456", "1", "PAÑALERA LÓPEZ"), 100_000, 19_000))
    assert r[0].ok, r[0].mensaje
    assert s.query(Tercero).filter_by(nit="800123456").one().nombre == "PAÑALERA LÓPEZ"
    # XML interno declarado en ISO-8859-1 dentro de un contenedor UTF-8
    from ubl import contenedor, documento
    interno = documento(numero="P-8", cufe="c8", fecha="2026-09-02", emisor=("800000009", "1", "PAÑALERA LÓPEZ"),
                        receptor=EMPRESA, base=100, iva=19).replace('encoding="utf-8"', 'encoding="ISO-8859-1"')
    doc = leer_xml(contenedor(interno, "P-8", "c8", "2026-09-02", ("800000009", "1", "X"), EMPRESA))
    assert doc.emisor.nombre == "PAÑALERA LÓPEZ"
    # duplicado sin CUFE
    sin = documento(numero="SC-1", cufe="", fecha="2026-09-03", emisor=("800000009", "1", "X"), receptor=EMPRESA,
                    base=100, iva=0)
    assert importar(s, "a.xml", sin)[0].ok
    assert not importar(s, "a.xml", sin)[0].ok
    assert s.query(Gasto).filter_by(numero="SC-1").count() == 1


def test_zip_dian_varios_documentos():
    from app.dian_xml import leer_archivo
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ad0901913577001.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 100))
        z.writestr("fv0901913577001.pdf", b"%PDF-1")
        z.writestr("ad0901913577002.xml", factura_venta("ALC-2", "2026-09-01", CLIENTE, 100))
        z.writestr("fv0901913577002.pdf", b"%PDF-2")
        z.writestr("__MACOSX/._ad0901913577001.xml", b"basura")
        z.writestr("carpeta/__MACOSX/._x.xml", b"basura")
    leidos = leer_archivo("lote.zip", buf.getvalue())
    assert [l.documento.numero for l in leidos] == ["ALC-1", "ALC-2"]
    assert [l.pdf for l in leidos] == [b"%PDF-1", b"%PDF-2"]
    anidado = io.BytesIO()
    with zipfile.ZipFile(anidado, "w") as z:
        z.writestr("interno.zip", buf.getvalue())
    assert len(leer_archivo("lote2.zip", anidado.getvalue())) == 2


def test_categoria_plural_y_proveedor(s):
    from app.importacion import sugerir_categoria
    from app.models import Tercero
    assert sugerir_categoria(s, None, "INTERESES DE MORA")[0].nombre == "Intereses"
    assert sugerir_categoria(s, None, "Se envia certificado de tradicion")[0].nombre == "Otros gastos"
    prov = Tercero(nit="1", nombre="MICROSOFT COLOMBIA", es_proveedor=True)
    s.add(prov)
    s.flush()
    assert sugerir_categoria(s, prov, "Licencia de construccion")[0].nombre == "Software y nube"


def test_nombre_seguro_conserva_extension():
    from app.archivos import nombre_seguro
    assert nombre_seguro("факту́ра.xml") == "archivo.xml"
    assert nombre_seguro("..xml") == "archivo.xml"
    assert nombre_seguro("C:\\Users\\x\\factura 電子.pdf") == "factura.pdf"
    assert nombre_seguro("../../etc/passwd") == "passwd"


def test_editar_recaudo_aplica_anticipo(cliente_web, s):
    from app import cartera, reportes
    from app.models import DocumentoVenta, Recaudo
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    doc = s.query(DocumentoVenta).one()
    r = cliente_web.post("/recaudos/nuevo", data={"cliente_id": doc.cliente_id, "guardar": "1", "fecha": "2026-08-20",
                                                  "banco_id": "1", "valor": "1190000"})  # anticipo sin aplicar
    assert r.status_code == 302
    rec = s.query(Recaudo).one()
    assert rec.sin_aplicar == D("1190000") and reportes.saldo_cuenta(s, "280505") == D("-1190000")
    assert cliente_web.get(f"/recaudos/{rec.id}").status_code == 200
    r = cliente_web.post(f"/recaudos/{rec.id}", data={"guardar": "1", "fecha": "2026-08-20", "banco_id": "1",
                                                      "valor": "1190000", f"aplicar_{doc.id}": "1190000"})
    assert r.status_code == 302
    s.expire_all()
    doc = s.get(DocumentoVenta, doc.id)
    assert cartera.saldo_documento(s, doc) == 0
    assert reportes.saldo_cuenta(s, "280505") == 0
    assert reportes.saldo_cuenta(s, "130505") == 0


def test_formularios_no_fallan_con_datos_invalidos(cliente_web, s):
    """Casos que antes producían error 500; ahora responden con mensaje y redirección."""
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    from app.models import Banco, CategoriaGasto, DocumentoVenta, Tercero
    doc = s.query(DocumentoVenta).one()
    # banco nuevo en configuración
    assert cliente_web.post("/configuracion/", data={"accion": "banco", "nombre": "Davivienda"}).status_code == 302
    assert s.query(Banco).filter_by(nombre="Davivienda").one().cuenta == "11200503"
    # categoría duplicada
    n = s.query(CategoriaGasto).count()
    assert cliente_web.post("/configuracion/", data={"accion": "categoria", "nombre": "Energía",
                                                     "cuenta": "519595"}).status_code == 302
    assert s.query(CategoriaGasto).count() == n
    # reteIVA con texto, adjunto no permitido, nota crédito que impide eliminar
    r = cliente_web.post(f"/ventas/{doc.id}", data={"accion": "guardar", "reteiva_aplica": "on",
                                                    "reteiva_valor": "abc"})
    assert r.status_code == 302
    r = cliente_web.post(f"/ventas/{doc.id}", data={"accion": "guardar", "cert_archivo": (io.BytesIO(b"x"), "c.docx")},
                         content_type="multipart/form-data")
    assert r.status_code == 302
    r = cliente_web.post("/ventas/nueva", data={"tipo": "NC", "numero": "NC-1", "fecha": "2026-09-02",
                                                "cliente_id": doc.cliente_id, "referencia_id": doc.id,
                                                "base": "100.000", "iva": "19.000"})
    assert r.status_code == 302
    assert cliente_web.post(f"/ventas/{doc.id}", data={"accion": "eliminar"}).status_code == 302
    assert s.query(DocumentoVenta).filter_by(id=doc.id).count() == 1
    # asiento manual con montos mal escritos
    r = cliente_web.post("/contabilidad/asiento/nuevo", data={"fecha": "2026-01-02", "descripcion": "x",
                                                              "cuenta": ["11200501", "310505"], "tercero": ["x", ""],
                                                              "detalle": ["", ""], "debito": ["abc", ""],
                                                              "credito": ["", "1"]})
    assert r.status_code == 200 and "No se pudo guardar" in r.data.decode()
    # tercero con NIT en blanco
    t = s.query(Tercero).first()
    assert cliente_web.post(f"/terceros/{t.id}", data={"nit": "  ", "nombre": "", "plazo_dias": "abc"}).status_code == 200
    assert cliente_web.get("/").status_code == 200
    # años fuera de rango y calendario
    for ruta in ["/ventas?anio=0", "/recaudos?anio=20226", "/impuestos/iva?anio=99999", "/impuestos/simple?anio=0"]:
        assert cliente_web.get(ruta).status_code == 200, ruta
    assert cliente_web.get("/impuestos/iva/0/1").status_code == 404
    assert cliente_web.post("/impuestos/calendario", data={"accion": "agregar", "obligacion": "x", "fecha": ""}).status_code == 302
    assert cliente_web.post("/impuestos/calendario", data={"eliminar": "9999"}).status_code == 302
    # formulario de gasto con monto inválido conserva lo escrito
    r = cliente_web.post("/gastos/nuevo", data={"tipo_soporte": "FE", "fecha": "2026-09-12", "proveedor_id": "",
                                                "categoria_id": "1", "subtotal": "abc", "forma_pago": "credito"})
    assert r.status_code == 200 and 'value="credito" selected' in r.data.decode()


def test_bloqueo_de_periodo_e_historial(cliente_web, s):
    from app import contab
    from app.models import Bitacora, DocumentoVenta, Gasto
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    importar(s, "g.xml", factura_compra("P-1", "2026-09-05", ARRENDADOR, 100_000, 19_000, descripcion="ARRIENDO"))
    # al pagar el 2593 del bimestre 5 queda bloqueado hasta el 31-10-2026
    r = cliente_web.post("/impuestos/pago", data={"formulario": "2593", "anio": "2026", "bimestre": "5",
                                                  "fecha": "2026-11-15", "valor_simple": "100.000",
                                                  "valor_iva": "0", "banco_id": "1"})
    assert r.status_code == 302
    assert contab.periodo_bloqueado_hasta(s) == date(2026, 10, 31)
    # no se puede borrar ni editar un gasto de septiembre, ni importar documentos de esas fechas
    g = s.query(Gasto).one()
    cliente_web.post(f"/gastos/{g.id}", data={"accion": "eliminar"})
    assert s.query(Gasto).count() == 1
    r = importar(s, "2.xml", factura_venta("ALC-2", "2026-10-15", CLIENTE, 1_000_000))
    assert not r[0].ok and "bloqueado" in r[0].mensaje
    assert importar(s, "3.xml", factura_venta("ALC-3", "2026-11-02", CLIENTE, 1_000_000))[0].ok
    # el historial registra creación y pago
    entidades = {(b.accion, b.entidad) for b in s.query(Bitacora)}
    assert ("crear", "Factura de venta") in entidades and ("crear", "Pago de impuesto") in entidades
    assert cliente_web.get("/historial").status_code == 200
    assert cliente_web.get("/buscar?q=ALC-1").data.decode().count("ALC-1") >= 1


def test_correo_imap_simulado(s, monkeypatch):
    """Lee un mensaje con ZIP adjunto desde un buzón IMAP simulado y registra el gasto una sola vez."""
    from email.message import EmailMessage
    from app import correo
    from app.models import CorreoProcesado, Gasto
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ad123.xml", factura_compra("MAIL-1", "2026-09-07", ARRENDADOR, 500_000, 95_000,
                                               descripcion="ARRENDAMIENTO"))
    msg = EmailMessage()
    msg["Subject"] = "Factura electrónica CCI"
    msg["Message-ID"] = "<abc@proveedor>"
    msg.set_content("Adjunto")
    msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip", filename="factura.zip")
    crudo = msg.as_bytes()

    class Imap:
        def select(self, *a, **k): return "OK", [b"1"]
        def search(self, *a): return "OK", [b"1"]
        def fetch(self, uid, que):
            if "HEADER" in que:
                return "OK", [(b"1", b"Message-ID: <abc@proveedor>\r\nSubject: Factura\r\n\r\n")]
            return "OK", [(b"1", crudo)]
        def logout(self): pass

    monkeypatch.setattr(correo, "conectar", lambda session: (Imap(), {"correo_carpeta": "INBOX", "correo_dias": "30",
                                                                       "correo_filtro": ""}))
    r = correo.revisar(s)
    assert r.error is None and r.importados == 1 and r.revisados == 1
    assert s.query(Gasto).filter_by(numero="MAIL-1").one().origen == "correo"
    r2 = correo.revisar(s)
    assert r2.revisados == 0 and s.query(CorreoProcesado).count() == 1


def test_carpeta_vigilada_duplicar_y_lote(cliente_web, s, tmp_path):
    from app import contab, vigilancia
    from app.models import Gasto
    carpeta = tmp_path / "vigilada"
    carpeta.mkdir()
    contab.set_config(s, "carpeta_vigilada", str(carpeta))
    s.commit()
    (carpeta / "a.xml").write_text(factura_compra("V-1", "2026-09-06", ARRENDADOR, 100_000, 19_000,
                                                  descripcion="ARRENDAMIENTO"))
    (carpeta / "malo.xml").write_text("no es xml")
    res = vigilancia.revisar(s)
    assert [ok for _, ok, _ in res] == [True, False]
    assert (carpeta / "importados" / "a.xml").exists() and (carpeta / "errores" / "malo.xml.txt").exists()
    g = s.query(Gasto).one()
    # duplicar prellena el formulario; revisión en lote marca como revisado
    assert 'value="100000"' in cliente_web.get(f"/gastos/nuevo?copiar={g.id}").data.decode()
    assert not g.revisado
    r = cliente_web.post("/gastos/revisar", data={"id": [str(g.id)], f"ok_{g.id}": "on", f"categoria_{g.id}": "1",
                                                  f"iva_{g.id}": "on", f"forma_{g.id}": "contado",
                                                  f"cuenta_{g.id}": "11200501", f"rec_{g.id}": "on"})
    assert r.status_code == 302
    s.expire_all()
    g = s.get(Gasto, g.id)
    assert g.revisado and g.recurrente and g.forma_pago == "contado"


def test_pago_rapido_y_retainer(cliente_web, s):
    from app import cartera
    from app.models import DocumentoVenta, Tercero
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    doc = s.query(DocumentoVenta).one()
    r = cliente_web.post(f"/ventas/{doc.id}/pagar", data={"banco_id": "1", "fecha": "2026-09-20"})
    assert r.status_code == 302
    s.expire_all()
    assert cartera.saldo_documento(s, s.get(DocumentoVenta, doc.id)) == 0
    t = s.query(Tercero).one()
    t.retainer_mensual = D("2000000")
    s.commit()
    assert "Honorarios mensuales sin facturar" in cliente_web.get("/").data.decode()


def test_depreciacion_flujo_y_cierre(cliente_web, s):
    from app import contab, impuestos, planeacion, reportes
    from app.models import Gasto
    _escenario(s)
    importar(s, "pc.xml", factura_compra("PC-1", "2026-03-10", ("800000009", "1", "TIENDA PC"), 3_000_000, 570_000,
                                         descripcion="Computador portatil"))
    g = s.query(Gasto).filter_by(numero="PC-1").one()
    assert g.categoria.cuenta == "152805" and not g.iva_descontable
    assert planeacion.causar_depreciaciones(s, date(2026, 10, 31)) == 7
    assert planeacion.causar_depreciaciones(s, date(2026, 10, 31)) == 0  # idempotente
    assert reportes.saldo_cuenta(s, "159220") == -D("59500") * 7
    filas, promedio = planeacion.flujo_de_caja(s, 6, date(2026, 10, 3))
    assert filas[0].cobros_vencidos + filas[0].cobros > 0 and filas[1].impuestos > 0
    assert cliente_web.get("/flujo-caja").status_code == 200
    proy = impuestos.proyeccion_anual(s, 2026, date(2026, 10, 3))
    assert proy.ingresos_proyectados > proy.ingresos_a_la_fecha and proy.simple_proyectado > 0
    # arrastre de saldo a favor de IVA
    contab.set_config(s, "iva_arrastre_saldo_favor", "si")
    s.commit()
    assert impuestos.resumen_iva_bimestre(s, 2026, 3).saldo_favor_siguiente == D("570000") * 0  # IVA del PC no descontable
    # cierre del año
    r = cliente_web.post("/contabilidad/cierre", data={"anio": "2026", "accion": "cerrar"})
    assert r.status_code == 302 and planeacion.anio_cerrado(s, 2026)
    assert contab.periodo_bloqueado_hasta(s) == date(2026, 12, 31)
    er = reportes.estado_resultados(s, date(2026, 1, 1), date(2026, 12, 31))
    assert er["ingresos_op"] == D("15000000")  # el cierre no altera el estado de resultados
    assert reportes.balance_general(s, date(2026, 12, 31))["cuadre"] == 0
    filas, _, _ = reportes.balance_de_prueba(s, date(2027, 1, 1), date(2027, 12, 31))
    clases = {f.cuenta.codigo: f.saldo_inicial for f in filas if len(f.cuenta.codigo) == 1}
    assert clases["1"] == clases["2"] + clases["3"] and "4" not in clases
    assert reportes.balance_general(s, date(2027, 6, 30))["cuadre"] == 0
    assert cliente_web.post("/contabilidad/cierre", data={"anio": "2026", "accion": "reabrir"}).status_code == 302
    assert not planeacion.anio_cerrado(s, 2026)


def test_conciliacion_bancaria(cliente_web, s):
    from app import bancos, reportes
    from app.models import Banco, DocumentoVenta, Gasto, MovimientoBanco, Recaudo
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    importar(s, "g.xml", factura_compra("INM-50", "2026-09-11", ARRENDADOR, 2_000_000, 380_000, vence="2026-10-02",
                                        descripcion="ARRENDAMIENTO"))
    doc = s.query(DocumentoVenta).one()
    cliente_web.post(f"/ventas/{doc.id}/pagar", data={"banco_id": "1", "fecha": "2026-09-20"})
    extracto = ("Fecha;Descripción;Valor;Referencia\n"
                "20/09/2026;TRANSFERENCIA CLIENTE DEMO;1.190.000,00;123\n"
                "21/09/2026;GMF 4X1000;-4.760,00;\n"
                "22/09/2026;COMISION TRANSFERENCIA;-6.000,00;\n"
                "25/09/2026;PAGO PSE INMOBILIARIA;-2.380.000,00;\n"
                "26/09/2026;ABONO INTERESES;15.000,00;\n"
                "27/09/2026;CONSIGNACION NUEVO CLIENTE;500.000,00;\n").encode("latin-1")
    banco = s.get(Banco, 1)
    nuevas, repetidas = bancos.importar_extracto(s, banco, "extracto.csv", extracto)
    assert (nuevas, repetidas) == (6, 0)
    assert bancos.importar_extracto(s, banco, "extracto.csv", extracto) == (0, 6)
    assert bancos.conciliar_automatico(s, 1) == 1  # el recaudo de 1.190.000
    movs = {m.descripcion: m for m in s.query(MovimientoBanco)}
    assert movs["TRANSFERENCIA CLIENTE DEMO"].estado == "conciliado"
    # 4x1000 → gasto sugerido automáticamente
    m = movs["GMF 4X1000"]
    assert bancos.sugerir_categoria_bancaria(m.descripcion) == "Gravamen 4x1000"
    cat = [c for c in s.query(__import__("app.models", fromlist=["CategoriaGasto"]).CategoriaGasto)
           if c.nombre == "Gravamen 4x1000"][0]
    r = cliente_web.post(f"/bancos/movimiento/{m.id}", data={"accion": "gasto", "categoria_id": cat.id})
    assert r.status_code == 302
    s.expire_all()
    assert s.get(MovimientoBanco, m.id).estado == "conciliado"
    assert reportes.saldo_cuenta(s, "511595") == D("4760")
    # pago de la factura del arrendador
    g = s.query(Gasto).filter_by(numero="INM-50").one()
    m = movs["PAGO PSE INMOBILIARIA"]
    r = cliente_web.post(f"/bancos/movimiento/{m.id}", data={"accion": "pago_proveedor", "gasto_id": g.id})
    assert r.status_code == 302
    s.expire_all()
    assert s.get(Gasto, g.id).saldo == 0
    # recaudo de un cliente (anticipo, no hay facturas) y rendimientos
    m = movs["CONSIGNACION NUEVO CLIENTE"]
    r = cliente_web.post(f"/bancos/movimiento/{m.id}", data={"accion": "recaudo", "cliente_id": doc.cliente_id})
    assert r.status_code == 302 and s.query(Recaudo).count() == 2
    m = movs["ABONO INTERESES"]
    assert cliente_web.post(f"/bancos/movimiento/{m.id}", data={"accion": "ingreso", "cuenta": "421005"}).status_code == 302
    assert reportes.saldo_cuenta(s, "421005") == D("-15000")
    assert cliente_web.post(f"/bancos/movimiento/{movs['COMISION TRANSFERENCIA'].id}", data={"accion": "ignorar"}).status_code == 302
    assert s.query(MovimientoBanco).filter_by(estado="pendiente").count() == 0
    assert cliente_web.get("/bancos/conciliacion?estado=todos").status_code == 200
    # extracto con débito/crédito en columnas separadas, formato Excel
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Extracto Banco Caja Social"])
    ws.append(["FECHA", "CONCEPTO", "DEBITOS", "CREDITOS"])
    ws.append([date(2026, 9, 28), "Pago nómina", 100000, None])
    ws.append([date(2026, 9, 29), "Consignación", None, 250000])
    buf = io.BytesIO()
    wb.save(buf)
    assert bancos.importar_extracto(s, s.get(Banco, 2), "e.xlsx", buf.getvalue()) == (2, 0)
    vals = sorted(m.valor for m in s.query(MovimientoBanco).filter_by(banco_id=2))
    assert vals == [D("-100000"), D("250000")]


def test_acceso_red_actualizacion_y_restauracion(cliente_web, s, tmp_path):
    from pathlib import Path
    from app import config, contab, respaldo, sistema
    from app.db import Session, engine
    from app.models import DocumentoVenta
    # Desde el PC local nunca pide clave; desde la red solo si está activado
    assert cliente_web.get("/").status_code == 200
    remoto = {"REMOTE_ADDR": "192.168.1.20"}
    assert cliente_web.get("/", environ_base=remoto).status_code == 403
    contab.set_config(s, "acceso_red", "si")
    contab.set_config(s, "clave_acceso", "secreta")
    s.commit()
    assert cliente_web.get("/", environ_base=remoto).status_code == 302
    assert "incorrecta" in cliente_web.post("/acceso", data={"clave": "x"}, environ_base=remoto).data.decode()
    assert cliente_web.post("/acceso", data={"clave": "secreta"}, environ_base=remoto).status_code == 302
    assert cliente_web.get("/ventas", environ_base=remoto).status_code == 200
    # Versiones y preparación de una actualización descargada
    assert sistema.es_mas_nueva("v1.10.0", "1.9.9") and not sistema.es_mas_nueva("1.2.0", "1.2.0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("repo-abc/app/__init__.py", "VERSION = '9'")
    script = sistema.preparar_actualizacion(buf.getvalue(), tmp_path / "act")
    assert script.exists() and "robocopy" in script.read_text()
    # Restauración de un respaldo al reiniciar
    importar(s, "1.xml", factura_venta("ALC-1", "2026-09-01", CLIENTE, 1_000_000))
    contab.set_config(s, "carpeta_respaldo", str(tmp_path / "resp"))
    s.commit()
    z = respaldo.crear_respaldo(s)
    s.query(DocumentoVenta).delete()
    s.commit()
    r = cliente_web.post("/configuracion/", data={"accion": "restaurar", "respaldo_existente": z.name})
    assert r.status_code == 302 and (config.DATOS_DIR / "restaurar.zip").exists()
    Session.remove()
    engine.dispose()
    from app import create_app
    app2 = create_app(config.DATOS_DIR)
    assert app2.config["RESTAURADO"] and Session().query(DocumentoVenta).count() == 1
    assert list(Path(config.DATOS_DIR).glob("contabilidad_antes_de_restaurar_*.db"))
