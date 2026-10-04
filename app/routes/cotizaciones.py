"""Cotizaciones (propuestas de honorarios): se preparan, se envían y, al aceptarse, se facturan en la DIAN."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import contab, correo, exportar
from ..db import Session
from ..models import Asunto, Cotizacion, DocumentoVenta, LineaCotizacion, Tercero
from . import anio_arg, descargar, entero_requerido, fecha_arg

bp = Blueprint("cotizaciones", __name__, url_prefix="/cotizaciones")

ESTADOS = {"borrador": "Borrador", "enviada": "Enviada", "aceptada": "Aceptada", "rechazada": "Rechazada",
           "facturada": "Facturada"}
CONDICIONES_DEFECTO = ("Forma de pago: 50 % al aceptar la propuesta y 50 % a la entrega.\n"
                       "Los gastos de notaría, certificados, viajes y similares se cobran por separado contra soporte.\n"
                       "Esta propuesta no incluye actuaciones no descritas; cualquier trabajo adicional se cotiza aparte.")


def _siguiente_numero(s):
    ultimo = s.query(Cotizacion).order_by(Cotizacion.id.desc()).first()
    n = 1
    if ultimo and ultimo.numero.startswith("COT-"):
        try:
            n = int(ultimo.numero.split("-")[1]) + 1
        except ValueError:
            n = ultimo.id + 1
    return f"COT-{n:04d}"


@bp.route("/")
def lista():
    s = Session()
    anio = anio_arg()
    estado = request.args.get("estado", "")
    q = s.query(Cotizacion).filter(Cotizacion.fecha.between(date(anio, 1, 1), date(anio, 12, 31)))
    if estado:
        q = q.filter(Cotizacion.estado == estado)
    from sqlalchemy.orm import selectinload
    q = q.options(selectinload(Cotizacion.cliente), selectinload(Cotizacion.lineas))
    cots = q.order_by(Cotizacion.fecha.desc(), Cotizacion.id.desc()).all()
    resumen = {}
    for c in (s.query(Cotizacion).options(selectinload(Cotizacion.lineas))
              .filter(Cotizacion.fecha.between(date(anio, 1, 1), date(anio, 12, 31)))):
        r = resumen.setdefault(c.estado, [0, contab.CERO])
        r[0] += 1
        r[1] += c.subtotal
    return render_template("cotizaciones/lista.html", cots=cots, anio=anio, estado=estado, estados=ESTADOS,
                           resumen=resumen, hoy=date.today())


def _llenar(s, cot):
    cot.fecha = fecha_arg("fecha", date.today())
    cot.cliente_id = entero_requerido("cliente_id", "Seleccione el cliente.")
    if s.get(Tercero, cot.cliente_id) is None:
        raise ValueError("Seleccione el cliente.")
    cot.asunto_id = request.form.get("asunto_id", type=int) or None
    if cot.asunto_id:
        a = s.get(Asunto, cot.asunto_id)
        if a is None or a.cliente_id != cot.cliente_id:
            raise ValueError("El asunto debe ser del mismo cliente.")
    cot.titulo = request.form.get("titulo", "").strip()
    if not cot.titulo:
        raise ValueError("Indique el título de la propuesta.")
    validez = request.form.get("validez_dias", "").strip()
    cot.validez_dias = int(validez) if validez.isdigit() else 30
    if not 1 <= cot.validez_dias <= 365:
        raise ValueError("La validez debe estar entre 1 y 365 días.")
    cot.condiciones = request.form.get("condiciones", "").strip() or None
    cot.notas = request.form.get("notas", "").strip() or None
    descripciones = request.form.getlist("descripcion")
    valores = request.form.getlist("valor")
    ivas = request.form.getlist("iva_pct")
    cot.lineas.clear()
    for i, desc in enumerate(descripciones):
        desc = desc.strip()
        try:
            valor = contab.d(valores[i] if i < len(valores) else 0)
            iva_pct = contab.d(ivas[i] if i < len(ivas) and ivas[i].strip() != "" else 19)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"El valor o el IVA de la línea {i + 1} no es un número válido.") from e
        if not desc and not valor:
            continue
        if not desc:
            raise ValueError(f"La línea {i + 1} no tiene descripción.")
        if valor <= 0:
            raise ValueError(f"El valor de la línea {i + 1} debe ser mayor que cero.")
        if iva_pct not in (0, 5, 19):
            raise ValueError(f"El IVA de la línea {i + 1} debe ser 0, 5 o 19 %.")
        cot.lineas.append(LineaCotizacion(descripcion=desc, valor=valor, iva_pct=iva_pct))
    if not cot.lineas:
        raise ValueError("Agregue al menos una línea con honorarios.")


@bp.route("/nueva", methods=["GET", "POST"])
@bp.route("/<int:id>", methods=["GET", "POST"])
def editar(id=None):
    s = Session()
    cot = s.get(Cotizacion, id) if id else None
    if id and cot is None:
        abort(404)
    if request.method == "POST":
        accion = request.form.get("accion", "guardar")
        try:
            if cot is None and accion != "guardar":
                raise ValueError("Primero guarde la cotización.")
            if accion == "borrar":
                s.delete(cot)
                s.commit()
                flash("Cotización eliminada.", "ok")
                return redirect(url_for("cotizaciones.lista"))
            if accion in ESTADOS:
                cot.estado = accion
                if accion == "facturada":
                    fid = request.form.get("factura_id", type=int)
                    factura = s.get(DocumentoVenta, fid) if fid else None
                    if fid and (factura is None or factura.cliente_id != cot.cliente_id or factura.tipo != "FV"):
                        raise ValueError("La factura debe ser una factura de venta del mismo cliente.")
                    cot.factura_id = fid
                    if cot.asunto_id and factura is not None and not factura.asunto_id:
                        factura.asunto_id = cot.asunto_id
                s.commit()
                flash(f"Cotización marcada como {ESTADOS[accion].lower()}.", "ok")
                return redirect(url_for("cotizaciones.editar", id=cot.id))
            if accion == "enviar":
                if not cot.cliente.email:
                    raise ValueError("El cliente no tiene correo registrado. Agréguelo en Clientes y proveedores.")
                pdf = exportar.cotizacion_pdf(exportar._empresa_dict(s), cot)
                emp = contab.config(s, "empresa_nombre", "")
                from ..formato import pesos
                cuerpo = (f"Estimados señores {cot.cliente.nombre}:\n\nAdjunto la propuesta de honorarios {cot.numero}: "
                          f"{cot.titulo}, por {pesos(cot.total)} (IVA incluido), válida hasta el "
                          f"{cot.vence:%d/%m/%Y}.\n\nQuedo atento a sus comentarios.\n\nCordialmente,\n{emp}")
                correo.enviar(s, cot.cliente.email, f"Propuesta de honorarios {cot.numero} · {emp}", cuerpo,
                              [(f"{cot.numero}.pdf", pdf, "application/pdf")])
                from datetime import datetime
                cot.estado, cot.enviada_el = "enviada", datetime.now()
                s.commit()
                flash(f"Propuesta enviada a {cot.cliente.email}.", "ok")
                return redirect(url_for("cotizaciones.editar", id=cot.id))
            nueva = cot is None
            if cot is not None and cot.estado != "borrador":
                raise ValueError("La cotización ya fue enviada; pulse \"Reabrir\" para editarla.")
            cot = cot or Cotizacion(numero=_siguiente_numero(s), estado="borrador")
            _llenar(s, cot)
            s.add(cot)
            s.commit()
            flash("Cotización guardada." if not nueva else f"Cotización {cot.numero} creada.", "ok")
            return redirect(url_for("cotizaciones.editar", id=cot.id))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo completar: {e}", "error")
            cot = s.get(Cotizacion, id) if id else None
    clientes = s.query(Tercero).filter(Tercero.es_cliente.is_(True)).order_by(Tercero.nombre).all()
    asuntos = s.query(Asunto).filter_by(estado="abierto").order_by(Asunto.nombre).all()
    facturas = []
    if cot:
        facturas = (s.query(DocumentoVenta).filter_by(cliente_id=cot.cliente_id, tipo="FV")
                    .order_by(DocumentoVenta.fecha.desc()).limit(30).all())
    return render_template("cotizaciones/form.html", cot=cot, clientes=clientes, asuntos=asuntos, estados=ESTADOS,
                           form=request.form, hoy=date.today(), condiciones_defecto=CONDICIONES_DEFECTO,
                           facturas=facturas, correo_activo=correo.configuracion(s)["activo"])


@bp.route("/<int:id>/pdf")
def pdf(id):
    s = Session()
    cot = s.get(Cotizacion, id) or abort(404)
    return descargar(exportar.cotizacion_pdf(exportar._empresa_dict(s), cot), f"{cot.numero}.pdf", "application/pdf")
