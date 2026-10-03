"""IVA, SIMPLE (2593, F260), IVA anual (F300), calendario y exógena."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import archivos, contab, exogena, exportar, impuestos
from ..config import BIMESTRES, SIMPLE_TARIFA_ANUAL, SIMPLE_TARIFA_BIMESTRAL
from ..db import Session
from ..models import Banco, PagoImpuesto, Vencimiento
from . import XLSX, anio_arg, check, descargar, destino_seguro, dinero, fecha_arg

bp = Blueprint("impuestos", __name__, url_prefix="/impuestos")


@bp.route("/iva")
def iva():
    s = Session()
    anio = anio_arg()
    filas = [(b, impuestos.nombre_bimestre(b), impuestos.resumen_iva_bimestre(s, anio, b)) for b in BIMESTRES]
    anual = impuestos.declaracion_iva(s, anio)
    return render_template("impuestos/iva.html", filas=filas, anio=anio, anual=anual)


@bp.route("/iva/<int:anio>/<int:bim>")
def iva_detalle(anio, bim):
    if bim not in BIMESTRES:
        abort(404)
    s = Session()
    r = impuestos.resumen_iva_bimestre(s, anio, bim)
    if request.args.get("xlsx"):
        hojas = {
            "IVA generado": (["Tipo", "Número", "Fecha", "Cliente", "NIT", "Base", "IVA"],
                             [[v.tipo, v.numero, v.fecha, v.cliente.nombre, v.cliente.nit, v.signo * v.ingreso,
                               v.signo * v.iva] for v in r.ventas]),
            "IVA descontable": (["Fecha", "Número", "Proveedor", "NIT", "Categoría", "Base", "IVA", "Descontable"],
                                [[g.fecha, g.numero or "", g.proveedor.nombre if g.proveedor else "",
                                  g.proveedor.nit if g.proveedor else "", g.categoria.nombre, g.signo * g.subtotal,
                                  g.signo * g.iva, g.signo * g.iva_desc_valor] for g in r.gastos]),
            "ReteIVA": (["Factura", "Fecha retención", "Cliente", "NIT", "Valor", "Certificado"],
                        [[v.numero, v.reteiva_fecha or v.fecha, v.cliente.nombre, v.cliente.nit, v.reteiva_valor,
                          "Sí" if v.cert_recibido else "No"] for v in r.retenciones]),
        }
        return descargar(exportar.excel(hojas), f"iva_{anio}_bimestre{bim}.xlsx", XLSX)
    return render_template("impuestos/iva_detalle.html", r=r, anio=anio, bim=bim,
                           nombre=impuestos.nombre_bimestre(bim))


@bp.route("/simple")
def simple():
    s = Session()
    anio = anio_arg()
    recibos = [impuestos.recibo_2593(s, anio, b) for b in BIMESTRES]
    return render_template("impuestos/simple.html", recibos=recibos, anio=anio, nombres=impuestos.nombre_bimestre,
                           bancos=s.query(Banco).filter_by(activo=True).all(),
                           tabla=SIMPLE_TARIFA_BIMESTRAL, uvt=impuestos.uvt(anio),
                           base=contab.config(s, "simple_base", "causacion"))


@bp.route("/pago", methods=["POST"])
def registrar_pago():
    s = Session()
    try:
        p = PagoImpuesto(formulario=request.form.get("formulario", "2593"), anio=int(request.form["anio"]),
                         bimestre=request.form.get("bimestre", type=int), fecha=fecha_arg("fecha", date.today()),
                         valor_simple=dinero("valor_simple"), valor_iva=dinero("valor_iva"),
                         banco_id=int(request.form["banco_id"]),
                         numero_formulario=request.form.get("numero_formulario") or None)
        p.archivo = archivos.guardar_upload("impuestos", request.files.get("archivo"))
        if p.total <= 0:
            raise ValueError("El valor pagado debe ser mayor que cero.")
        s.add(p)
        s.flush()
        s.refresh(p)
        contab.contabilizar_pago_impuesto(s, p)
        if p.formulario == "2593":
            v = (s.query(Vencimiento).filter(Vencimiento.obligacion.like("Recibo 2593%"),
                                             Vencimiento.periodo.like(f"Bimestre {p.bimestre} %{p.anio}")).first())
            if v:
                v.cumplido = True
        s.commit()
        flash("Pago registrado y contabilizado.", "ok")
    except Exception as e:  # noqa: BLE001
        s.rollback()
        flash(f"No se pudo registrar el pago: {e}", "error")
    return redirect(destino_seguro(request.form.get("volver"), url_for("impuestos.simple")))


@bp.route("/pago/<int:id>/eliminar", methods=["POST"])
def eliminar_pago(id):
    s = Session()
    p = s.get(PagoImpuesto, id) or abort(404)
    contab.borrar_asientos(s, f"impuesto:{p.id}")
    s.delete(p)
    s.commit()
    flash("Pago eliminado.", "ok")
    return redirect(destino_seguro(request.form.get("volver"), url_for("impuestos.simple")))


@bp.route("/f260", methods=["GET", "POST"])
def f260():
    s = Session()
    anio = anio_arg()
    if request.method == "POST":
        impuestos.causar_simple_anual(s, anio)
        s.commit()
        flash(f"Impuesto SIMPLE {anio} causado en la contabilidad (asiento al 31 de diciembre).", "ok")
        return redirect(url_for("impuestos.f260", anio=anio))
    dec = impuestos.declaracion_simple(s, anio)
    pagos = s.query(PagoImpuesto).filter_by(formulario="260", anio=anio).all()
    return render_template("impuestos/f260.html", dec=dec, anio=anio, tabla=SIMPLE_TARIFA_ANUAL,
                           uvt=impuestos.uvt(anio), pagos=pagos, bancos=s.query(Banco).filter_by(activo=True).all())


@bp.route("/f300")
def f300():
    s = Session()
    anio = anio_arg()
    dec = impuestos.declaracion_iva(s, anio)
    pagos = s.query(PagoImpuesto).filter_by(formulario="300", anio=anio).all()
    bimestres = [(b, impuestos.nombre_bimestre(b), impuestos.resumen_iva_bimestre(s, anio, b)) for b in BIMESTRES]
    return render_template("impuestos/f300.html", dec=dec, anio=anio, pagos=pagos, bimestres=bimestres,
                           bancos=s.query(Banco).filter_by(activo=True).all())


@bp.route("/calendario", methods=["GET", "POST"])
def calendario():
    s = Session()
    if request.method == "POST":
        if request.form.get("accion") == "agregar":
            s.add(Vencimiento(obligacion=request.form["obligacion"], periodo=request.form.get("periodo"),
                              fecha=fecha_arg("fecha"), notas=request.form.get("notas") or None))
        elif request.form.get("eliminar"):
            s.delete(s.get(Vencimiento, int(request.form["eliminar"])))
        else:
            for v in s.query(Vencimiento):
                v.fecha = fecha_arg(f"fecha_{v.id}", v.fecha)
                v.cumplido = check(f"cumplido_{v.id}")
        s.commit()
        flash("Calendario actualizado.", "ok")
        return redirect(url_for("impuestos.calendario"))
    return render_template("impuestos/calendario.html", vencimientos=s.query(Vencimiento).order_by(Vencimiento.fecha))


@bp.route("/exogena")
def exogena_vista():
    s = Session()
    anio = anio_arg()
    hojas, incompletos = exogena.generar(s, anio)
    if request.args.get("xlsx"):
        return descargar(exportar.excel(hojas), f"exogena_{anio}.xlsx", XLSX)
    return render_template("impuestos/exogena.html", hojas=hojas, incompletos=incompletos, anio=anio)
