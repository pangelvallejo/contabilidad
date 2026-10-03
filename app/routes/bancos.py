"""Extractos bancarios y conciliación."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import bancos, cartera, contab, reportes
from ..db import Session
from ..models import (CERO, Banco, CategoriaGasto, DocumentoVenta, Gasto, MovimientoBanco, PagoGasto, Recaudo,
                      Tercero)
from . import check, dinero, fecha_arg

bp = Blueprint("bancos", __name__, url_prefix="/bancos")


def _banco(s):
    bid = request.values.get("banco", type=int)
    b = s.get(Banco, bid) if bid else s.query(Banco).filter_by(activo=True).order_by(Banco.id).first()
    if b is None:
        abort(404)
    return b


@bp.route("/conciliacion", methods=["GET", "POST"])
def conciliacion():
    s = Session()
    banco = _banco(s)
    if request.method == "POST":
        archivo = request.files.get("extracto")
        if archivo is None or not archivo.filename:
            flash("Seleccione el archivo del extracto (CSV o Excel).", "error")
        else:
            try:
                nuevas, repetidas = bancos.importar_extracto(s, banco, archivo.filename, archivo.read())
                auto = bancos.conciliar_automatico(s, banco.id)
                flash(f"{nuevas} movimiento(s) importado(s), {repetidas} ya existían. {auto} conciliado(s) "
                      "automáticamente.", "ok")
            except Exception as e:  # noqa: BLE001
                s.rollback()
                flash(f"No se pudo importar el extracto: {e}", "error")
        return redirect(url_for("bancos.conciliacion", banco=banco.id))
    estado = request.args.get("estado", "pendiente")
    q = s.query(MovimientoBanco).filter_by(banco_id=banco.id)
    if estado != "todos":
        q = q.filter(MovimientoBanco.estado == estado)
    movs = q.order_by(MovimientoBanco.fecha.desc(), MovimientoBanco.id.desc()).limit(300).all()
    sugerencias = {m.id: bancos.candidatos(s, m) for m in movs if m.estado == "pendiente"}
    categorias = {m.id: bancos.sugerir_categoria_bancaria(m.descripcion) for m in movs if m.estado == "pendiente"}
    corte = fecha_arg("corte", date.today())
    pendientes = s.query(MovimientoBanco).filter_by(banco_id=banco.id, estado="pendiente").count()
    return render_template(
        "bancos/conciliacion.html", banco=banco, movs=movs, sugerencias=sugerencias, categorias_sug=categorias,
        estado=estado, bancos=s.query(Banco).filter_by(activo=True).all(), pendientes=pendientes,
        saldo_libros=reportes.saldo_cuenta(s, banco.cuenta, corte), saldo_extracto=bancos.saldo_extracto(s, banco.id, corte),
        corte=corte, categorias=s.query(CategoriaGasto).filter_by(activa=True).order_by(CategoriaGasto.nombre).all(),
        clientes=s.query(Tercero).filter_by(es_cliente=True).order_by(Tercero.nombre).all(),
        proveedores=s.query(Tercero).filter_by(es_proveedor=True).order_by(Tercero.nombre).all())


@bp.route("/movimiento/<int:id>", methods=["POST"])
def accion(id):
    """Acciones sobre una línea del extracto: vincular, crear gasto, crear recaudo, ignorar, deshacer."""
    s = Session()
    mov = s.get(MovimientoBanco, id) or abort(404)
    accion = request.form.get("accion")
    try:
        if accion == "vincular":
            tipo, _, oid = request.form["origen"].partition(":")
            mov.origen_tipo, mov.origen_id, mov.estado = tipo, int(oid), "conciliado"
        elif accion == "ignorar":
            mov.estado = "ignorado"
        elif accion == "pendiente":
            mov.estado, mov.origen_tipo, mov.origen_id = "pendiente", None, None
        elif accion == "gasto":
            categoria = s.get(CategoriaGasto, int(request.form["categoria_id"]))
            g = Gasto(tipo_soporte=request.form.get("tipo_soporte", "RE"), fecha=mov.fecha,
                      proveedor_id=request.form.get("proveedor_id", type=int) or None, categoria=categoria,
                      descripcion=mov.descripcion, subtotal=-mov.valor, iva=CERO, iva_descontable=False,
                      total=-mov.valor, forma_pago="contado", cuenta_pago=mov.banco.cuenta, origen="banco",
                      numero=mov.referencia)
            if mov.valor >= 0:
                raise ValueError("Solo las salidas de dinero se registran como gasto.")
            s.add(g)
            s.flush()
            s.refresh(g)
            contab.contabilizar_gasto(s, g)
            mov.origen_tipo, mov.origen_id, mov.estado = "gasto", g.id, "conciliado"
        elif accion == "pago_proveedor":
            g = s.get(Gasto, int(request.form["gasto_id"]))
            if g is None or -mov.valor > g.saldo:
                raise ValueError("El valor supera el saldo de la factura del proveedor.")
            p = PagoGasto(fecha=mov.fecha, cuenta_pago=mov.banco.cuenta, valor=-mov.valor)
            s.add(p)
            g.pagos.append(p)
            s.flush()
            contab.contabilizar_pago_gasto(s, p)
            mov.origen_tipo, mov.origen_id, mov.estado = "pagogasto", p.id, "conciliado"
        elif accion == "recaudo":
            if mov.valor <= 0:
                raise ValueError("Solo las entradas de dinero se registran como recaudo.")
            cliente_id = int(request.form["cliente_id"])
            rec = Recaudo(fecha=mov.fecha, cliente_id=cliente_id, banco_id=mov.banco_id, valor=mov.valor,
                          medio_electronico=check("medio_electronico"), referencia=(mov.referencia or mov.descripcion)[:80])
            s.add(rec)
            sugerencia, _ = cartera.aplicar_automatico(s, cliente_id, mov.valor)
            cartera.registrar_aplicaciones(rec, {doc: v for doc, _, v in sugerencia})
            s.flush()
            contab.contabilizar_recaudo(s, rec)
            mov.origen_tipo, mov.origen_id, mov.estado = "recaudo", rec.id, "conciliado"
        elif accion == "ingreso":
            # Rendimientos, reintegros u otros ingresos sin factura: asiento banco contra la cuenta elegida
            cuenta = request.form.get("cuenta", "421005")
            a = contab.guardar_asiento(s, origen=None, tipo="AJ", fecha=mov.fecha,
                                       descripcion=f"Banco: {mov.descripcion}"[:250], tercero_id=None,
                                       lineas=[(mov.banco.cuenta, mov.valor, 0, None, None), (cuenta, 0, mov.valor, None, None)])
            mov.origen_tipo, mov.origen_id, mov.estado = "asiento", a.id, "conciliado"
        s.commit()
    except Exception as e:  # noqa: BLE001
        s.rollback()
        flash(f"No se pudo aplicar: {e}", "error")
    return redirect(url_for("bancos.conciliacion", banco=mov.banco_id, estado=request.form.get("volver_estado", "pendiente")))


@bp.route("/facturas-proveedor")
def facturas_proveedor():
    """Facturas a crédito abiertas de un proveedor (para el selector de pagos)."""
    s = Session()
    pid = request.args.get("proveedor", type=int)
    res = [{"id": g.id, "texto": f"{g.numero or ''} {g.fecha:%d/%m} saldo {g.saldo:,.0f}"}
           for g in s.query(Gasto).filter_by(proveedor_id=pid, forma_pago="credito") if g.saldo > 0]
    return {"facturas": res}
