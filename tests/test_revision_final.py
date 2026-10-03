"""Pruebas de la última revisión: validaciones amables, secretos, saldos en lote y respaldo atómico."""
from datetime import date
from decimal import Decimal

import pytest

from app import cartera, contab
from app.models import Banco, CategoriaGasto, DocumentoVenta, LineaVenta, Tercero


def _cliente(s, nit="900111222"):
    t = Tercero(nit=nit, nombre="Cliente Prueba", es_cliente=True)
    s.add(t)
    s.flush()
    return t


def _factura(s, cliente, numero, base, fecha=None):
    doc = DocumentoVenta(tipo="FV", numero=numero, fecha=fecha or date(2026, 2, 1), cliente=cliente, subtotal=base,
                         base_gravada=base, iva=base * Decimal("0.19"), total=base * Decimal("1.19"))
    doc.lineas.append(LineaVenta(descripcion="Honorarios", base=base, iva_pct=19, iva=doc.iva))
    s.add(doc)
    s.flush()
    contab.contabilizar_venta(s, doc)
    return doc


def test_numeros_no_finitos_se_rechazan():
    for texto in ("NaN", "Infinity", "-Infinity", "nan"):
        with pytest.raises(ValueError):
            contab.d(texto)
    assert contab.d("1.234.567,89") == Decimal("1234567.89")


def test_saldos_en_lote_coinciden_con_saldo_individual(s, cliente_web):
    c = _cliente(s)
    f1 = _factura(s, c, "FV-1", Decimal("1000000"))
    f2 = _factura(s, c, "FV-2", Decimal("500000"), fecha=date(2026, 3, 1))
    nc = DocumentoVenta(tipo="NC", numero="NC-1", fecha=date(2026, 3, 5), cliente=c, subtotal=100000,
                        base_gravada=100000, iva=19000, total=119000, referencia_id=f1.id)
    s.add(nc)
    s.flush()
    contab.contabilizar_venta(s, nc)
    s.commit()
    banco = s.query(Banco).first()
    r = cliente_web.post(f"/recaudos/nuevo?cliente_id={c.id}",
                         data={"guardar": "1", "fecha": "2026-03-10", "banco_id": banco.id, "valor": "200000",
                               f"aplicar_{f1.id}": "200000", f"aplicar_{f2.id}": "0"})
    assert r.status_code == 302
    docs = s.query(DocumentoVenta).order_by(DocumentoVenta.id).all()
    lote = cartera.saldos_en_lote(s, docs)
    for doc in docs:
        assert lote[doc.id] == cartera.saldo_documento(s, doc)
    assert lote[f1.id] == Decimal("1190000") - 119000 - 200000
    # con fecha de corte anterior al recaudo y a la NC
    lote_corte = cartera.saldos_en_lote(s, docs, al=date(2026, 2, 28))
    assert lote_corte[f1.id] == cartera.saldo_documento(s, f1, al=date(2026, 2, 28)) == Decimal("1190000")


def test_mensajes_amables_en_formularios_incompletos(cliente_web, s):
    r = cliente_web.post("/ventas/nueva", data={"numero": "FV-9", "base": "100", "iva": "19", "fecha": "2026-02-01"},
                         follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Seleccione el cliente." in html and "invalid literal" not in html
    r = cliente_web.post("/ventas/nueva", data={"numero": "FV-9", "base": "NaN", "iva": "0", "fecha": "2026-02-01",
                                               "cliente_id": _cliente(s).id}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "no es un número válido" in html and "InvalidOperation" not in html
    r = cliente_web.post("/recaudos/nuevo", data={"guardar": "1", "valor": "1000", "banco_id": ""}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Seleccione el cliente." in html and "IntegrityError" not in html
    cat = s.query(CategoriaGasto).first()
    r = cliente_web.post("/gastos/nuevo", data={"fecha": "2026-02-01", "categoria_id": "", "subtotal": "100",
                                               "iva": "0", "otros_impuestos": "0", "proveedor_id": ""},
                         follow_redirects=True)
    assert "Seleccione la categoría" in r.get_data(as_text=True)
    assert cat is not None


def test_asiento_manual_sin_valores(cliente_web):
    r = cliente_web.post("/contabilidad/asiento/nuevo",
                         data={"fecha": "2026-02-01", "descripcion": "x", "cuenta": ["11200501", "310505"],
                               "debito": ["0", ""], "credito": ["", "0"]}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "al menos un valor" in html and "Asiento guardado" not in html and "NoneType" not in html


def test_secretos_no_vuelven_al_navegador_y_se_conservan(cliente_web, s, monkeypatch):
    from app import correo
    monkeypatch.setattr(correo, "probar", lambda session: "Conexión correcta (simulada).")
    r = cliente_web.post("/configuracion/", data={"accion": ["empresa", "probar_correo"], "correo_servidor": "gmail",
                                                 "correo_usuario": "facturas@gmail.com", "correo_clave": "ghp_SECRETO"},
                         follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "ghp_SECRETO" not in html
    assert contab.config(s, "correo_usuario") == "facturas@gmail.com"  # el botón de prueba también guardó
    assert contab.config(s, "correo_clave") == "ghp_SECRETO"
    assert "deje vacío para conservarla" in html
    cliente_web.post("/configuracion/", data={"accion": "empresa", "correo_usuario": "otro@gmail.com", "correo_clave": ""})
    assert contab.config(s, "correo_clave") == "ghp_SECRETO"
    assert contab.config(s, "correo_usuario") == "otro@gmail.com"


def test_xml_adjunto_se_descarga_y_no_se_muestra(cliente_web, app):
    from app import config
    carpeta = config.ADJUNTOS_DIR / "gastos"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "malo.xml").write_text('<html xmlns="http://www.w3.org/1999/xhtml"><script>alert(1)</script></html>',
                                       encoding="utf-8")
    r = cliente_web.get("/archivo/gastos/malo.xml")
    assert r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "")
    (carpeta / "soporte.pdf").write_bytes(b"%PDF-1.4")
    assert "attachment" not in cliente_web.get("/archivo/gastos/soporte.pdf").headers.get("Content-Disposition", "")


def test_respaldo_sin_temporales(s, app):
    from app import respaldo
    ruta = respaldo.crear_respaldo(s)
    assert ruta.exists() and ruta.suffix == ".zip"
    assert not list(ruta.parent.glob("*.tmp"))
    # el respaldo automático no ensucia el historial
    from app.models import Bitacora
    assert not s.query(Bitacora).filter(Bitacora.descripcion == "ultimo_respaldo").count()


def test_reiniciar_devuelve_pagina_de_espera(cliente_web, monkeypatch):
    import threading
    lanzados = []
    monkeypatch.setattr(threading.Timer, "start", lambda self: lanzados.append(self))
    r = cliente_web.post("/configuracion/", data={"accion": "reiniciar"})
    assert r.status_code == 200 and "Reiniciando" in r.get_data(as_text=True) and lanzados


def test_favicon_y_menu_movil(cliente_web):
    assert cliente_web.get("/favicon.ico").status_code == 302
    assert cliente_web.get("/static/icono.svg").status_code == 200
    html = cliente_web.get("/").get_data(as_text=True)
    assert 'class="menu-boton"' in html and 'rel="icon"' in html
