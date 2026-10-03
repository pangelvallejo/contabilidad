"""Extractos bancarios y conciliación."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import bancos, cartera, contab, reportes
from ..db import Session
from ..models import (CERO, Banco, CategoriaGasto, Cuenta, Gasto, MovimientoBanco, PagoGasto, Recaudo,
                      Tercero)
from . import check, fecha_arg

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
                nuevas, repetidas, omitidas = bancos.importar_extracto(s, banco, archivo.filename, archivo.read())
                auto = bancos.conciliar_automatico(s, banco.id)
                flash(f"{nuevas} movimiento(s) importado(s), {repetidas} ya existían. {auto} conciliado(s) "
                      "automáticamente.", "ok")
                if omitidas:
                    flash(f"{omitidas} fila(s) del archivo se omitieron (sin fecha o valor, o líneas de saldo).", "info")
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
        if accion == "pendiente":
            bancos.deshacer(s, mov)
            s.commit()
            return redirect(url_for("bancos.conciliacion", banco=mov.banco_id,
                                    estado=request.form.get("volver_estado", "pendiente")))
        if mov.estado != "pendiente":
            raise ValueError("La línea ya fue conciliada o ignorada; use 'Deshacer' primero.")
        if accion == "vincular":
            tipo, _, oid = request.form.get("origen", "").partition(":")
            if tipo not in bancos.TIPOS_ORIGEN or not oid.isdigit() or not bancos.origen_existe(s, tipo, int(oid)):
                raise ValueError("Seleccione un movimiento válido para vincular.")
            if (tipo, int(oid)) in bancos._ya_conciliados(s):
                raise ValueError("Ese movimiento ya está vinculado a otra línea del extracto.")
            mov.origen_tipo, mov.origen_id, mov.estado = tipo, int(oid), "conciliado"
        elif accion == "ignorar":
            mov.estado = "ignorado"
        elif accion == "gasto":
            categoria = s.get(CategoriaGasto, request.form.get("categoria_id", type=int) or 0)
            if categoria is None:
                raise ValueError("Seleccione una categoría.")
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
            mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "gasto", g.id, "conciliado", True
        elif accion == "pago_proveedor":
            g = s.get(Gasto, request.form.get("gasto_id", type=int) or 0)
            if g is None or mov.valor >= 0 or -mov.valor > g.saldo:
                raise ValueError("El valor supera el saldo de la factura del proveedor.")
            p = PagoGasto(fecha=mov.fecha, cuenta_pago=mov.banco.cuenta, valor=-mov.valor)
            s.add(p)
            g.pagos.append(p)
            s.flush()
            contab.contabilizar_pago_gasto(s, p)
            mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "pagogasto", p.id, "conciliado", True
        elif accion == "recaudo":
            if mov.valor <= 0:
                raise ValueError("Solo las entradas de dinero se registran como recaudo.")
            cliente_id = int(request.form["cliente_id"])
            rec = Recaudo(fecha=mov.fecha, cliente_id=cliente_id, banco_id=mov.banco_id, valor=mov.valor,
                          medio_electronico=check("medio_electronico"), referencia=(mov.referencia or mov.descripcion)[:80],
                          notas="Conciliación bancaria")
            s.add(rec)
            sugerencia, _ = cartera.aplicar_automatico(s, cliente_id, mov.valor)
            cartera.registrar_aplicaciones(rec, {doc: v for doc, _, v in sugerencia})
            s.flush()
            contab.contabilizar_recaudo(s, rec)
            mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "recaudo", rec.id, "conciliado", True
        elif accion == "ingreso":
            # Rendimientos, reintegros u otros ingresos sin factura: asiento banco contra la cuenta elegida
            cuenta = request.form.get("cuenta", "421005")
            if mov.valor <= 0:
                raise ValueError("Solo las entradas de dinero se registran como ingreso.")
            cta = s.get(Cuenta, cuenta)
            if cta is None or not cta.movimiento or not cta.activa or cta.codigo[0] not in "234":
                raise ValueError("Cuenta no válida.")
            a = contab.guardar_asiento(s, origen=None, tipo="AJ", fecha=mov.fecha,
                                       descripcion=f"Banco: {mov.descripcion}"[:250], tercero_id=None,
                                       lineas=[(mov.banco.cuenta, mov.valor, 0, None, None), (cuenta, 0, mov.valor, None, None)])
            mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "asiento", a.id, "conciliado", True
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
