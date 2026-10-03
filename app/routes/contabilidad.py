"""Libros, estados financieros, asientos manuales y PUC."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import contab, exportar, reportes
from ..db import Session
from ..models import Asiento, Cuenta, Tercero
from . import XLSX, descargar, fecha_arg, periodo

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
    return render_template("contabilidad/diario.html", asientos=asientos, desde=desde, hasta=hasta, tipo=tipo,
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
    return render_template("contabilidad/balance_prueba.html", filas=filas, tot_d=tot_d, tot_c=tot_c, desde=desde,
                           hasta=hasta, nivel=nivel)


@bp.route("/resultados")
def resultados():
    s = Session()
    desde, hasta = periodo()
    er = reportes.estado_resultados(s, desde, hasta)
    return render_template("contabilidad/resultados.html", er=er, desde=desde, hasta=hasta)


@bp.route("/balance")
def balance():
    s = Session()
    corte = fecha_arg("corte", date.today())
    bg = reportes.balance_general(s, corte)
    return render_template("contabilidad/balance.html", bg=bg, corte=corte)


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
            s.delete(a)
            s.commit()
            flash("Asiento eliminado.", "ok")
            return redirect(url_for("contabilidad.diario"))
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
                lineas.append((cta, debito, credito, int(tercero) if tercero.isdigit() else None,
                               detalles[i] if i < len(detalles) else ""))
            if len(lineas) < 2:
                raise contab.ErrorContable("Agregue al menos dos líneas.")
            validas = {c.codigo for c in _cuentas_movimiento(s)}
            for cta, *_ in lineas:
                if cta not in validas:
                    raise contab.ErrorContable(f"La cuenta {cta} no existe o no recibe movimientos.")
            nuevo = contab.guardar_asiento(s, origen=None, tipo="AJ", fecha=fecha_arg("fecha", date.today()),
                                           descripcion=request.form.get("descripcion") or "Asiento manual",
                                           tercero_id=None, lineas=lineas, asiento=a)
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
