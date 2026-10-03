"""Extractos bancarios y conciliación."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import archivos, bancos, cartera, contab, reportes
from ..db import Session
from ..models import (CERO, Banco, CategoriaGasto, Cuenta, Gasto, MovimientoBanco, OtroIngreso, PagoGasto, Recaudo,
                      Tercero)
from . import check, entero_requerido, fecha_arg

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
    usados = bancos._ya_conciliados(s)
    sugerencias = {m.id: bancos.candidatos(s, m, usados=usados) for m in movs if m.estado == "pendiente"}
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
            flash("Movimiento devuelto a pendiente.", "ok")
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
            flash("Movimiento vinculado.", "ok")
        elif accion == "ignorar":
            mov.estado = "ignorado"
            flash("Movimiento ignorado.", "ok")
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
            flash(f"Gasto registrado en {categoria.nombre} y conciliado.", "ok")
        elif accion == "pago_proveedor":
            g = s.get(Gasto, request.form.get("gasto_id", type=int) or 0)
            if g is None:
                raise ValueError("Elija el proveedor y luego la factura que se pagó con este movimiento.")
            if mov.valor >= 0 or -mov.valor > g.saldo:
                raise ValueError("El valor supera el saldo de la factura del proveedor.")
            p = PagoGasto(fecha=mov.fecha, cuenta_pago=mov.banco.cuenta, valor=-mov.valor)
            s.add(p)
            g.pagos.append(p)
            s.flush()
            contab.contabilizar_pago_gasto(s, p)
            mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "pagogasto", p.id, "conciliado", True
            from ..formato import pesos
            flash(f"Pago aplicado a {g.numero or 'la factura'}; saldo pendiente {pesos(g.saldo)}.", "ok")
        elif accion == "recaudo":
            if mov.valor <= 0:
                raise ValueError("Solo las entradas de dinero se registran como recaudo.")
            cliente_id = entero_requerido("cliente_id", "Seleccione el cliente que hizo el pago.")
            if s.get(Tercero, cliente_id) is None:
                raise ValueError("Seleccione el cliente que hizo el pago.")
            rec = Recaudo(fecha=mov.fecha, cliente_id=cliente_id, banco_id=mov.banco_id, valor=mov.valor,
                          medio_electronico=check("medio_electronico"), referencia=(mov.referencia or mov.descripcion)[:80],
                          notas="Conciliación bancaria")
            s.add(rec)
            sugerencia, _ = cartera.aplicar_automatico(s, cliente_id, mov.valor)
            cartera.registrar_aplicaciones(rec, {doc: v for doc, _, v in sugerencia})
            s.flush()
            contab.contabilizar_recaudo(s, rec)
            mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "recaudo", rec.id, "conciliado", True
            flash("Recaudo registrado y aplicado a las facturas más antiguas." if rec.aplicaciones
                  else "Recaudo registrado como anticipo (el cliente no tiene facturas pendientes).", "ok")
        elif accion == "ingreso":
            # Rendimientos, reintegros u otros ingresos sin factura: asiento banco contra la cuenta elegida
            cuenta = request.form.get("cuenta", "421005")
            if mov.valor <= 0:
                raise ValueError("Solo las entradas de dinero se registran como ingreso.")
            cta = s.get(Cuenta, cuenta)
            if cta is None or not cta.movimiento or not cta.activa or cta.codigo[0] not in "234":
                raise ValueError("Cuenta no válida.")
            if cta.codigo.startswith("4"):
                oi = OtroIngreso(fecha=mov.fecha, banco_id=mov.banco_id, cuenta=cuenta, valor=mov.valor,
                                 descuentos=CERO, concepto=(mov.descripcion or cta.nombre)[:160],
                                 referencia=(mov.referencia or "")[:80] or None, notas="Conciliación bancaria")
                s.add(oi)
                s.flush()
                s.refresh(oi)
                contab.contabilizar_otro_ingreso(s, oi)
                mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "otroingreso", oi.id, "conciliado", True
                flash(f"Ingreso registrado en {cta.nombre}. Puede completar el tercero en Ingresos sin factura.", "ok")
            else:
                a = contab.guardar_asiento(s, origen=None, tipo="AJ", fecha=mov.fecha,
                                           descripcion=f"Banco: {mov.descripcion}"[:250], tercero_id=None,
                                           lineas=[(mov.banco.cuenta, mov.valor, 0, None, None), (cuenta, 0, mov.valor, None, None)])
                mov.origen_tipo, mov.origen_id, mov.estado, mov.creado_aqui = "asiento", a.id, "conciliado", True
                flash("Movimiento registrado.", "ok")
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


# ------------------------------------------------------------------ saldos y otros ingresos

def _cuentas_ingreso(s):
    return (s.query(Cuenta).filter(Cuenta.movimiento.is_(True), Cuenta.activa.is_(True), Cuenta.codigo.like("4%"),
                                   ~Cuenta.codigo.like("4135%"), ~Cuenta.codigo.like("4155%"),
                                   ~Cuenta.codigo.like("4175%")).order_by(Cuenta.codigo).all())


@bp.route("/")
def inicio():
    """Saldo de cada banco, lo pendiente por conciliar y los últimos ingresos sin factura."""
    s = Session()
    corte = fecha_arg("corte", date.today())
    filas, total = bancos.saldos_bancos(s, corte)
    anio = corte.year
    ingresos = (s.query(OtroIngreso).filter(OtroIngreso.fecha.between(date(anio, 1, 1), corte))
                .order_by(OtroIngreso.fecha.desc(), OtroIngreso.id.desc()).all())
    por_cuenta = {}
    for oi in ingresos:
        por_cuenta[oi.cuenta_rel.nombre] = por_cuenta.get(oi.cuenta_rel.nombre, CERO) + oi.valor
    return render_template("bancos/inicio.html", filas=filas, total=total, corte=corte, ingresos=ingresos[:10],
                           total_ingresos=sum((oi.valor for oi in ingresos), CERO), por_cuenta=sorted(por_cuenta.items()),
                           caja=reportes.saldo_cuenta(s, contab.CTA_CAJA, corte))


@bp.route("/movimientos")
def movimientos():
    """Movimientos contables de un banco con saldo acumulado (libro auxiliar de la cuenta)."""
    s = Session()
    banco = _banco(s)
    hoy = date.today()
    desde = fecha_arg("desde", hoy.replace(day=1))
    hasta = fecha_arg("hasta", hoy)
    _, inicial, filas, final = reportes.libro_mayor(s, banco.cuenta, desde, hasta)
    if request.args.get("xlsx"):
        from .. import exportar
        from . import XLSX, descargar
        enc = ["Fecha", "Comprobante", "Descripción", "Tercero", "Entradas", "Salidas", "Saldo"]
        datos = [[m.asiento.fecha, f"{m.asiento.tipo}-{m.asiento.numero}", m.descripcion or m.asiento.descripcion,
                  m.tercero.nombre if m.tercero else "", m.debito, m.credito, saldo] for m, saldo in filas]
        return descargar(exportar.excel({f"{banco.nombre[:25]}": (enc, [[desde, "", "Saldo inicial", "", 0, 0, inicial]]
                                                                   + datos)}),
                         f"banco_{banco.nombre}_{desde}_{hasta}.xlsx", XLSX)
    return render_template("bancos/movimientos.html", banco=banco, desde=desde, hasta=hasta, inicial=inicial,
                           filas=filas, final=final, bancos=s.query(Banco).filter_by(activo=True).all(),
                           entradas=sum((m.debito for m, _ in filas), CERO), salidas=sum((m.credito for m, _ in filas), CERO))


@bp.route("/ingresos")
def otros_ingresos():
    s = Session()
    from . import anio_arg, paginar_lista
    anio = anio_arg()
    q = s.query(OtroIngreso).filter(OtroIngreso.fecha.between(date(anio, 1, 1), date(anio, 12, 31)))
    banco_id = request.args.get("banco", type=int)
    if banco_id:
        q = q.filter(OtroIngreso.banco_id == banco_id)
    todos = q.order_by(OtroIngreso.fecha.desc(), OtroIngreso.id.desc()).all()
    if request.args.get("xlsx"):
        from .. import exportar
        from . import XLSX, descargar
        enc = ["Fecha", "Banco", "Concepto", "Cuenta", "Tercero", "NIT", "Valor bruto", "Descuentos", "Neto al banco",
               "Referencia"]
        datos = [[o.fecha, o.banco.nombre, o.concepto, f"{o.cuenta} {o.cuenta_rel.nombre}",
                  o.tercero.nombre if o.tercero else "", o.tercero.nit if o.tercero else "", o.valor, o.descuentos,
                  o.neto, o.referencia or ""] for o in todos]
        return descargar(exportar.excel({"Otros ingresos": (enc, datos)}), f"otros_ingresos_{anio}.xlsx", XLSX)
    pagina, num, paginas = paginar_lista(todos)
    return render_template("bancos/ingresos.html", ingresos=pagina, pagina=num, paginas=paginas, anio=anio,
                           total=sum((o.valor for o in todos), CERO), total_neto=sum((o.neto for o in todos), CERO),
                           bancos=s.query(Banco).order_by(Banco.id).all(), banco_id=banco_id)


def _tercero_desde_form(s):
    tid = request.form.get("tercero_id", "")
    if tid == "nuevo":
        nit = request.form.get("nuevo_nit", "").strip()
        nombre = request.form.get("nuevo_nombre", "").strip()
        if not nit or not nombre:
            raise ValueError("Para crear el tercero indique NIT y nombre.")
        t = s.query(Tercero).filter_by(nit=nit).one_or_none()
        if t is None:
            t = Tercero(nit=nit, nombre=nombre, tipo_doc="31")
            s.add(t)
            s.flush()
        return t.id
    return int(tid) if tid.isdigit() else None


def _llenar_otro_ingreso(s, oi):
    from . import dinero
    oi.fecha = fecha_arg("fecha", date.today())
    oi.banco_id = entero_requerido("banco_id", "Seleccione el banco donde entró el dinero.")
    if s.get(Banco, oi.banco_id) is None:
        raise ValueError("Seleccione el banco donde entró el dinero.")
    oi.cuenta = request.form.get("cuenta", "421005")
    cta = s.get(Cuenta, oi.cuenta)
    if cta is None or not cta.movimiento or not cta.codigo.startswith("4"):
        raise ValueError("La cuenta de ingreso no es válida.")
    oi.concepto = request.form.get("concepto", "").strip() or cta.nombre
    oi.valor = dinero("valor")
    oi.descuentos = dinero("descuentos")
    if oi.valor <= 0:
        raise ValueError("El valor del ingreso debe ser mayor que cero.")
    if oi.descuentos < 0 or oi.descuentos >= oi.valor:
        raise ValueError("Los descuentos del banco deben ser menores que el ingreso.")
    oi.tercero_id = _tercero_desde_form(s)
    oi.referencia = request.form.get("referencia", "").strip() or None
    oi.notas = request.form.get("notas", "").strip() or None


@bp.route("/ingresos/nuevo", methods=["GET", "POST"])
@bp.route("/ingresos/<int:id>", methods=["GET", "POST"])
def otro_ingreso(id=None):
    s = Session()
    oi = s.get(OtroIngreso, id) if id else None
    if id and oi is None:
        abort(404)
    if request.method == "POST":
        try:
            if request.form.get("accion") == "borrar":
                bancos.liberar(s, "otroingreso", oi.id)
                contab.borrar_asientos(s, f"otroingreso:{oi.id}")
                s.delete(oi)
                s.commit()
                flash("Ingreso eliminado.", "ok")
                return redirect(url_for("bancos.otros_ingresos"))
            nuevo = oi is None
            oi = oi or OtroIngreso()
            _llenar_otro_ingreso(s, oi)
            s.add(oi)
            s.flush()
            s.refresh(oi)
            contab.contabilizar_otro_ingreso(s, oi)
            soporte = archivos.guardar_upload("otros_ingresos", request.files.get("soporte"))
            if soporte:
                oi.soporte_archivo = soporte
            s.commit()
            from ..formato import pesos
            flash(("Ingreso registrado" if nuevo else "Ingreso actualizado") + f": {oi.concepto}, {pesos(oi.valor)}.", "ok")
            return redirect(url_for("bancos.otros_ingresos", anio=oi.fecha.year))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {e}", "error")
            oi = s.get(OtroIngreso, id) if id else None
    return render_template("bancos/ingreso_form.html", oi=oi, form=request.form,
                           bancos=s.query(Banco).filter_by(activo=True).order_by(Banco.id).all(),
                           cuentas=_cuentas_ingreso(s), terceros=s.query(Tercero).order_by(Tercero.nombre).all(),
                           hoy=date.today())
