"""Libros, estados financieros, asientos manuales y PUC."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import contab, exportar, reportes
from ..db import Session
from ..models import CERO, Asiento, Cuenta, Tercero
from . import XLSX, descargar, fecha_arg, paginar_lista, periodo

bp = Blueprint("contabilidad", __name__, url_prefix="/contabilidad")


def _cuentas_movimiento(s):
    return s.query(Cuenta).filter_by(movimiento=True, activa=True).order_by(Cuenta.codigo).all()


@bp.route("/diario")
def diario():
    s = Session()
    desde, hasta = periodo()
    tipo = request.args.get("tipo") or None
    asientos = reportes.libro_diario(s, desde, hasta, tipo)
    if request.args.get("xlsx"):
        filas = [[a.fecha, f"{a.tipo}-{a.numero}", a.descripcion, m.cuenta, m.cuenta_rel.nombre,
                  m.tercero.nit if m.tercero else "", m.tercero.nombre if m.tercero else "", m.debito, m.credito]
                 for a in asientos for m in a.lineas]
        enc = ["Fecha", "Comprobante", "Descripción", "Cuenta", "Nombre cuenta", "NIT", "Tercero", "Débito", "Crédito"]
        return descargar(exportar.excel({"Libro diario": (enc, filas)}), f"libro_diario_{desde}_{hasta}.xlsx", XLSX)
    asientos, pagina, paginas = paginar_lista(asientos)
    return render_template("contabilidad/diario.html", asientos=asientos, desde=desde, hasta=hasta, tipo=tipo,
                           pagina=pagina, paginas=paginas,
                           tipos=contab.TIPOS_ASIENTO)


@bp.route("/mayor")
def mayor():
    s = Session()
    desde, hasta = periodo()
    cuenta = request.args.get("cuenta") or "11"
    tercero_id = request.args.get("tercero", type=int)
    c, inicial, filas, final = reportes.libro_mayor(s, cuenta, desde, hasta, tercero_id)
    if request.args.get("xlsx"):
        datos = [[m.asiento.fecha, f"{m.asiento.tipo}-{m.asiento.numero}", m.cuenta, m.asiento.descripcion,
                  m.tercero.nombre if m.tercero else "", m.debito, m.credito, saldo] for m, saldo in filas]
        enc = ["Fecha", "Comprobante", "Cuenta", "Descripción", "Tercero", "Débito", "Crédito", "Saldo"]
        return descargar(exportar.excel({"Mayor": (enc, datos)}), f"mayor_{cuenta}.xlsx", XLSX)
    return render_template("contabilidad/mayor.html", cuenta=c, codigo=cuenta, inicial=inicial, filas=filas,
                           final=final, desde=desde, hasta=hasta, cuentas=s.query(Cuenta).order_by(Cuenta.codigo).all(),
                           terceros=s.query(Tercero).order_by(Tercero.nombre).all(), tercero_id=tercero_id)


@bp.route("/balance-prueba")
def balance_prueba():
    s = Session()
    desde, hasta = periodo()
    nivel = request.args.get("nivel", type=int) or 8
    filas, tot_d, tot_c = reportes.balance_de_prueba(s, desde, hasta, nivel)
    if request.args.get("xlsx"):
        datos = [[f.cuenta.codigo, f.cuenta.nombre, f.saldo_inicial, f.debitos, f.creditos, f.saldo_final]
                 for f in filas]
        enc = ["Cuenta", "Nombre", "Saldo inicial", "Débitos", "Créditos", "Saldo final"]
        return descargar(exportar.excel({"Balance de prueba": (enc, datos)}), f"balance_prueba_{hasta}.xlsx", XLSX)
    if request.args.get("pdf"):
        cols = [("Cuenta", 22, "L"), ("Nombre", 70, "L"), ("Saldo inicial", 30, "R"), ("Débitos", 30, "R"),
                ("Créditos", 30, "R"), ("Saldo final", 30, "R")]
        datos = [{"celdas": [f.cuenta.codigo, f.cuenta.nombre, f.saldo_inicial, f.debitos, f.creditos, f.saldo_final],
                  "estilo": "grupo" if f.nivel <= 2 else ""} for f in filas]
        datos.append({"celdas": ["", "Sumas iguales", "", tot_d, tot_c, ""], "estilo": "total"})
        pdf = exportar.informe_pdf(exportar._empresa_dict(s), "Balance de prueba",
                                   f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}", cols, datos)
        return descargar(pdf, f"balance_prueba_{hasta}.pdf", "application/pdf")
    return render_template("contabilidad/balance_prueba.html", filas=filas, tot_d=tot_d, tot_c=tot_c, desde=desde,
                           hasta=hasta, nivel=nivel)


@bp.route("/resultados")
def resultados():
    s = Session()
    desde, hasta = periodo()
    er = reportes.estado_resultados(s, desde, hasta)
    if request.args.get("pdf"):
        filas = _filas_resultados(er)
        pdf = exportar.informe_pdf(exportar._empresa_dict(s), "Estado de resultados",
                                   f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}", [("Concepto", 140, "L"), ("Valor", 45, "R")],
                                   filas)
        return descargar(pdf, f"estado_resultados_{hasta}.pdf", "application/pdf")
    return render_template("contabilidad/resultados.html", er=er, desde=desde, hasta=hasta)


def _filas_resultados(er):
    filas = [{"celdas": ["Ingresos operacionales", er["ingresos_op"]], "estilo": "grupo"}]
    filas += [[f"   {c.codigo} {c.nombre}", v] for c, v in er["det_ingresos_op"]]
    filas.append({"celdas": ["(−) Gastos operacionales de administración", er["gastos_admin"]], "estilo": "grupo"})
    filas += [[f"   {c.codigo} {c.nombre}", v] for c, v in er["det_gastos_admin"]]
    if er["gastos_ventas"]:
        filas.append({"celdas": ["(−) Gastos operacionales de ventas", er["gastos_ventas"]], "estilo": "grupo"})
        filas += [[f"   {c.codigo} {c.nombre}", v] for c, v in er["det_gastos_ventas"]]
    filas.append({"celdas": ["Utilidad operacional", er["utilidad_operacional"]], "estilo": "total"})
    if er["ingresos_no_op"]:
        filas.append({"celdas": ["(+) Ingresos no operacionales", er["ingresos_no_op"]], "estilo": "grupo"})
    if er["gastos_no_op"]:
        filas.append({"celdas": ["(−) Gastos no operacionales", er["gastos_no_op"]], "estilo": "grupo"})
    filas.append({"celdas": ["Utilidad antes de impuestos", er["antes_impuestos"]], "estilo": "total"})
    filas.append(["(−) Impuesto unificado SIMPLE", er["impuesto"]])
    filas.append({"celdas": ["Utilidad neta", er["utilidad_neta"]], "estilo": "total"})
    return filas


def _filas_balance(bg):
    filas = [{"celdas": ["ACTIVO", bg["activo"]], "estilo": "grupo"}]
    filas += [[f"   {c.codigo} {c.nombre}", v] for c, v in bg["det_activo"]]
    filas.append({"celdas": ["PASIVO", bg["pasivo"]], "estilo": "grupo"})
    filas += [[f"   {c.codigo} {c.nombre}", v] for c, v in bg["det_pasivo"]]
    filas.append({"celdas": ["PATRIMONIO", bg["total_patrimonio"]], "estilo": "grupo"})
    filas += [[f"   {c.codigo} {c.nombre}", v] for c, v in bg["det_patrimonio"]]
    if bg["anteriores"]:
        filas.append(["   Resultados de ejercicios anteriores", bg["anteriores"]])
    filas.append(["   Resultado del ejercicio", bg["resultado"]])
    filas.append({"celdas": ["Total pasivo y patrimonio", bg["pasivo"] + bg["total_patrimonio"]], "estilo": "total"})
    return filas


@bp.route("/balance")
def balance():
    s = Session()
    corte = fecha_arg("corte", date.today())
    bg = reportes.balance_general(s, corte)
    if request.args.get("pdf"):
        pdf = exportar.informe_pdf(exportar._empresa_dict(s), "Estado de situación financiera",
                                   f"Al {corte:%d/%m/%Y}", [("Concepto", 140, "L"), ("Valor", 45, "R")],
                                   _filas_balance(bg))
        return descargar(pdf, f"balance_general_{corte}.pdf", "application/pdf")
    return render_template("contabilidad/balance.html", bg=bg, corte=corte)


@bp.route("/paquete")
def paquete():
    """Un solo Excel con todos los libros del periodo, para el contador o revisor."""
    s = Session()
    desde, hasta = periodo()
    from sqlalchemy.orm import selectinload
    from ..models import AplicacionRecaudo, DocumentoVenta, Gasto, OtroIngreso, Recaudo
    from .. import cartera, impuestos
    asientos = reportes.libro_diario(s, desde, hasta)
    bp_filas, td, tc = reportes.balance_de_prueba(s, desde, hasta)
    er = reportes.estado_resultados(s, desde, hasta)
    bg = reportes.balance_general(s, hasta)
    ventas = (s.query(DocumentoVenta).options(selectinload(DocumentoVenta.cliente))
              .filter(DocumentoVenta.fecha.between(desde, hasta)).order_by(DocumentoVenta.fecha).all())
    gastos = (s.query(Gasto).options(selectinload(Gasto.proveedor), selectinload(Gasto.categoria))
              .filter(Gasto.fecha.between(desde, hasta)).order_by(Gasto.fecha).all())
    recaudos = (s.query(Recaudo).options(selectinload(Recaudo.cliente), selectinload(Recaudo.banco),
                                         selectinload(Recaudo.aplicaciones).selectinload(AplicacionRecaudo.documento))
                .filter(Recaudo.fecha.between(desde, hasta)).order_by(Recaudo.fecha).all())
    otros = (s.query(OtroIngreso).options(selectinload(OtroIngreso.banco), selectinload(OtroIngreso.tercero),
                                          selectinload(OtroIngreso.cuenta_rel))
             .filter(OtroIngreso.fecha.between(desde, hasta)).order_by(OtroIngreso.fecha).all())
    iva = impuestos.resumen_iva(s, desde, hasta)
    cart, _ = cartera.cartera_por_edades(s, hasta)
    hojas = {
        "Estado de resultados": (["Concepto", "Valor"],
                                 [(f["celdas"] if isinstance(f, dict) else f) for f in _filas_resultados(er)]),
        "Balance general": (["Concepto", "Valor"],
                            [(f["celdas"] if isinstance(f, dict) else f) for f in _filas_balance(bg)]),
        "Balance de prueba": (["Cuenta", "Nombre", "Saldo inicial", "Débitos", "Créditos", "Saldo final"],
                              [[f.cuenta.codigo, f.cuenta.nombre, f.saldo_inicial, f.debitos, f.creditos, f.saldo_final]
                               for f in bp_filas]),
        "Libro diario": (["Fecha", "Comprobante", "Descripción", "Cuenta", "Nombre cuenta", "NIT", "Tercero",
                          "Débito", "Crédito"],
                         [[a.fecha, f"{a.tipo}-{a.numero}", a.descripcion, m.cuenta, m.cuenta_rel.nombre,
                           m.tercero.nit if m.tercero else "", m.tercero.nombre if m.tercero else "", m.debito, m.credito]
                          for a in asientos for m in a.lineas]),
        "Ventas": (["Tipo", "Número", "Fecha", "Cliente", "NIT", "Base", "IVA", "Total", "ReteIVA", "CUFE"],
                   [[d.tipo, d.numero, d.fecha, d.cliente.nombre, d.cliente.nit, d.signo * d.ingreso, d.signo * d.iva,
                     d.signo * d.total, d.reteiva_valor if d.reteiva_aplica else 0, d.cufe or ""] for d in ventas]),
        "Gastos": (["Fecha", "Número", "Proveedor", "NIT", "Categoría", "Cuenta", "Base", "IVA", "IVA descontable",
                    "Total", "CUFE"],
                   [[g.fecha, g.numero or "", g.proveedor.nombre if g.proveedor else "",
                     g.proveedor.nit if g.proveedor else "", g.categoria.nombre, g.categoria.cuenta,
                     g.signo * g.subtotal, g.signo * g.iva, g.signo * g.iva_desc_valor, g.signo * g.total, g.cufe or ""]
                    for g in gastos]),
        "Recaudos": (["Fecha", "Cliente", "Banco", "Valor", "Aplicado a"],
                     [[r.fecha, r.cliente.nombre, r.banco.nombre, r.valor,
                       ", ".join(f"{a.documento.numero} ({a.valor:,.0f})" for a in r.aplicaciones)] for r in recaudos]),
        "Otros ingresos": (["Fecha", "Concepto", "Cuenta", "Banco", "Tercero", "NIT", "Valor", "Descuentos banco"],
                           [[o.fecha, o.concepto, f"{o.cuenta} {o.cuenta_rel.nombre}", o.banco.nombre,
                             o.tercero.nombre if o.tercero else "", o.tercero.nit if o.tercero else "", o.valor,
                             o.descuentos] for o in otros]),
        "IVA": (["Concepto", "Valor"],
                [["Ingresos gravados", iva.ingresos_gravados], ["IVA generado", iva.iva_generado_neto],
                 ["IVA descontable", iva.iva_descontable], ["ReteIVA", iva.reteiva], ["Neto", iva.neto]]),
        "Cartera": (["Cliente", "NIT", "Total por cobrar"], [[f["cliente"].nombre, f["cliente"].nit, f["total"]] for f in cart]),
    }
    return descargar(exportar.excel(hojas), f"paquete_contable_{desde}_{hasta}.xlsx", XLSX)


@bp.route("/ventas-clientes")
def ventas_clientes():
    """Ingresos por cliente y por mes, con comparación contra el año anterior."""
    from collections import defaultdict
    from ..models import DocumentoVenta
    s = Session()
    from . import anio_arg
    anio = anio_arg()
    por_cliente = defaultdict(lambda: [CERO] * 12)
    totales_mes = [CERO] * 12
    anterior = defaultdict(lambda: CERO)
    for d in s.query(DocumentoVenta).filter(DocumentoVenta.anulada.is_(False),
                                            DocumentoVenta.fecha.between(date(anio - 1, 1, 1), date(anio, 12, 31))):
        v = d.signo * d.ingreso
        if d.fecha.year == anio:
            por_cliente[d.cliente][d.fecha.month - 1] += v
            totales_mes[d.fecha.month - 1] += v
        else:
            anterior[d.cliente] += v
    filas = sorted(((c, meses, sum(meses, CERO), anterior.get(c, CERO)) for c, meses in por_cliente.items()),
                   key=lambda x: -x[2])
    for c in anterior:
        if c not in por_cliente:
            filas.append((c, [CERO] * 12, CERO, anterior[c]))
    total = sum(totales_mes, CERO)
    total_anterior = sum(anterior.values(), CERO)
    if request.args.get("xlsx"):
        enc = ["Cliente", "NIT"] + [f"{m:02d}" for m in range(1, 13)] + [f"Total {anio}", f"Total {anio - 1}"]
        datos = [[c.nombre, c.nit] + meses + [t, ant] for c, meses, t, ant in filas]
        return descargar(exportar.excel({"Ventas por cliente": (enc, datos)}), f"ventas_clientes_{anio}.xlsx", XLSX)
    return render_template("contabilidad/ventas_clientes.html", filas=filas, totales_mes=totales_mes, total=total,
                           total_anterior=total_anterior, anio=anio)


@bp.route("/asiento/nuevo", methods=["GET", "POST"])
@bp.route("/asiento/<int:id>", methods=["GET", "POST"])
def asiento(id=None):
    s = Session()
    a = s.get(Asiento, id) if id else None
    if id and a is None:
        abort(404)
    if request.method == "POST":
        if a is not None and not a.manual:
            flash("Este asiento es automático: modifique el documento que lo origina.", "error")
            return redirect(url_for("contabilidad.asiento", id=id))
        if request.form.get("accion") == "eliminar" and a is not None:
            try:
                contab.verificar_periodo(s, a.fecha)
                from .. import bancos
                bancos.liberar(s, "asiento", a.id)
                s.delete(a)
                s.commit()
                flash("Asiento eliminado.", "ok")
                return redirect(url_for("contabilidad.diario"))
            except contab.ErrorContable as e:
                s.rollback()
                flash(str(e), "error")
                return redirect(url_for("contabilidad.asiento", id=id))
        try:
            lineas = []
            cuentas = request.form.getlist("cuenta")
            terceros = request.form.getlist("tercero")
            debitos, creditos, detalles = (request.form.getlist(k) for k in ("debito", "credito", "detalle"))
            for i, cta in enumerate(cuentas):
                if not cta:
                    continue
                tercero = terceros[i] if i < len(terceros) else ""
                try:
                    debito = contab.d(debitos[i] if i < len(debitos) else 0)
                    credito = contab.d(creditos[i] if i < len(creditos) else 0)
                except Exception as e:  # noqa: BLE001
                    raise contab.ErrorContable(f"Monto no válido en la línea {i + 1} (use 1.234.567,89).") from e
                if debito < 0 or credito < 0:
                    raise contab.ErrorContable(f"Los valores de la línea {i + 1} no pueden ser negativos.")
                lineas.append((cta, debito, credito, int(tercero) if tercero.isdigit() else None,
                               detalles[i] if i < len(detalles) else ""))
            if len(lineas) < 2:
                raise contab.ErrorContable("Agregue al menos dos líneas.")
            if sum((l[1] for l in lineas), CERO) == 0:
                raise contab.ErrorContable("Indique al menos un valor distinto de cero.")
            validas = {c.codigo for c in _cuentas_movimiento(s)}
            for cta, *_ in lineas:
                if cta not in validas:
                    raise contab.ErrorContable(f"La cuenta {cta} no existe o no recibe movimientos.")
            nuevo = contab.guardar_asiento(s, origen=None, tipo="AJ", fecha=fecha_arg("fecha", date.today()),
                                           descripcion=request.form.get("descripcion") or "Asiento manual",
                                           tercero_id=None, lineas=lineas, asiento=a)
            if nuevo is None:
                raise contab.ErrorContable("El asiento no tiene movimientos.")
            s.commit()
            flash("Asiento guardado.", "ok")
            return redirect(url_for("contabilidad.asiento", id=nuevo.id))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {e}", "error")
            a = s.get(Asiento, id) if id else None
    return render_template("contabilidad/asiento.html", a=a, cuentas=_cuentas_movimiento(s),
                           terceros=s.query(Tercero).order_by(Tercero.nombre).all())


@bp.route("/puc", methods=["GET", "POST"])
def puc():
    s = Session()
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip()
        nombre = request.form.get("nombre", "").strip()
        if not codigo.isdigit() or len(codigo) not in (6, 8, 10) or not nombre:
            flash("El código debe tener 6, 8 o 10 dígitos y la cuenta debe tener nombre.", "error")
        elif s.get(Cuenta, codigo):
            cta = s.get(Cuenta, codigo)
            cta.nombre = nombre
            cta.activa = request.form.get("activa") != "0"
            s.commit()
            flash("Cuenta actualizada.", "ok")
        else:
            padre = next((s.get(Cuenta, codigo[:n]) for n in (8, 6, 4) if n < len(codigo) and s.get(Cuenta, codigo[:n])),
                         None)
            if padre is None:
                flash("No existe la cuenta padre en el PUC.", "error")
            else:
                if padre.movimiento:
                    from ..models import Movimiento
                    if s.query(Movimiento).filter_by(cuenta=padre.codigo).first():
                        flash(f"La cuenta {padre.codigo} ya tiene movimientos; no se le pueden crear subcuentas.",
                              "error")
                        return redirect(url_for("contabilidad.puc"))
                    padre.movimiento = False
                s.add(Cuenta(codigo=codigo, nombre=nombre, naturaleza=padre.naturaleza, movimiento=True))
                s.commit()
                flash("Cuenta creada.", "ok")
        return redirect(url_for("contabilidad.puc"))
    return render_template("contabilidad/puc.html", cuentas=s.query(Cuenta).order_by(Cuenta.codigo).all())
