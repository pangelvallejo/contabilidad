"""Facturas de venta, recaudos, cartera y certificados de reteIVA."""
from datetime import date, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import archivos, cartera, contab, exportar, importacion
from ..config import TARIFA_IVA, TARIFA_RETEIVA
from ..db import Session
from ..formato import pesos
from ..models import CERO, Asiento, Banco, DocumentoVenta, LineaVenta, Recaudo, Tercero
from . import XLSX, anio_arg, check, descargar, dinero, fecha_arg

bp = Blueprint("ventas", __name__)


def _clientes(s):
    return s.query(Tercero).filter_by(es_cliente=True).order_by(Tercero.nombre).all()


@bp.route("/ventas")
def lista():
    s = Session()
    anio = anio_arg()
    q = s.query(DocumentoVenta).filter(DocumentoVenta.fecha.between(date(anio, 1, 1), date(anio, 12, 31)))
    cliente_id = request.args.get("cliente", type=int)
    if cliente_id:
        q = q.filter(DocumentoVenta.cliente_id == cliente_id)
    docs = q.order_by(DocumentoVenta.fecha.desc(), DocumentoVenta.id.desc()).all()
    filas = [(doc, cartera.saldo_documento(s, doc), cartera.estado_documento(s, doc)) for doc in docs]
    estado = request.args.get("estado")
    if estado:
        filas = [f for f in filas if f[2] == estado]
    if request.args.get("xlsx"):
        enc = ["Tipo", "Número", "Fecha", "Vence", "Cliente", "NIT", "Base", "IVA", "Total", "ReteIVA",
               "Certificado", "Saldo", "Estado", "CUFE"]
        datos = [[d.tipo, d.numero, d.fecha, d.vencimiento, d.cliente.nombre, d.cliente.nit, d.signo * d.ingreso,
                  d.signo * d.iva, d.signo * d.total, d.reteiva_valor if d.reteiva_aplica else 0,
                  "Sí" if d.cert_recibido else ("Pendiente" if d.reteiva_aplica else ""), sal, est, d.cufe or ""]
                 for d, sal, est in filas]
        return descargar(exportar.excel({"Ventas": (enc, datos)}), f"ventas_{anio}.xlsx", XLSX)
    totales = {
        "base": sum((d.signo * d.ingreso for d, _, e in filas if e != "Anulada"), CERO),
        "iva": sum((d.signo * d.iva for d, _, e in filas if e != "Anulada"), CERO),
        "total": sum((d.signo * d.total for d, _, e in filas if e != "Anulada"), CERO),
        "reteiva": sum((d.reteiva_valor for d, _, e in filas if d.reteiva_aplica and e != "Anulada"), CERO),
        "saldo": sum((sal for _, sal, _ in filas), CERO),
    }
    return render_template("ventas/lista.html", filas=filas, totales=totales, anio=anio, clientes=_clientes(s),
                           cliente_id=cliente_id, estado=estado)


@bp.route("/ventas/importar", methods=["GET", "POST"])
def importar():
    resultados = None
    if request.method == "POST":
        s = Session()
        resultados = []
        forzar = request.form.get("forzar") or None
        for f in request.files.getlist("archivos"):
            if f.filename:
                resultados += importacion.importar_archivo(s, f.filename, f.read(), forzar=forzar)
    return render_template("importar.html", resultados=resultados, destino="ventas",
                           titulo="Importar facturas emitidas")


@bp.route("/ventas/nueva", methods=["GET", "POST"])
def nueva():
    s = Session()
    if request.method == "POST":
        try:
            cliente = s.get(Tercero, int(request.form["cliente_id"]))
            base = dinero("base")
            iva = dinero("iva")
            doc = DocumentoVenta(tipo=request.form.get("tipo", "FV"), numero=request.form["numero"].strip(),
                                 cufe=request.form.get("cufe", "").strip() or None,
                                 fecha=fecha_arg("fecha"), cliente=cliente, subtotal=base, base_gravada=base,
                                 iva=iva, total=base + iva, notas=request.form.get("notas") or None)
            doc.vencimiento = fecha_arg("vencimiento") or doc.fecha + timedelta(days=cliente.plazo_dias or 0)
            if doc.tipo == "NC" and request.form.get("referencia_id"):
                doc.referencia_id = int(request.form["referencia_id"])
            doc.lineas.append(LineaVenta(descripcion=request.form.get("descripcion") or "Honorarios", base=base,
                                         iva_pct=contab.d(TARIFA_IVA * 100) if iva else 0, iva=iva))
            if doc.tipo != "NC" and cliente.aplica_reteiva:
                doc.reteiva_aplica = True
                doc.reteiva_valor = contab.redondear(iva * contab.d(TARIFA_RETEIVA), "1")
                doc.reteiva_fecha = doc.fecha
            doc.pdf_archivo = archivos.guardar_upload("ventas", request.files.get("pdf"))
            s.add(doc)
            s.flush()
            contab.contabilizar_venta(s, doc)
            s.commit()
            flash(f"{doc.numero} registrada.", "ok")
            return redirect(url_for("ventas.detalle", id=doc.id))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo registrar: {e}", "error")
    facturas = s.query(DocumentoVenta).filter_by(tipo="FV").order_by(DocumentoVenta.fecha.desc()).all()
    return render_template("ventas/nueva.html", clientes=_clientes(s), facturas=facturas, tarifa_iva=TARIFA_IVA)


@bp.route("/ventas/<int:id>", methods=["GET", "POST"])
def detalle(id):
    s = Session()
    doc = s.get(DocumentoVenta, id) or _404()
    if request.method == "POST":
        accion = request.form.get("accion")
        if accion == "eliminar":
            if doc.aplicaciones:
                flash("No se puede eliminar: tiene recaudos aplicados. Elimine primero los recaudos.", "error")
                return redirect(url_for("ventas.detalle", id=id))
            contab.borrar_asientos(s, f"venta:{doc.id}")
            s.delete(doc)
            s.commit()
            flash("Documento eliminado.", "ok")
            return redirect(url_for("ventas.lista"))
        doc.vencimiento = fecha_arg("vencimiento", doc.vencimiento)
        doc.reteiva_aplica = check("reteiva_aplica")
        doc.reteiva_valor = dinero("reteiva_valor") if doc.reteiva_aplica else CERO
        doc.reteiva_fecha = fecha_arg("reteiva_fecha", doc.fecha) if doc.reteiva_aplica else None
        doc.cert_recibido = check("cert_recibido")
        doc.anulada = check("anulada")
        doc.notas = request.form.get("notas") or None
        cert = archivos.guardar_upload("certificados", request.files.get("cert_archivo"))
        if cert:
            doc.cert_archivo, doc.cert_recibido = cert, True
        pdf = archivos.guardar_upload("ventas", request.files.get("pdf"))
        if pdf:
            doc.pdf_archivo = pdf
        if check("recordar_reteiva"):
            doc.cliente.aplica_reteiva = doc.reteiva_aplica
        contab.contabilizar_venta(s, doc)
        s.commit()
        flash("Cambios guardados.", "ok")
        return redirect(url_for("ventas.detalle", id=id))
    notas = s.query(DocumentoVenta).filter_by(referencia_id=doc.id).all()
    asientos = s.query(Asiento).filter_by(origen=f"venta:{doc.id}").all()
    return render_template("ventas/detalle.html", doc=doc, saldo=cartera.saldo_documento(s, doc),
                           estado=cartera.estado_documento(s, doc), notas=notas, asientos=asientos,
                           reteiva_sugerida=contab.redondear(doc.iva * contab.d(TARIFA_RETEIVA), "1"))


def _404():
    abort(404)


# ------------------------------------------------------------------ recaudos

@bp.route("/recaudos")
def recaudos():
    s = Session()
    anio = anio_arg()
    recs = (s.query(Recaudo).filter(Recaudo.fecha.between(date(anio, 1, 1), date(anio, 12, 31)))
            .order_by(Recaudo.fecha.desc(), Recaudo.id.desc()).all())
    return render_template("ventas/recaudos.html", recaudos=recs, anio=anio,
                           total=sum((r.valor for r in recs), CERO))


@bp.route("/recaudos/nuevo", methods=["GET", "POST"])
def nuevo_recaudo():
    s = Session()
    cliente_id = request.values.get("cliente_id", type=int)
    if request.method == "POST" and request.form.get("guardar"):
        try:
            rec = Recaudo(fecha=fecha_arg("fecha", date.today()), cliente_id=cliente_id,
                          banco_id=int(request.form["banco_id"]), valor=dinero("valor"),
                          medio_electronico=check("medio_electronico"),
                          referencia=request.form.get("referencia") or None, notas=request.form.get("notas") or None)
            if rec.valor <= 0:
                raise ValueError("El valor recibido debe ser mayor que cero.")
            rec.soporte_archivo = archivos.guardar_upload("recaudos", request.files.get("soporte"))
            s.add(rec)
            valores = {}
            for doc, saldo in cartera.documentos_abiertos(s, cliente_id):
                v = dinero(f"aplicar_{doc.id}")
                if v > saldo:
                    raise ValueError(f"El abono a {doc.numero} supera su saldo.")
                valores[doc] = v
            cartera.registrar_aplicaciones(rec, valores)
            if rec.aplicado > rec.valor:
                raise ValueError("Lo aplicado a facturas supera el valor recibido.")
            s.flush()
            contab.contabilizar_recaudo(s, rec)
            s.commit()
            flash("Recaudo registrado." + (f" Quedó un anticipo sin aplicar de {pesos(rec.sin_aplicar)}."
                                           if rec.sin_aplicar > 0 else ""), "ok")
            return redirect(url_for("ventas.recaudos"))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo registrar: {e}", "error")
    abiertos = cartera.documentos_abiertos(s, cliente_id) if cliente_id else []
    return render_template("ventas/recaudo_nuevo.html", clientes=_clientes(s), cliente_id=cliente_id,
                           abiertos=abiertos, bancos=s.query(Banco).filter_by(activo=True).all(),
                           form=request.form)


@bp.route("/recaudos/<int:id>/eliminar", methods=["POST"])
def eliminar_recaudo(id):
    s = Session()
    rec = s.get(Recaudo, id) or _404()
    contab.borrar_asientos(s, f"recaudo:{rec.id}")
    s.delete(rec)
    s.commit()
    flash("Recaudo eliminado.", "ok")
    return redirect(url_for("ventas.recaudos"))


# ------------------------------------------------------------------ cartera

@bp.route("/cartera")
def cartera_edades():
    s = Session()
    corte = fecha_arg("corte", date.today())
    filas, totales = cartera.cartera_por_edades(s, corte)
    if request.args.get("xlsx"):
        enc = ["Cliente", "NIT"] + [e[0] for e in cartera.EDADES] + ["Total"]
        datos = [[f["cliente"].nombre, f["cliente"].nit] + [f[e[0]] for e in cartera.EDADES] + [f["total"]]
                 for f in filas]
        return descargar(exportar.excel({"Cartera": (enc, datos)}), f"cartera_{corte}.xlsx", XLSX)
    return render_template("ventas/cartera.html", filas=filas, totales=totales, edades=cartera.EDADES, corte=corte)


def _estado_cuenta(s, cliente, corte):
    filas = []
    for doc, saldo in cartera.documentos_abiertos(s, cliente.id, al=corte):
        rete = doc.reteiva_valor if doc.reteiva_aplica else CERO
        filas.append({"numero": doc.numero, "fecha": doc.fecha, "vencimiento": doc.vencimiento, "total": doc.total,
                      "reteiva": rete, "abonos": doc.total - rete - saldo, "saldo": saldo,
                      "dias": (corte - (doc.vencimiento or doc.fecha)).days, "doc": doc})
    return filas, sum((f["saldo"] for f in filas), CERO)


@bp.route("/cartera/<int:cliente_id>")
def estado_cuenta(cliente_id):
    s = Session()
    cliente = s.get(Tercero, cliente_id) or _404()
    corte = fecha_arg("corte", date.today())
    filas, total = _estado_cuenta(s, cliente, corte)
    anticipo = cartera.anticipos_cliente(s, cliente_id)
    if request.args.get("pdf"):
        emp = {k: contab.config(s, f"empresa_{k}", "") for k in ("nombre", "direccion", "ciudad", "email",
                                                                   "telefono")}
        emp["nit"] = f"{contab.config(s, 'empresa_nit', '')}-{contab.config(s, 'empresa_dv', '')}"
        pdf = exportar.estado_de_cuenta_pdf(emp, cliente, filas, total, corte)
        nombre = f"estado_cuenta_{archivos.nombre_seguro(cliente.nombre)}_{corte}.pdf"
        return descargar(pdf, nombre, "application/pdf")
    return render_template("ventas/estado_cuenta.html", cliente=cliente, filas=filas, total=total, corte=corte,
                           anticipo=anticipo)


@bp.route("/certificados")
def certificados():
    s = Session()
    docs = (s.query(DocumentoVenta).filter(DocumentoVenta.reteiva_aplica.is_(True), DocumentoVenta.anulada.is_(False))
            .order_by(DocumentoVenta.cert_recibido, DocumentoVenta.fecha).all())
    pendientes = [d for d in docs if not d.cert_recibido]
    return render_template("ventas/certificados.html", docs=docs,
                           total_pendiente=sum((d.reteiva_valor for d in pendientes), CERO),
                           n_pendientes=len(pendientes))
