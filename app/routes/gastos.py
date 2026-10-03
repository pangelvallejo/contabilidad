"""Gastos, IVA descontable y cuentas por pagar."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import archivos, bancos, contab, exportar, importacion, planeacion
from sqlalchemy.orm import selectinload

from ..db import Session
from ..models import (CERO, TIPOS_SOPORTE, Asiento, Asunto, Banco, CategoriaGasto, Gasto, PagoGasto,
                      Tercero)
from . import XLSX, anio_arg, check, descargar, dinero, entero_requerido, fecha_arg, paginar_lista

bp = Blueprint("gastos", __name__)


def opciones_pago(s):
    """Cuentas desde las que se puede pagar un gasto: bancos, caja y reembolso al socio."""
    ops = [(b.cuenta, b.nombre) for b in s.query(Banco).filter_by(activo=True).order_by(Banco.id)]
    ops += [(contab.CTA_CAJA, "Caja (efectivo)"), (contab.CTA_SOCIOS, "Pagado por el socio (reembolsable)")]
    return ops


def _contexto_form(s):
    return {"categorias": s.query(CategoriaGasto).filter_by(activa=True).order_by(CategoriaGasto.nombre).all(),
            "proveedores": s.query(Tercero).filter_by(es_proveedor=True).order_by(Tercero.nombre).all(),
            "opciones_pago": opciones_pago(s), "tipos": TIPOS_SOPORTE,
            "facturas_proveedor": s.query(Gasto).filter(Gasto.tipo_soporte != "NC", Gasto.proveedor_id.isnot(None))
            .order_by(Gasto.fecha.desc()).limit(300).all(),
            "asuntos": s.query(Asunto).filter_by(estado="abierto").order_by(Asunto.nombre).all()}


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
    gastos = (q.options(selectinload(Gasto.proveedor), selectinload(Gasto.categoria), selectinload(Gasto.pagos),
                        selectinload(Gasto.notas_credito))
              .order_by(Gasto.fecha.desc(), Gasto.id.desc()).all())
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
    gastos, pagina, paginas = paginar_lista(gastos)
    return render_template("gastos/lista.html", gastos=gastos, totales=totales, anio=anio, pagina=pagina, paginas=paginas,
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
    if g.tipo_soporte not in TIPOS_SOPORTE:
        raise ValueError("Tipo de soporte no válido.")
    g.numero = request.form.get("numero", "").strip() or None
    g.cufe = request.form.get("cufe", "").strip() or None
    g.fecha = fecha_arg("fecha", date.today())
    g.vencimiento = fecha_arg("vencimiento")
    g.proveedor_id = _proveedor_desde_form(s)
    g.categoria_id = entero_requerido("categoria_id", "Seleccione la categoría del gasto.")
    if s.get(CategoriaGasto, g.categoria_id) is None:
        raise ValueError("La categoría indicada no existe.")
    g.descripcion = request.form.get("descripcion") or None
    g.subtotal = dinero("subtotal")
    g.iva = dinero("iva")
    g.iva_descontable = check("iva_descontable")
    g.otros_impuestos = dinero("otros_impuestos")
    if g.subtotal < 0 or g.iva < 0 or g.otros_impuestos < 0:
        raise ValueError("Los valores no pueden ser negativos (una nota crédito se registra con su tipo de soporte).")
    g.total = g.subtotal + g.iva + g.otros_impuestos
    g.forma_pago = request.form.get("forma_pago", "contado")
    if g.forma_pago not in ("contado", "credito"):
        raise ValueError("Forma de pago no válida.")
    if g.tipo_soporte == "NC":
        ref = s.get(Gasto, request.form.get("referencia_id", type=int) or 0)
        g.referencia = ref if ref is not None and ref.tipo_soporte != "NC" else None
    g.cuenta_pago = request.form.get("cuenta_pago") if g.forma_pago == "contado" else None
    g.notas = request.form.get("notas") or None
    g.recurrente = check("recurrente")
    g.asunto_id = request.form.get("asunto_id", type=int) or None
    if g.asunto_id and s.get(Asunto, g.asunto_id) is None:
        raise ValueError("El asunto indicado no existe.")
    g.reembolsable = check("reembolsable") and bool(g.asunto_id)
    if not g.reembolsable:
        g.reembolsado = False
    g.vida_util_meses = request.form.get("vida_util_meses", type=int) or None
    g.revisado = True
    if g.iva and g.iva_descontable and g.tipo_soporte not in ("FE", "DS", "NC"):
        # Art. 771-2 E.T.: sin factura electrónica o documento soporte no hay derecho al descontable.
        g.iva_descontable = False
        flash("El IVA se registró como NO descontable: solo es descontable con factura electrónica o "
              "documento soporte. Cambie el tipo de soporte si tiene la factura.", "advertencia")
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
    form = request.form
    copiar = request.args.get("copiar", type=int)
    if request.method == "GET" and copiar:
        base = s.get(Gasto, copiar)
        if base is not None:
            # Duplicar: mismos datos, fecha de hoy, sin número ni CUFE (son del documento original).
            from ..formato import entrada
            form = {"tipo_soporte": base.tipo_soporte, "proveedor_id": str(base.proveedor_id or ""),
                    "categoria_id": str(base.categoria_id), "descripcion": base.descripcion or "",
                    "subtotal": entrada(base.subtotal), "iva": entrada(base.iva), "otros_impuestos": entrada(base.otros_impuestos),
                    "iva_descontable": "on" if base.iva_descontable else "", "forma_pago": base.forma_pago,
                    "cuenta_pago": base.cuenta_pago or "", "recurrente": "on" if base.recurrente else "",
                    "fecha": date.today().isoformat(), "notas": ""}
    return render_template("gastos/form.html", g=None, form=form, **_contexto_form(s))


@bp.route("/gastos/revisar", methods=["GET", "POST"])
def revisar():
    """Corrige en una sola pantalla la categoría, el IVA descontable y la forma de pago de los gastos importados."""
    s = Session()
    if request.method == "POST":
        ids = request.form.getlist("id")
        n = 0
        for gid in ids:
            g = s.get(Gasto, int(gid)) if str(gid).isdigit() else None
            if g is None or not request.form.get(f"ok_{gid}"):
                continue
            try:
                categoria = s.get(CategoriaGasto, int(request.form[f"categoria_{gid}"]))
                if categoria is None:
                    raise ValueError("Categoría no válida.")
                if g.categoria.cuenta.startswith("15") and not categoria.cuenta.startswith("15"):
                    planeacion.borrar_depreciaciones(s, g.id)
                g.categoria_id = categoria.id
                g.iva_descontable = request.form.get(f"iva_{gid}") == "on" and g.tipo_soporte in ("FE", "DS", "NC")
                forma = request.form.get(f"forma_{gid}", g.forma_pago)
                if forma not in ("contado", "credito"):
                    raise ValueError("forma de pago no válida")
                if forma == "contado" and g.pagos:
                    raise ValueError("tiene pagos registrados; elimínelos antes de marcarlo de contado")
                g.forma_pago = forma
                g.cuenta_pago = request.form.get(f"cuenta_{gid}") if g.forma_pago == "contado" else None
                g.recurrente = request.form.get(f"rec_{gid}") == "on"
                g.revisado = True
                s.flush()
                s.refresh(g)
                contab.contabilizar_gasto(s, g)
                n += 1
            except Exception as e:  # noqa: BLE001
                s.rollback()
                flash(f"No se pudo guardar el gasto {g.numero or gid}: {e}", "error")
                return redirect(url_for("gastos.revisar"))
        s.commit()
        flash(f"{n} gasto(s) revisado(s).", "ok")
        return redirect(url_for("gastos.revisar") if s.query(Gasto).filter_by(revisado=False).count()
                        else url_for("gastos.lista"))
    pendientes = s.query(Gasto).filter_by(revisado=False).order_by(Gasto.fecha.desc()).limit(200).all()
    return render_template("gastos/revisar.html", gastos=pendientes, **_contexto_form(s))


@bp.route("/gastos/<int:id>", methods=["GET", "POST"])
def detalle(id):
    s = Session()
    g = s.get(Gasto, id) or abort(404)
    if request.method == "POST":
        accion = request.form.get("accion")
        try:
            if accion == "eliminar":
                if g.notas_credito:
                    raise ValueError("Tiene notas crédito asociadas; elimínelas primero.")
                planeacion.borrar_depreciaciones(s, g.id)
                bancos.liberar(s, "gasto", g.id)
                for p in g.pagos:
                    bancos.liberar(s, "pagogasto", p.id)
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
                p = s.query(PagoGasto).filter_by(id=request.form.get("pago_id", type=int) or 0, gasto_id=g.id).first()
                if p is None:
                    raise ValueError("El pago ya no existe.")
                bancos.liberar(s, "pagogasto", p.id)
                contab.borrar_asientos(s, f"pagogasto:{p.id}")
                s.delete(p)
                s.commit()
                return redirect(url_for("gastos.detalle", id=id))
            categoria_anterior = g.categoria.cuenta
            _llenar_gasto(s, g)
            if categoria_anterior.startswith("15") and not s.get(CategoriaGasto, g.categoria_id).cuenta.startswith("15"):
                planeacion.borrar_depreciaciones(s, g.id)  # dejó de ser activo
            if g.forma_pago == "contado" and g.pagos:
                raise ValueError("El gasto tiene pagos registrados; elimínelos antes de marcarlo de contado.")
            if g.forma_pago == "credito" and g.pagos and g.total < sum((p.valor for p in g.pagos), CERO):
                raise ValueError("El total no puede ser menor que lo ya pagado; ajuste primero los pagos.")
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
