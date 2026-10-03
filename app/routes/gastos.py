"""Gastos, IVA descontable y cuentas por pagar."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import archivos, contab, exportar, importacion
from ..db import Session
from ..models import (CERO, TIPOS_SOPORTE, Asiento, Banco, CategoriaGasto, Gasto, PagoGasto,
                      Tercero)
from . import XLSX, anio_arg, check, descargar, dinero, fecha_arg

bp = Blueprint("gastos", __name__)


def opciones_pago(s):
    """Cuentas desde las que se puede pagar un gasto: bancos, caja y reembolso al socio."""
    ops = [(b.cuenta, b.nombre) for b in s.query(Banco).filter_by(activo=True).order_by(Banco.id)]
    ops += [(contab.CTA_CAJA, "Caja (efectivo)"), (contab.CTA_SOCIOS, "Pagado por el socio (reembolsable)")]
    return ops


def _contexto_form(s):
    return {"categorias": s.query(CategoriaGasto).filter_by(activa=True).order_by(CategoriaGasto.nombre).all(),
            "proveedores": s.query(Tercero).filter_by(es_proveedor=True).order_by(Tercero.nombre).all(),
            "opciones_pago": opciones_pago(s), "tipos": TIPOS_SOPORTE}


@bp.route("/gastos")
def lista():
    s = Session()
    anio = anio_arg()
    q = s.query(Gasto).filter(Gasto.fecha.between(date(anio, 1, 1), date(anio, 12, 31)))
    categoria_id = request.args.get("categoria", type=int)
    if categoria_id:
        q = q.filter(Gasto.categoria_id == categoria_id)
    if request.args.get("revisar"):
        q = q.filter(Gasto.revisado.is_(False))
    if request.args.get("sin_soporte"):
        q = q.filter(Gasto.soporte_archivo.is_(None), Gasto.xml_archivo.is_(None))
    if request.args.get("por_pagar"):
        q = q.filter(Gasto.forma_pago == "credito")
    gastos = q.order_by(Gasto.fecha.desc(), Gasto.id.desc()).all()
    if request.args.get("por_pagar"):
        gastos = [g for g in gastos if g.saldo > 0]
    if request.args.get("xlsx"):
        enc = ["Fecha", "Tipo", "Número", "Proveedor", "NIT", "Categoría", "Cuenta", "Descripción", "Base", "IVA",
               "IVA descontable", "Otros impuestos", "Total", "Saldo por pagar", "CUFE"]
        datos = [[g.fecha, TIPOS_SOPORTE.get(g.tipo_soporte), g.numero or "",
                  g.proveedor.nombre if g.proveedor else "", g.proveedor.nit if g.proveedor else "",
                  g.categoria.nombre, g.categoria.cuenta, g.descripcion or "", g.signo * g.subtotal, g.signo * g.iva,
                  g.signo * g.iva_desc_valor, g.signo * g.otros_impuestos, g.signo * g.total,
                  g.saldo if g.tipo_soporte != "NC" else 0, g.cufe or ""] for g in gastos]
        return descargar(exportar.excel({"Gastos": (enc, datos)}), f"gastos_{anio}.xlsx", XLSX)
    totales = {k: sum((g.signo * getattr(g, k) for g in gastos), CERO)
               for k in ("subtotal", "iva", "iva_desc_valor", "total")}
    return render_template("gastos/lista.html", gastos=gastos, totales=totales, anio=anio,
                           categorias=s.query(CategoriaGasto).order_by(CategoriaGasto.nombre).all(),
                           categoria_id=categoria_id, tipos=TIPOS_SOPORTE)


@bp.route("/gastos/importar", methods=["GET", "POST"])
def importar():
    resultados = None
    if request.method == "POST":
        s = Session()
        resultados = []
        forzar = request.form.get("forzar") or None
        for f in request.files.getlist("archivos"):
            if f.filename:
                resultados += importacion.importar_archivo(s, f.filename, f.read(), forzar=forzar)
    return render_template("importar.html", resultados=resultados, destino="gastos",
                           titulo="Importar facturas de proveedores")


def _proveedor_desde_form(s):
    pid = request.form.get("proveedor_id")
    if pid == "nuevo":
        nit = request.form.get("nuevo_nit", "").strip()
        nombre = request.form.get("nuevo_nombre", "").strip()
        if not nit or not nombre:
            raise ValueError("Para crear el proveedor indique NIT/cédula y nombre.")
        t = s.query(Tercero).filter_by(nit=nit).one_or_none()
        if t is None:
            t = Tercero(nit=nit, nombre=nombre, tipo_doc=request.form.get("nuevo_tipo_doc", "31"))
            s.add(t)
        t.es_proveedor = True
        s.flush()
        return t.id
    return int(pid) if pid else None


def _llenar_gasto(s, g):
    g.tipo_soporte = request.form.get("tipo_soporte", "FE")
    g.numero = request.form.get("numero", "").strip() or None
    g.cufe = request.form.get("cufe", "").strip() or None
    g.fecha = fecha_arg("fecha", date.today())
    g.vencimiento = fecha_arg("vencimiento")
    g.proveedor_id = _proveedor_desde_form(s)
    g.categoria_id = int(request.form["categoria_id"])
    g.descripcion = request.form.get("descripcion") or None
    g.subtotal = dinero("subtotal")
    g.iva = dinero("iva")
    g.iva_descontable = check("iva_descontable")
    g.otros_impuestos = dinero("otros_impuestos")
    g.total = g.subtotal + g.iva + g.otros_impuestos
    g.forma_pago = request.form.get("forma_pago", "contado")
    g.cuenta_pago = request.form.get("cuenta_pago") if g.forma_pago == "contado" else None
    g.notas = request.form.get("notas") or None
    g.revisado = True
    if g.iva and g.iva_descontable and g.tipo_soporte not in ("FE", "DS", "NC"):
        flash("Ojo: el IVA solo es descontable si está soportado en factura electrónica o documento soporte.",
              "advertencia")
    soporte = archivos.guardar_upload("gastos", request.files.get("soporte"))
    if soporte:
        g.soporte_archivo = soporte


@bp.route("/gastos/nuevo", methods=["GET", "POST"])
def nuevo():
    s = Session()
    if request.method == "POST":
        try:
            g = Gasto(origen="manual")
            _llenar_gasto(s, g)
            s.add(g)
            s.flush()
            s.refresh(g)
            contab.contabilizar_gasto(s, g)
            s.commit()
            flash("Gasto registrado.", "ok")
            if request.form.get("otro"):
                return redirect(url_for("gastos.nuevo"))
            return redirect(url_for("gastos.lista"))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo registrar: {e}", "error")
    return render_template("gastos/form.html", g=None, form=request.form, **_contexto_form(s))


@bp.route("/gastos/<int:id>", methods=["GET", "POST"])
def detalle(id):
    s = Session()
    g = s.get(Gasto, id) or abort(404)
    if request.method == "POST":
        accion = request.form.get("accion")
        try:
            if accion == "eliminar":
                contab.borrar_asientos(s, f"gasto:{g.id}")
                for p in g.pagos:
                    contab.borrar_asientos(s, f"pagogasto:{p.id}")
                s.delete(g)
                s.commit()
                flash("Gasto eliminado.", "ok")
                return redirect(url_for("gastos.lista"))
            if accion == "pago":
                valor = dinero("pago_valor")
                if valor <= 0 or valor > g.saldo:
                    raise ValueError("El valor del pago debe ser mayor que cero y no superar el saldo.")
                p = PagoGasto(fecha=fecha_arg("pago_fecha", date.today()),
                              cuenta_pago=request.form["pago_cuenta"], valor=valor)
                s.add(p)
                g.pagos.append(p)
                s.flush()
                contab.contabilizar_pago_gasto(s, p)
                s.commit()
                flash("Pago registrado.", "ok")
                return redirect(url_for("gastos.detalle", id=id))
            if accion == "borrar_pago":
                p = s.get(PagoGasto, int(request.form["pago_id"]))
                contab.borrar_asientos(s, f"pagogasto:{p.id}")
                s.delete(p)
                s.commit()
                return redirect(url_for("gastos.detalle", id=id))
            _llenar_gasto(s, g)
            if g.forma_pago == "contado" and g.pagos:
                raise ValueError("El gasto tiene pagos registrados; elimínelos antes de marcarlo de contado.")
            s.flush()
            s.refresh(g)
            contab.contabilizar_gasto(s, g)
            s.commit()
            flash("Cambios guardados.", "ok")
            return redirect(url_for("gastos.detalle", id=id))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {e}", "error")
            g = s.get(Gasto, id)
    asientos = s.query(Asiento).filter(Asiento.origen.in_(
        [f"gasto:{g.id}"] + [f"pagogasto:{p.id}" for p in g.pagos])).order_by(Asiento.fecha).all()
    return render_template("gastos/form.html", g=g, form={}, asientos=asientos, **_contexto_form(s))
