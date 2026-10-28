"""Pruebas aportadas por la revisión adversarial de la 1.4.5 (cobertura de las correcciones 1.4.3-1.4.5)."""
import zipfile, io, re
from datetime import date
from decimal import Decimal

from app import contab, informes, planeacion, vigilancia
from app.models import Asiento, Banco, CategoriaGasto, DocumentoVenta, Gasto, MovimientoBanco, PagoGasto, PagoImpuesto, Tercero
from app.reportes import saldo_cuenta
from tests.test_contabilidad import ARRENDADOR, GRAN_CONTRIBUYENTE, _escenario, cuadra, importar
from tests.test_informes import _cliente, _factura, _gasto
from ubl import factura_compra, factura_venta

D = Decimal


def _dos_pagos(s):
    _escenario(s)
    banco = s.query(Banco).first()
    pagos = []
    for fecha, simple, iva in ((date(2026, 11, 19), D("885000"), D("2328000")), (date(2026, 11, 25), D("10000"), D("0"))):
        p = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=fecha, valor_simple=simple, valor_iva=iva, banco=banco)
        s.add(p)
        s.flush()
        contab.contabilizar_pago_impuesto(s, p)
        pagos.append(p)
    s.commit()
    return pagos


def test_T1_eliminar_complementario_no_reescribe_principal_aunque_cambie_la_reteiva(cliente_web, s):
    p1, p2 = _dos_pagos(s)
    # Después de pagar llega una factura más del bimestre con reteIVA: la reclasificación "actual" ya no coincide
    importar(s, "3.xml", factura_venta("ALC-3", "2026-10-20", GRAN_CONTRIBUYENTE, 1_000_000, reteiva=28500))
    contab.set_config(s, "periodo_bloqueado_hasta", "")
    s.commit()
    a1 = s.query(Asiento).filter_by(origen=f"impuesto:{p1.id}").one()
    antes = (a1.id, [(m.cuenta, m.debito, m.credito) for m in a1.lineas])
    p1id, p2id = p1.id, p2.id
    r = cliente_web.post(f"/impuestos/pago/{p2id}/eliminar", follow_redirects=True)
    assert "Pago eliminado" in r.get_data(as_text=True)
    s.expire_all()
    a1 = s.query(Asiento).filter_by(origen=f"impuesto:{p1id}").one()
    assert (a1.id, [(m.cuenta, m.debito, m.credito) for m in a1.lineas]) == antes


def test_T9_recontabilizar_en_periodo_bloqueado_avisa_y_no_borra(cliente_web, s):
    _escenario(s)
    banco = s.query(Banco).first()
    p1 = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=date(2026, 12, 10), valor_simple=D("885000"),
                      valor_iva=D("2328000"), banco=banco)
    s.add(p1); s.flush(); contab.contabilizar_pago_impuesto(s, p1)
    p2 = PagoImpuesto(formulario="2593", anio=2026, bimestre=5, fecha=date(2026, 11, 25), valor_simple=D("10000"),
                      valor_iva=D("0"), banco=banco)
    s.add(p2); s.flush(); contab.contabilizar_pago_impuesto(s, p2)
    s.commit()
    contab.set_config(s, "periodo_bloqueado_hasta", "2026-11-30")
    s.commit()
    p1id = p1.id
    r = cliente_web.post(f"/impuestos/pago/{p1id}/eliminar", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "pasaría al pago del 25/11/2026" in html
    s.expire_all()
    assert s.get(PagoImpuesto, p1id) is not None
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == 0
    cuadra(s)


def test_T2_flujo_pago_de_activo_a_credito_es_inversion(s):
    prov = Tercero(nit="800111222", nombre="Proveedor", es_proveedor=True)
    cat = CategoriaGasto(nombre="Computadores", cuenta="152805", concepto_exogena="5016")
    s.add_all([prov, cat]); s.flush()
    banco = s.query(Banco).first()
    g = _gasto(s, cat, D("2380000"), date(2026, 2, 10), forma="credito", proveedor=prov)
    p = PagoGasto(fecha=date(2026, 3, 10), cuenta_pago=banco.cuenta, valor=D("2380000"))
    g.pagos.append(p); s.flush()
    contab.contabilizar_pago_gasto(s, p)
    s.commit()
    flujo = informes.flujo_de_efectivo(s, date(2026, 1, 1), date(2026, 12, 31))
    secciones = {sec["nombre"]: dict(sec["filas"]) for sec in flujo["secciones"]} if isinstance(flujo, dict) else None
    assert secciones is not None, flujo
    assert secciones.get("Actividades de inversión", {}).get("Compra de activos fijos") == D("-2380000"), secciones
    assert "Pagos a proveedores y gastos (incluido su IVA)" not in secciones.get("Actividades de operación", {}), secciones


def test_T3_importar_gasto_credito_por_vencimiento(s):
    res = importar(s, "a.xml", factura_compra("INM-1", "2026-03-01", ARRENDADOR, 100000, 19000, forma_pago="1", vence="2026-04-10"))
    assert res[0].ok, res[0].mensaje
    g = s.query(Gasto).one()
    assert g.forma_pago == "credito" and g.vencimiento == date(2026, 4, 10)
    res = importar(s, "b.xml", factura_compra("INM-2", "2026-03-01", ARRENDADOR, 100000, 19000, forma_pago="1"))
    assert res[0].ok
    assert s.query(Gasto).filter_by(numero="INM-2").one().forma_pago == "contado"


def test_T4_cambiar_fecha_de_activo_borra_depreciaciones(cliente_web, s):
    cat = CategoriaGasto(nombre="Computadores", cuenta="152805", concepto_exogena="5016")
    s.add(cat); s.flush()
    banco = s.query(Banco).first()
    g = _gasto(s, cat, D("2400000"), date(2026, 1, 15), banco=banco)
    s.commit()
    planeacion.causar_depreciaciones(s, hasta=date(2026, 6, 30)); s.commit()
    gid, cid, cta = g.id, cat.id, banco.cuenta
    assert s.query(Asiento).filter(Asiento.origen.like(f"depre:{gid}:%")).count() > 0
    r = cliente_web.post(f"/gastos/{gid}", data={"tipo_soporte": "FE", "fecha": "2026-03-01", "categoria_id": cid, "subtotal": "2.400.000",
                                                 "iva": "0", "forma_pago": "contado", "cuenta_pago": cta}, follow_redirects=True)
    assert r.status_code == 200 and "No se pudo" not in r.get_data(as_text=True)
    s.expire_all()
    assert s.get(Gasto, gid).fecha == date(2026, 3, 1)
    assert s.query(Asiento).filter(Asiento.origen.like(f"depre:{gid}:%")).count() == 0


def test_T5_numero_duplicado_para_el_mismo_cliente(cliente_web, s):
    c = _cliente(s)
    _factura(s, c, "FV-1", D("1000000"), date(2026, 3, 1))
    s.commit()
    r = cliente_web.post("/ventas/nueva", data={"tipo": "FV", "numero": "FV-1", "fecha": "2026-03-05", "cliente_id": c.id,
                                                "base": "100.000", "iva": "19.000"}, follow_redirects=True)
    assert "Ya existe el documento FV-1" in r.get_data(as_text=True)
    assert s.query(DocumentoVenta).count() == 1
    r = cliente_web.post("/ventas/nueva", data={"tipo": "FV", "numero": "", "fecha": "2026-03-05", "cliente_id": c.id,
                                                "base": "100.000", "iva": "19.000"}, follow_redirects=True)
    assert "Indique el número" in r.get_data(as_text=True)


def test_T6_deshacer_gasto_del_banco_con_nc_se_rechaza(cliente_web, s):
    b = s.query(Banco).first()
    cat = s.query(CategoriaGasto).filter(CategoriaGasto.cuenta.like("51%")).first()
    mov = MovimientoBanco(banco_id=b.id, fecha=date(2026, 3, 10), descripcion="PAGO", valor=D("-100000"), huella="h1")
    s.add(mov); s.commit()
    cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "gasto", "categoria_id": cat.id})
    s.expire_all()
    mov = s.get(MovimientoBanco, mov.id)
    gid = mov.origen_id
    g = s.get(Gasto, gid)
    nc = Gasto(tipo_soporte="NC", numero="NC-1", fecha=date(2026, 3, 12), categoria=cat, subtotal=D("10000"), iva=0,
               iva_descontable=False, total=D("10000"), forma_pago="contado", cuenta_pago=b.cuenta, referencia=g)
    s.add(nc); s.flush(); contab.contabilizar_gasto(s, nc); s.commit()
    r = cliente_web.post(f"/bancos/movimiento/{mov.id}", data={"accion": "pendiente"}, follow_redirects=True)
    assert "notas crédito asociadas" in r.get_data(as_text=True)
    s.expire_all()
    assert s.get(Gasto, gid) is not None and s.get(MovimientoBanco, mov.id).estado == "conciliado"


def test_T7_vigilancia_deja_txt_cuando_un_zip_trae_fallidos(s, tmp_path):
    carpeta = tmp_path / "vig"
    carpeta.mkdir()
    contab.set_config(s, "carpeta_vigilada", str(carpeta)); s.commit()
    bueno = factura_compra("INM-1", "2026-03-01", ARRENDADOR, 100000, 19000).encode()
    malo = re.sub(r"<cbc:IssueDate>[^<]*</cbc:IssueDate>", "", factura_compra("INM-2", "2026-03-02", ARRENDADOR, 100000, 19000)).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("INM-1.xml", bueno); z.writestr("INM-2.xml", malo)
    (carpeta / "lote.zip").write_bytes(buf.getvalue())
    res = vigilancia.revisar(s)
    assert [ok for _, ok, _ in res] == [True, False] or sorted(ok for _, ok, _ in res) == [False, True]
    assert (carpeta / "importados" / "lote.zip").exists()
    txt = (carpeta / "importados" / "lote.zip.txt").read_text(encoding="utf-8")
    assert "OK " in txt and "NO " in txt


def test_T8_mensaje_nc_sin_factura_no_se_repite(s):
    res = importar(s, "nc.xml", factura_compra("NCP-1", "2026-03-05", ARRENDADOR, 100000, 19000, tipo="CreditNote"))
    assert res[0].ok, res[0].mensaje
    assert res[0].mensaje.count("nota crédito sin factura asociada") == 1, res[0].mensaje


def test_T10_reparacion_al_arrancar(s, app):
    from app import create_app, config
    from app.models import Movimiento
    from app.db import Session
    p1, p2 = _dos_pagos(s)
    a2 = s.query(Asiento).filter_by(origen=f"impuesto:{p2.id}").one()
    a2.lineas.append(Movimiento(cuenta=contab.CTA_IVA_GENERADO, debito=D("142500"), credito=0))
    a2.lineas.append(Movimiento(cuenta=contab.CTA_RETEIVA, debito=0, credito=D("142500")))
    contab.set_config(s, "reparacion_reteiva", "")  # base de una versión anterior: sin la marca
    s.commit()
    assert saldo_cuenta(s, contab.CTA_RETEIVA) == D("-142500")
    Session.remove()
    create_app(config.DATOS_DIR)
    s2 = Session
    assert saldo_cuenta(s2, contab.CTA_RETEIVA) == 0
    assert contab.config(s2, "reparacion_reteiva") == "si"


def test_T11_cerrar_dos_veces_y_reabrir(cliente_web, s):
    c = _cliente(s)
    _factura(s, c, "FV-1", D("1000000"), date(2025, 5, 1)); s.commit()
    r = cliente_web.post("/contabilidad/cierre", data={"anio": "2025", "accion": "cerrar"}, follow_redirects=True)
    assert "cerrado" in r.get_data(as_text=True)
    r = cliente_web.post("/contabilidad/cierre", data={"anio": "2025", "accion": "cerrar"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "No se pudo" not in html, html[:3000]
    assert s.query(Asiento).filter_by(origen="cierre:2025").count() == 1
    cuadra(s)


def test_T12_anio_cerrado_tampoco_admite_borrar_ni_editar(cliente_web, s):
    c = _cliente(s)
    f = _factura(s, c, "FV-1", D("1000000"), date(2025, 5, 1)); s.commit()
    planeacion.cerrar_anio(s, 2025); s.commit()
    contab.set_config(s, "periodo_bloqueado_hasta", ""); s.commit()
    fid, cid = f.id, c.id
    r = cliente_web.post(f"/ventas/{fid}", data={"accion": "eliminar"}, follow_redirects=True)
    assert "ya está cerrado" in r.get_data(as_text=True)
    assert s.get(DocumentoVenta, fid) is not None
    r = cliente_web.post(f"/ventas/{fid}", data={"anulada": "on"}, follow_redirects=True)
    s.expire_all()
    assert "ya está cerrado" in r.get_data(as_text=True) and not s.get(DocumentoVenta, fid).anulada
    r = cliente_web.post(f"/ventas/{fid}", data={"reteiva_aplica": "on", "reteiva_valor": "28.500"}, follow_redirects=True)
    s.expire_all()
    assert "ya está cerrado" in r.get_data(as_text=True) and not s.get(DocumentoVenta, fid).reteiva_aplica
    r = cliente_web.post(f"/ventas/{fid}", data={"notas": "solo texto"}, follow_redirects=True)  # sin efecto contable: permitido
    s.expire_all()
    assert "Cambios guardados" in r.get_data(as_text=True) and s.get(DocumentoVenta, fid).notas == "solo texto"
    # y un recaudo nuevo fechado en el año cerrado
    banco = s.query(Banco).first()
    r = cliente_web.post("/recaudos/nuevo", data={"guardar": "1", "fecha": "2025-12-30", "cliente_id": cid, "banco_id": banco.id,
                                                  "valor": "1.190.000", f"aplicar_{fid}": "1.190.000"}, follow_redirects=True)
    assert "ya está cerrado" in r.get_data(as_text=True)
    from app.models import Recaudo
    assert s.query(Recaudo).count() == 0
