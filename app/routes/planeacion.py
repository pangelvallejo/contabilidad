"""Flujo de caja, proyección de impuestos, depreciaciones y cierre."""
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import impuestos, planeacion
from ..db import Session
from . import anio_arg

bp = Blueprint("planeacion", __name__)


@bp.route("/flujo-caja")
def flujo_caja():
    s = Session()
    meses = min(max(request.args.get("meses", type=int) or 6, 1), 24)
    filas, promedio = planeacion.flujo_de_caja(s, meses)
    return render_template("planeacion/flujo_caja.html", filas=filas, promedio=promedio, meses=meses,
                           proyeccion=impuestos.proyeccion_anual(s, date.today().year))


@bp.route("/contabilidad/depreciaciones", methods=["GET", "POST"])
def depreciaciones():
    s = Session()
    if request.method == "POST":
        n, omitidos = planeacion.causar_depreciaciones(s, forzar=True)
        flash(f"{n} asiento(s) de depreciación creados." if n else "No había depreciaciones pendientes.", "ok")
        if omitidos:
            flash(f"{omitidos} cuota(s) quedaron sin causar porque caen en un periodo bloqueado.", "advertencia")
        return redirect(url_for("planeacion.depreciaciones"))
    return render_template("planeacion/depreciaciones.html", filas=planeacion.resumen_activos(s))


@bp.route("/contabilidad/cierre", methods=["GET", "POST"])
def cierre():
    s = Session()
    anio = anio_arg() if request.args.get("anio") else date.today().year - 1
    if request.method == "POST":
        try:
            anio = int(request.form.get("anio", ""))
            if not 2000 <= anio <= 2100:
                raise ValueError
        except ValueError:
            flash("Año no válido.", "error")
            return redirect(url_for("planeacion.cierre"))
        try:
            if request.form.get("accion") == "reabrir":
                planeacion.reabrir_anio(s, anio)
                flash(f"Año {anio} reabierto. Recuerde ajustar la fecha de bloqueo en Configuración.", "ok")
            else:
                planeacion.causar_depreciaciones(s, date(anio, 12, 31), forzar=True)
                impuestos.causar_simple_anual(s, anio)
                resultado = planeacion.cerrar_anio(s, anio)
                flash(f"Año {anio} cerrado. Resultado del ejercicio: {resultado:,.0f}. Contabilidad bloqueada "
                      f"hasta el 31/12/{anio}.", "ok")
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo completar: {e}", "error")
        return redirect(url_for("planeacion.cierre", anio=anio))
    from .. import reportes
    er = reportes.estado_resultados(s, date(anio, 1, 1), date(anio, 12, 31))
    return render_template("planeacion/cierre.html", anio=anio, cerrado=planeacion.anio_cerrado(s, anio), er=er,
                           dec=impuestos.declaracion_simple(s, anio))
