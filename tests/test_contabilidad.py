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
    assert impuestos.tarifa(uvt * 1000, 2026, SIMPLE_TARIFA_BIMESTRAL) == D("0.059")
    assert impuestos.tarifa(uvt * 1001, 2026, SIMPLE_TARIFA_BIMESTRAL) == D("0.073")
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
