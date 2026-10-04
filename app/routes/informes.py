"""Centro de informes: estados financieros adicionales, análisis, proveedores, rentabilidad, presupuesto,
comparación de regímenes e informe de conciliación."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import contab, exportar, informes
from ..db import Session
from ..models import CERO, Asunto, Banco, Gasto, Tercero
from . import XLSX, anio_arg, descargar, dinero, fecha_arg, periodo

bp = Blueprint("informes", __name__, url_prefix="/informes")

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

CATALOGO = [
    ("Estados financieros", [
        ("/contabilidad/resultados", "Estado de resultados", "Ingresos, gastos y utilidad del periodo, con detalle por cuenta."),
        ("/contabilidad/balance", "Estado de situación financiera (balance general)", "Activos, pasivos y patrimonio a una fecha."),
        ("/informes/flujo-efectivo", "Estado de flujo de efectivo", "De dónde entró y a dónde salió el dinero: operación, inversión y financiación."),
        ("/informes/patrimonio", "Estado de cambios en el patrimonio", "Capital, resultados acumulados y resultado del ejercicio."),
        ("/contabilidad/balance-prueba", "Balance de prueba", "Saldos iniciales, movimientos y saldos finales de todas las cuentas."),
    ]),
    ("Análisis", [
        ("/informes/comparativo", "Estado de resultados comparativo", "Frente al año anterior o al periodo anterior: variación y análisis vertical."),
        ("/informes/comparativo-balance", "Balance comparativo", "Activos, pasivos y patrimonio frente a otra fecha."),
        ("/informes/resultados-mensual", "Estado de resultados mes a mes", "Doce columnas, una por mes, y el total del año."),
        ("/informes/indicadores", "Indicadores financieros", "Márgenes, liquidez, días de cobro, carga tributaria."),
        ("/informes/presupuesto", "Presupuesto vs. real", "Lo que planeó gastar e ingresar frente a lo ejecutado."),
        ("/informes/rentabilidad", "Rentabilidad por cliente y asunto", "Ingresos menos gastos imputados a cada cliente o caso."),
        ("/informes/regimenes", "SIMPLE vs. régimen ordinario", "Estimación de cuánto pagaría en cada régimen con las cifras del año."),
    ]),
    ("Clientes e ingresos", [
        ("/cartera", "Cartera por edades", "Saldos por cobrar según días de vencimiento, con estado de cuenta por cliente."),
        ("/contabilidad/ventas-clientes", "Ventas por cliente y mes", "Ingresos de cada cliente mes a mes y frente al año anterior."),
        ("/recaudos", "Recaudos", "Pagos recibidos y facturas a las que se aplicaron."),
        ("/bancos/ingresos", "Ingresos sin factura", "Intereses, reintegros y otros ingresos."),
        ("/certificados", "Certificados de reteIVA", "Retenciones que le practicaron y certificados pendientes."),
        ("/informes/reembolsables", "Gastos reembolsables pendientes", "Gastos hechos por cuenta de clientes que aún no se les han cobrado."),
    ]),
    ("Proveedores y gastos", [
        ("/informes/cuentas-por-pagar", "Cuentas por pagar por edades", "Facturas de proveedores pendientes según vencimiento."),
        ("/informes/gastos-proveedor", "Gastos por proveedor", "A quién le compra más, con participación sobre el total."),
        ("/informes/gastos-categoria", "Gastos por categoría mes a mes", "Cada categoría de gasto en doce columnas."),
        ("/gastos", "Listado de gastos", "Todos los gastos del año con filtros y Excel."),
        ("/contabilidad/depreciaciones", "Activos fijos y depreciación", "Equipos y muebles con su depreciación acumulada."),
    ]),
    ("Impuestos", [
        ("/impuestos/iva", "IVA por bimestre", "IVA generado, descontable y reteIVA de cada bimestre."),
        ("/impuestos/simple", "Recibo 2593 (SIMPLE + IVA)", "Anticipo bimestral del SIMPLE y IVA a pagar."),
        ("/impuestos/f260", "Declaración anual SIMPLE (F260)", "Impuesto del año, anticipos y saldo."),
        ("/impuestos/f300", "Declaración anual de IVA (F300)", "Consolidado del año."),
        ("/impuestos/exogena", "Información exógena", "Formatos 1001, 1005, 1006, 1007, 1008 y 1009 en Excel."),
        ("/flujo-caja", "Flujo de caja proyectado", "Cobros, pagos e impuestos de los próximos meses."),
    ]),
    ("Bancos y libros", [
        ("/bancos/", "Bancos y saldos", "Saldo de cada cuenta y movimientos."),
        ("/informes/conciliacion", "Informe de conciliación bancaria", "Saldo según libros, partidas pendientes y saldo que debe mostrar el extracto."),
        ("/contabilidad/diario", "Libro diario", "Todos los asientos del periodo."),
        ("/contabilidad/mayor", "Libro mayor y auxiliares", "Movimientos de una cuenta con saldo acumulado."),
        ("/historial", "Historial de cambios", "Quién cambió qué y cuándo."),
        ("/contabilidad/paquete", "Paquete para el contador", "Un solo Excel con todos los libros y listados del periodo."),
    ]),
]


@bp.route("/")
def centro():
    return render_template("informes/centro.html", catalogo=CATALOGO)


def _pdf(s, titulo, subtitulo, columnas, filas, nombre, orientacion="P", notas=None):
    pdf = exportar.informe_pdf(exportar._empresa_dict(s), titulo, subtitulo, columnas, filas, orientacion, notas)
    return descargar(pdf, nombre, "application/pdf")


# ------------------------------------------------------------------ estados financieros adicionales

@bp.route("/flujo-efectivo")
def flujo_efectivo():
    s = Session()
    desde, hasta = periodo()
    fe = informes.flujo_de_efectivo(s, desde, hasta)
    if request.args.get("pdf"):
        filas = [["Saldo inicial de caja y bancos", fe["saldo_inicial"]]]
        for sec in fe["secciones"]:
            filas.append({"celdas": [sec["nombre"], sec["total"]], "estilo": "grupo"})
            filas += [[f"   {n}", v] for n, v in sec["filas"]]
        filas.append({"celdas": ["Variación neta del efectivo", fe["neto"]], "estilo": "total"})
        filas.append({"celdas": ["Saldo final de caja y bancos", fe["saldo_final"]], "estilo": "total"})
        return _pdf(s, "Estado de flujo de efectivo", f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y} (método directo)",
                    [("Concepto", 140, "L"), ("Valor", 45, "R")], filas, f"flujo_efectivo_{hasta}.pdf")
    return render_template("informes/flujo_efectivo.html", fe=fe, desde=desde, hasta=hasta)


@bp.route("/patrimonio")
def patrimonio():
    s = Session()
    anio = anio_arg()
    filas, total = informes.cambios_patrimonio(s, anio)
    if request.args.get("pdf"):
        datos = [[f["cuenta"], f["inicial"], f["aumentos"], f["disminuciones"], f["final"]] for f in filas]
        datos.append({"celdas": ["Total patrimonio", total["inicial"], total["aumentos"], total["disminuciones"], total["final"]],
                      "estilo": "total"})
        return _pdf(s, "Estado de cambios en el patrimonio", f"Año {anio}",
                    [("Cuenta", 70, "L"), ("Saldo inicial", 30, "R"), ("Aumentos", 28, "R"), ("Disminuciones", 28, "R"),
                     ("Saldo final", 30, "R")], datos, f"cambios_patrimonio_{anio}.pdf")
    return render_template("informes/patrimonio.html", filas=filas, total=total, anio=anio)


@bp.route("/comparativo")
def comparativo():
    s = Session()
    desde, hasta = periodo()
    modo = request.args.get("modo", "anio")
    c = informes.comparativo_resultados(s, desde, hasta, modo)
    if request.args.get("pdf"):
        return _pdf(s, "Estado de resultados comparativo",
                    f"{desde:%d/%m/%Y} a {hasta:%d/%m/%Y} frente a {c['desde2']:%d/%m/%Y} a {c['hasta2']:%d/%m/%Y}",
                    [("Concepto", 86, "L"), ("Actual", 30, "R"), ("Anterior", 30, "R"), ("Variación", 30, "R"), ("%", 14, "R"),
                     ("% ingr.", 14, "R")],
                    [{"celdas": [f["concepto"], f["actual"], f["anterior"], f["variacion"], _fmt_pct(f["variacion_pct"]),
                                 _fmt_pct(f["vertical_actual"])], "estilo": f["estilo"]} for f in c["filas"]],
                    f"comparativo_{hasta}.pdf", "L")
    if request.args.get("xlsx"):
        enc = ["Concepto", "Actual", "Anterior", "Variación", "Variación %", "% sobre ingresos actual", "% sobre ingresos anterior"]
        datos = [[f["concepto"].strip(), f["actual"], f["anterior"], f["variacion"], f["variacion_pct"], f["vertical_actual"],
                  f["vertical_anterior"]] for f in c["filas"]]
        return descargar(exportar.excel({"Comparativo": (enc, datos)}), f"comparativo_{hasta}.xlsx", XLSX)
    return render_template("informes/comparativo.html", c=c, desde=desde, hasta=hasta, modo=modo)


@bp.route("/comparativo-balance")
def comparativo_balance():
    s = Session()
    corte = fecha_arg("corte", date.today())
    modo = "anio"  # misma fecha del año anterior
    c = informes.comparativo_balance(s, corte, modo)
    if request.args.get("pdf"):
        return _pdf(s, "Estado de situación financiera comparativo", f"Al {corte:%d/%m/%Y} frente al {c['corte2']:%d/%m/%Y}",
                    [("Concepto", 86, "L"), ("Actual", 30, "R"), ("Anterior", 30, "R"), ("Variación", 30, "R"), ("%", 14, "R"),
                     ("% activo", 14, "R")],
                    [{"celdas": [f["concepto"], f["actual"], f["anterior"], f["variacion"], _fmt_pct(f["variacion_pct"]),
                                 _fmt_pct(f["vertical_actual"])], "estilo": f["estilo"]} for f in c["filas"]],
                    f"balance_comparativo_{corte}.pdf", "L")
    return render_template("informes/comparativo_balance.html", c=c, corte=corte, modo=modo)


def _fmt_pct(v):
    return "" if v is None else f"{v:.1f} %".replace(".", ",")


@bp.route("/resultados-mensual")
def resultados_mensual():
    s = Session()
    anio = anio_arg()
    filas = informes.resultados_mensuales(s, anio)
    if request.args.get("xlsx"):
        enc = ["Concepto"] + [f"{m} {anio}" for m in MESES] + ["Total"]
        datos = [[f["concepto"].strip()] + f["meses"] + [f["total"]] for f in filas]
        return descargar(exportar.excel({"Resultados mensual": (enc, datos)}), f"resultados_mensual_{anio}.xlsx", XLSX)
    return render_template("informes/resultados_mensual.html", filas=filas, anio=anio, meses=MESES)


@bp.route("/indicadores")
def indicadores():
    s = Session()
    anio = anio_arg()
    corte = fecha_arg("corte", date.today())
    datos = informes.indicadores(s, anio, corte)
    grupos = []
    for d in datos:
        if not grupos or grupos[-1][0] != d["grupo"]:
            grupos.append((d["grupo"], []))
        grupos[-1][1].append(d)
    return render_template("informes/indicadores.html", grupos=grupos, anio=anio, corte=corte)


# ------------------------------------------------------------------ proveedores y gastos

@bp.route("/cuentas-por-pagar")
def cuentas_por_pagar():
    s = Session()
    corte = fecha_arg("corte", date.today())
    filas, totales = informes.cuentas_por_pagar_edades(s, corte)
    edades = informes.EDADES_CXP
    if request.args.get("xlsx"):
        enc = ["Proveedor", "NIT"] + [e[0] for e in edades] + ["Total"]
        datos = [[f["proveedor"].nombre if f["proveedor"] else "Sin proveedor", f["proveedor"].nit if f["proveedor"] else ""]
                 + [f[e[0]] for e in edades] + [f["total"]] for f in filas]
        return descargar(exportar.excel({"Cuentas por pagar": (enc, datos)}), f"cuentas_por_pagar_{corte}.xlsx", XLSX)
    if request.args.get("pdf"):
        datos = [[f["proveedor"].nombre if f["proveedor"] else "Sin proveedor"] + [f[e[0]] for e in edades] + [f["total"]]
                 for f in filas]
        datos.append({"celdas": ["Total"] + [totales[e[0]] for e in edades] + [totales["total"]], "estilo": "total"})
        return _pdf(s, "Cuentas por pagar por edades", f"Al {corte:%d/%m/%Y}",
                    [("Proveedor", 70, "L")] + [(e[0], 26, "R") for e in edades] + [("Total", 30, "R")], datos,
                    f"cuentas_por_pagar_{corte}.pdf", "L")
    return render_template("informes/cuentas_por_pagar.html", filas=filas, totales=totales, edades=edades, corte=corte)


@bp.route("/proveedor/<int:proveedor_id>")
def estado_cuenta_proveedor(proveedor_id):
    s = Session()
    prov = s.get(Tercero, proveedor_id) or abort(404)
    corte = fecha_arg("corte", date.today())
    filas = informes.facturas_por_pagar(s, corte, proveedor_id)
    total = sum((v for _, v in filas), CERO)
    historico = (s.query(Gasto).filter(Gasto.proveedor_id == proveedor_id, Gasto.fecha <= corte)
                 .order_by(Gasto.fecha.desc()).limit(50).all())
    if request.args.get("pdf"):
        datos = [[g.numero or "", g.fecha, g.vencimiento or "", g.total, g.total - saldo, saldo] for g, saldo in filas]
        datos.append({"celdas": ["Total por pagar", "", "", "", "", total], "estilo": "total"})
        return _pdf(s, f"Estado de cuenta: {prov.nombre}", f"NIT {prov.nit_completo} · al {corte:%d/%m/%Y}",
                    [("Factura", 35, "L"), ("Fecha", 25, "C"), ("Vence", 25, "C"), ("Valor", 35, "R"), ("Pagado/NC", 35, "R"),
                     ("Saldo", 35, "R")], datos, f"estado_cuenta_proveedor_{proveedor_id}_{corte}.pdf")
    return render_template("informes/estado_cuenta_proveedor.html", prov=prov, filas=filas, total=total, corte=corte,
                           historico=historico)


@bp.route("/gastos-proveedor")
def gastos_proveedor():
    s = Session()
    desde, hasta = periodo()
    filas, total = informes.gastos_por_proveedor(s, desde, hasta)
    if request.args.get("xlsx"):
        enc = ["Proveedor", "NIT", "Documentos", "Gasto (sin IVA descontable)", "IVA descontable", "% del total"]
        datos = [[f["proveedor"].nombre if f["proveedor"] else "Sin proveedor", f["proveedor"].nit if f["proveedor"] else "",
                  f["n"], f["total"], f["iva"], f["pct"]] for f in filas]
        return descargar(exportar.excel({"Gastos por proveedor": (enc, datos)}), f"gastos_proveedor_{hasta}.xlsx", XLSX)
    return render_template("informes/gastos_proveedor.html", filas=filas, total=total, desde=desde, hasta=hasta)


@bp.route("/gastos-categoria")
def gastos_categoria():
    s = Session()
    anio = anio_arg()
    filas, totales, total = informes.gastos_por_categoria_mensual(s, anio)
    if request.args.get("xlsx"):
        enc = ["Categoría"] + [f"{m} {anio}" for m in MESES] + ["Total"]
        datos = [[n] + meses + [t] for n, meses, t in filas] + [["Total"] + totales + [total]]
        return descargar(exportar.excel({"Gastos por categoría": (enc, datos)}), f"gastos_categoria_{anio}.xlsx", XLSX)
    return render_template("informes/gastos_categoria.html", filas=filas, totales=totales, total=total, anio=anio,
                           meses=MESES)


# ------------------------------------------------------------------ rentabilidad y asuntos

@bp.route("/rentabilidad")
def rentabilidad():
    s = Session()
    desde, hasta = periodo()
    filas, totales = informes.rentabilidad(s, desde, hasta)
    if request.args.get("xlsx"):
        enc = ["Cliente", "Asunto", "Ingresos (sin IVA)", "Gastos imputados", "Margen", "Margen %"]
        datos = []
        for f in filas:
            datos.append([f["cliente"].nombre if f["cliente"] else "", "(total cliente)", f["ingresos"], f["gastos"],
                          f["margen"], f["margen_pct"]])
            for a in f["asuntos"]:
                datos.append(["", a["asunto"].nombre, a["ingresos"], a["gastos"], a["margen"], a["margen_pct"]])
        return descargar(exportar.excel({"Rentabilidad": (enc, datos)}), f"rentabilidad_{hasta}.xlsx", XLSX)
    return render_template("informes/rentabilidad.html", filas=filas, totales=totales, desde=desde, hasta=hasta)


@bp.route("/reembolsables", methods=["GET", "POST"])
def reembolsables():
    s = Session()
    if request.method == "POST":
        ids = request.form.getlist("id")
        n = 0
        for gid in ids:
            g = s.get(Gasto, int(gid)) if gid.isdigit() else None
            if g is not None and g.reembolsable and not g.reembolsado:
                g.reembolsado = True
                n += 1
        s.commit()
        flash(f"{n} gasto(s) marcados como ya cobrados al cliente.", "ok")
        return redirect(url_for("informes.reembolsables"))
    gastos = informes.gastos_reembolsables_pendientes(s)
    por_cliente = {}
    for g in gastos:
        por_cliente.setdefault(g.asunto.cliente, []).append(g)
    return render_template("informes/reembolsables.html", por_cliente=por_cliente,
                           total=sum((g.total for g in gastos), CERO))


@bp.route("/asuntos", methods=["GET", "POST"])
def asuntos():
    s = Session()
    if request.method == "POST":
        try:
            aid = request.form.get("id", type=int)
            existente = s.get(Asunto, aid) if aid else None
            if aid and existente is None:
                raise ValueError("El asunto ya no existe.")
            a = existente or Asunto()
            if request.form.get("accion") == "borrar":
                from ..models import Cotizacion
                if existente and (s.query(Gasto).filter_by(asunto_id=aid).count()
                                  or s.query(informes.DocumentoVenta).filter_by(asunto_id=aid).count()
                                  or s.query(Cotizacion).filter_by(asunto_id=aid).count()):
                    raise ValueError("El asunto tiene facturas, gastos o cotizaciones asociados; ciérrelo en vez de eliminarlo.")
                if existente:
                    s.delete(a)
                    s.commit()
                    flash("Asunto eliminado.", "ok")
                return redirect(url_for("informes.asuntos"))
            a.cliente_id = request.form.get("cliente_id", type=int)
            cliente = s.get(Tercero, a.cliente_id) if a.cliente_id else None
            if cliente is None or not cliente.es_cliente:
                raise ValueError("Seleccione el cliente.")
            a.nombre = request.form.get("nombre", "").strip()
            if not a.nombre:
                raise ValueError("Indique el nombre del asunto.")
            a.referencia = request.form.get("referencia", "").strip() or None
            a.estado = "cerrado" if request.form.get("estado") == "cerrado" else "abierto"
            a.honorarios_pactados = dinero("honorarios_pactados") or None
            a.notas = request.form.get("notas", "").strip() or None
            s.add(a)
            s.commit()
            flash("Asunto guardado.", "ok")
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {e}", "error")
        return redirect(url_for("informes.asuntos"))
    estado = request.args.get("estado", "abierto")
    q = s.query(Asunto)
    if estado != "todos":
        q = q.filter(Asunto.estado == estado)
    lista = q.order_by(Asunto.estado, Asunto.creado.desc()).all()
    editar = s.get(Asunto, request.args.get("editar", type=int) or 0)
    clientes = s.query(Tercero).filter(Tercero.es_cliente.is_(True)).order_by(Tercero.nombre).all()
    resumen = {}
    if lista:
        filas, _ = informes.rentabilidad(s, date(2000, 1, 1), date(2100, 12, 31))
        for f in filas:
            for a in f["asuntos"]:
                resumen[a["asunto"].id] = a
    return render_template("informes/asuntos.html", asuntos=lista, editar=editar, clientes=clientes, estado=estado,
                           resumen=resumen)


# ------------------------------------------------------------------ presupuesto

@bp.route("/presupuesto", methods=["GET", "POST"])
def presupuesto():
    s = Session()
    anio = anio_arg()
    if request.method == "POST":
        try:
            anio_txt = request.form.get("anio", "").strip()
            if not anio_txt.isdigit() or not 2000 <= int(anio_txt) <= 2100:
                raise ValueError("El año no es válido.")
            anio = int(anio_txt)
            valores = {"ingresos": dinero("ingresos")}
            from ..models import CategoriaGasto
            validas = {f"categoria:{c.id}" for c in s.query(CategoriaGasto)}
            for k in request.form:
                if k.startswith("categoria:"):
                    if k not in validas:
                        raise ValueError("Una de las categorías del formulario ya no existe.")
                    valores[k] = dinero(k)
            informes.guardar_presupuesto(s, anio, valores)
            s.commit()
            flash(f"Presupuesto {anio} guardado.", "ok")
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {e}", "error")
        return redirect(url_for("informes.presupuesto", anio=anio))
    corte = fecha_arg("corte", date.today())
    pr = informes.presupuesto_vs_real(s, anio, corte)
    pres = informes.presupuesto_del_anio(s, anio)
    if request.args.get("xlsx"):
        enc = ["Concepto", "Presupuesto anual", f"Esperado a {pr['meses']} meses", "Real", "Diferencia", "Ejecución %",
               "Ejecución anual %"]
        datos = [[f["concepto"], f["anual"], f["esperado"], f["real"], f["diferencia"], f["ejecucion"], f["ejecucion_anual"]]
                 for f in pr["filas"]]
        return descargar(exportar.excel({"Presupuesto": (enc, datos)}), f"presupuesto_{anio}.xlsx", XLSX)
    return render_template("informes/presupuesto.html", pr=pr, pres=pres, anio=anio, corte=corte,
                           editar=request.args.get("editar"))


# ------------------------------------------------------------------ regímenes

@bp.route("/regimenes", methods=["GET", "POST"])
def regimenes():
    s = Session()
    anio = anio_arg()
    if request.method == "POST":
        try:
            ica = dinero("ica_por_mil")
            if ica < 0 or ica > 100:
                raise ValueError("La tarifa de ICA debe estar entre 0 y 100 por mil.")
            contab.set_config(s, "ica_por_mil", str(ica))
            s.commit()
            flash("Tarifa de ICA guardada.", "ok")
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {e}", "error")
        return redirect(url_for("informes.regimenes", anio=anio))
    corte = fecha_arg("corte", date.today())
    c = informes.comparar_regimenes(s, anio, corte)
    return render_template("informes/regimenes.html", c=c, anio=anio, corte=corte)


# ------------------------------------------------------------------ conciliación

@bp.route("/conciliacion")
def conciliacion():
    s = Session()
    bid = request.args.get("banco", type=int)
    banco = s.get(Banco, bid) if bid else s.query(Banco).filter_by(activo=True).order_by(Banco.id).first()
    if banco is None:
        abort(404)
    corte = fecha_arg("corte", date.today())
    saldo_txt = request.args.get("saldo_extracto", "").strip()
    saldo = None
    if saldo_txt:
        try:
            saldo = contab.d(saldo_txt)
        except Exception:  # noqa: BLE001
            flash("El saldo del extracto no es un número válido (use 1.234.567,89).", "error")
            saldo_txt = ""
    inf = informes.informe_conciliacion(s, banco, corte, saldo)
    if request.args.get("pdf"):
        filas = [{"celdas": ["Saldo según libros (contabilidad)", inf["saldo_libros"]], "estilo": "total"}]
        filas.append({"celdas": ["(−) Registrado en libros y no en el extracto", inf["total_en_libros"]], "estilo": "grupo"})
        filas += [[f"   {a.fecha:%d/%m} {a.tipo}-{a.numero} {a.descripcion or ''}", v] for a, v in inf["en_libros"]]
        filas.append({"celdas": ["(+) En el extracto y pendiente de registrar", inf["total_pendientes"]], "estilo": "grupo"})
        filas += [[f"   {m.fecha:%d/%m} {m.descripcion}", m.valor] for m in inf["pendientes"]]
        filas.append({"celdas": ["Saldo que debe mostrar el extracto", inf["extracto_esperado"]], "estilo": "total"})
        if saldo is not None:
            filas.append(["Saldo según extracto", saldo])
            filas.append({"celdas": ["Diferencia sin explicar", inf["diferencia"]], "estilo": "total"})
        return _pdf(s, f"Conciliación bancaria: {banco.nombre}", f"Al {corte:%d/%m/%Y} · cuenta {banco.cuenta}",
                    [("Concepto", 140, "L"), ("Valor", 45, "R")], filas, f"conciliacion_{banco.id}_{corte}.pdf")
    return render_template("informes/conciliacion.html", inf=inf, banco=banco, corte=corte, saldo_txt=saldo_txt,
                           bancos=s.query(Banco).filter_by(activo=True).order_by(Banco.id).all())
