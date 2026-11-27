"""Cuentas en participación: ingresos certificados por el socio gestor, reconocidos sin factura de venta.

En el contrato de cuentas en participación (arts. 507 a 514 del Código de Comercio) el socio gestor factura al
cliente a su nombre y certifica a cada partícipe la parte de los ingresos que le corresponde. El partícipe reconoce
ese ingreso con la certificación (art. 18 del Estatuto Tributario), sin emitir factura y sin generar IVA (el IVA lo
factura y lo declara el gestor), pero el ingreso sí hace parte de los ingresos brutos del régimen SIMPLE.
"""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from .. import archivos, bancos, contab
from ..contab import d
from ..db import Session
from ..models import (CERO, Banco, CertificacionParticipacion, ContratoParticipacion, MovimientoBanco, PagoParticipacion,
                      Tercero)
from . import anio_arg, dinero, entero_requerido, explicar, fecha_arg

bp = Blueprint("participacion", __name__, url_prefix="/participacion")

CIEN = d("100")


def _porcentaje(nombre):
    texto = request.form.get(nombre, "").replace("%", "").strip()
    if not texto:
        return None
    try:
        from decimal import Decimal
        p = Decimal(texto.replace(" ", "").replace(",", "."))  # un porcentaje nunca lleva separador de miles
        if not p.is_finite():
            raise ValueError(texto)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"El porcentaje '{texto}' no es un número válido (por ejemplo 30 o 33,33).") from e
    if p <= 0 or p > CIEN:
        raise ValueError("El porcentaje de participación debe ser mayor que 0 y máximo 100.")
    return p.quantize(d("0.0001"))


def _fecha_obligatoria(nombre, mensaje):
    f = fecha_arg(nombre)
    if f is None:
        raise ValueError(mensaje)
    return f


def _fecha_opcional(nombre):
    if request.form.get(nombre) and fecha_arg(nombre) is None:
        raise ValueError("Una de las fechas no es válida; use el selector de fecha.")
    return fecha_arg(nombre)


def _conciliado(s, pago_id):
    return s.query(MovimientoBanco).filter_by(origen_tipo="pagoparticipacion", origen_id=pago_id,
                                              estado="conciliado").first() is not None


def resumen(s, desde, hasta):
    """Totales del periodo: reconocido (certificado), cobrado (transferido) y saldo por cobrar a la fecha final."""
    certs = (s.query(CertificacionParticipacion).options(selectinload(CertificacionParticipacion.pagos))
             .filter(CertificacionParticipacion.fecha.between(desde, hasta)).all())
    reconocido = sum((c.valor for c in certs), CERO)
    cobrado = sum((p.valor for p in s.query(PagoParticipacion).filter(PagoParticipacion.fecha.between(desde, hasta))),
                  CERO)
    por_cobrar = sum((c.valor - sum((p.valor for p in c.pagos if p.fecha <= hasta), CERO)
                      for c in s.query(CertificacionParticipacion)
                      .options(selectinload(CertificacionParticipacion.pagos))
                      .filter(CertificacionParticipacion.fecha <= hasta)), CERO)
    return {"reconocido": reconocido, "cobrado": cobrado, "por_cobrar": por_cobrar}


def pendientes(s):
    """Certificaciones con saldo por cobrar al gestor, de la más antigua a la más reciente."""
    return [c for c in s.query(CertificacionParticipacion)
            .options(selectinload(CertificacionParticipacion.pagos),
                     selectinload(CertificacionParticipacion.contrato).selectinload(ContratoParticipacion.gestor))
            .order_by(CertificacionParticipacion.fecha, CertificacionParticipacion.id) if c.saldo > 0]


# ------------------------------------------------------------------ listado

@bp.route("/")
def lista():
    s = Session()
    anio = anio_arg()
    desde, hasta = date(anio, 1, 1), date(anio, 12, 31)
    contrato_id = request.args.get("contrato", type=int)
    q = (s.query(CertificacionParticipacion)
         .options(selectinload(CertificacionParticipacion.pagos),
                  selectinload(CertificacionParticipacion.contrato).selectinload(ContratoParticipacion.gestor))
         .filter(CertificacionParticipacion.fecha.between(desde, hasta)))
    if contrato_id:
        q = q.filter(CertificacionParticipacion.contrato_id == contrato_id)
    certs = q.order_by(CertificacionParticipacion.fecha.desc(), CertificacionParticipacion.id.desc()).all()
    if request.args.get("xlsx"):
        from .. import exportar
        from . import XLSX, descargar
        enc = ["Fecha", "Contrato", "Socio gestor", "NIT", "Periodo desde", "Periodo hasta", "Ingresos del contrato",
               "% participación", "Ingreso reconocido", "Cobrado", "Por cobrar", "Referencia"]
        datos = [[c.fecha, c.contrato.nombre, c.contrato.gestor.nombre, c.contrato.gestor.nit, c.periodo_desde or "",
                  c.periodo_hasta or "", c.ingresos_contrato if c.ingresos_contrato is not None else "",
                  c.porcentaje if c.porcentaje is not None else "", c.valor, c.cobrado, c.saldo, c.referencia or ""]
                 for c in certs]
        return descargar(exportar.excel({"Cuentas en participación": (enc, datos)}),
                         f"cuentas_participacion_{anio}.xlsx", XLSX)
    contratos = (s.query(ContratoParticipacion).options(selectinload(ContratoParticipacion.gestor))
                 .order_by(ContratoParticipacion.activo.desc(), ContratoParticipacion.nombre).all())
    return render_template("participacion/lista.html", anio=anio, certs=certs, contratos=contratos,
                           contrato_id=contrato_id, tot=resumen(s, desde, hasta),
                           total_valor=sum((c.valor for c in certs), CERO),
                           total_cobrado=sum((c.cobrado for c in certs), CERO),
                           total_saldo=sum((c.saldo for c in certs), CERO))


# ------------------------------------------------------------------ contratos

def _gestor_desde_form(s):
    tid = request.form.get("gestor_id", "")
    if tid == "nuevo":
        nit = request.form.get("nuevo_nit", "").strip()
        nombre = request.form.get("nuevo_nombre", "").strip()
        if not nit or not nombre:
            raise ValueError("Para crear el socio gestor indique NIT y razón social.")
        t = s.query(Tercero).filter_by(nit=nit).one_or_none()
        if t is None:
            t = Tercero(nit=nit, nombre=nombre, tipo_doc="31", es_cliente=True)
            s.add(t)
            s.flush()
        return t.id
    if not tid.isdecimal() or s.get(Tercero, int(tid)) is None:
        raise ValueError("Seleccione el socio gestor (quien factura al cliente y le certifica su participación).")
    return int(tid)


@bp.route("/contrato/nuevo", methods=["GET", "POST"])
@bp.route("/contrato/<int:id>", methods=["GET", "POST"])
def contrato(id=None):
    s = Session()
    c = s.get(ContratoParticipacion, id) if id else None
    if id and c is None:
        abort(404)
    if request.method == "POST":
        try:
            if request.form.get("accion") == "borrar":
                if c is None:
                    raise ValueError("El contrato no existe.")
                if c.certificaciones:
                    raise ValueError("El contrato tiene certificaciones registradas; elimínelas primero o márquelo "
                                     "como inactivo.")
                s.delete(c)
                s.commit()
                flash("Contrato eliminado.", "ok")
                return redirect(url_for("participacion.lista"))
            nuevo = c is None
            c = c or ContratoParticipacion()
            nombre = request.form.get("nombre", "").strip()
            if not nombre:
                raise ValueError("Escriba un nombre para identificar el contrato.")
            gestor_id = _gestor_desde_form(s)
            if not nuevo and c.certificaciones and gestor_id != c.gestor_id:
                raise ValueError("El contrato ya tiene certificaciones contabilizadas a nombre del socio gestor actual; "
                                 "no se puede cambiar el gestor. Cree un contrato nuevo.")
            c.nombre, c.gestor_id = nombre[:160], gestor_id
            c.porcentaje = _porcentaje("porcentaje")
            c.fecha_inicio = _fecha_opcional("fecha_inicio")
            c.activo = request.form.get("activo") is not None if not nuevo else True
            c.notas = request.form.get("notas", "").strip() or None
            s.add(c)
            s.flush()
            soporte = archivos.guardar_upload("participacion", request.files.get("soporte"))
            if soporte:
                c.soporte_archivo = soporte
            s.commit()
            flash("Contrato registrado." if nuevo else "Contrato actualizado.", "ok")
            return redirect(url_for("participacion.lista"))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {explicar(e)}", "error")
            c = s.get(ContratoParticipacion, id) if id else None
    return render_template("participacion/contrato.html", c=c, form=request.form,
                           terceros=s.query(Tercero).order_by(Tercero.nombre).all())


# ------------------------------------------------------------------ certificaciones y pagos

def _llenar_certificacion(s, c):
    contrato_id = entero_requerido("contrato_id", "Seleccione el contrato de cuentas en participación.")
    contrato = s.get(ContratoParticipacion, contrato_id)
    if contrato is None:
        raise ValueError("Seleccione el contrato de cuentas en participación.")
    if c.id and c.pagos and contrato_id != c.contrato_id and contrato.gestor_id != c.contrato.gestor_id:
        raise ValueError("La certificación ya tiene pagos del socio gestor; no se puede pasar a un contrato de otro gestor.")
    c.contrato_id = contrato_id
    c.contrato = contrato
    c.fecha = _fecha_obligatoria("fecha", "Indique la fecha de la certificación (la fecha en que se reconoce el ingreso).")
    c.periodo_desde = _fecha_opcional("periodo_desde")
    c.periodo_hasta = _fecha_opcional("periodo_hasta")
    if c.periodo_desde and c.periodo_hasta and c.periodo_desde > c.periodo_hasta:
        raise ValueError("El periodo certificado está al revés: la fecha inicial es posterior a la final.")
    base = dinero("ingresos_contrato") if request.form.get("ingresos_contrato", "").strip() else None
    if base is not None and base < 0:
        raise ValueError("Los ingresos del contrato no pueden ser negativos.")
    c.ingresos_contrato = base
    c.porcentaje = _porcentaje("porcentaje")
    c.valor = dinero("valor")
    if c.valor <= 0:
        raise ValueError("El ingreso certificado a su favor debe ser mayor que cero.")
    if c.id and c.valor < c.cobrado:
        raise ValueError("El valor certificado no puede ser menor que lo que el socio gestor ya le pagó "
                         f"({c.cobrado:,.0f}).".replace(",", "."))
    c.referencia = request.form.get("referencia", "").strip()[:80] or None
    c.notas = request.form.get("notas", "").strip() or None


@bp.route("/certificacion/nueva", methods=["GET", "POST"])
@bp.route("/certificacion/<int:id>", methods=["GET", "POST"])
def certificacion(id=None):
    s = Session()
    c = s.get(CertificacionParticipacion, id) if id else None
    if id and c is None:
        abort(404)
    if request.method == "POST":
        accion = request.form.get("accion", "guardar")
        try:
            from ..formato import pesos
            if accion == "borrar":
                if c is None:
                    raise ValueError("La certificación no existe.")
                if c.pagos:
                    raise ValueError("La certificación tiene pagos registrados; elimínelos primero.")
                contab.borrar_asientos(s, f"participacion:{c.id}")
                s.delete(c)
                s.commit()
                flash("Certificación eliminada con su asiento contable.", "ok")
                return redirect(url_for("participacion.lista"))
            if accion == "pago":
                if c is None:
                    raise ValueError("Guarde primero la certificación.")
                banco_id = entero_requerido("banco_id", "Seleccione el banco donde entró la transferencia.")
                if s.get(Banco, banco_id) is None:
                    raise ValueError("Seleccione el banco donde entró la transferencia.")
                valor = dinero("pago_valor")
                if valor <= 0:
                    raise ValueError("El valor del pago debe ser mayor que cero.")
                if valor > c.saldo:
                    raise ValueError(f"El pago supera el saldo por cobrar de la certificación ({pesos(c.saldo)}).")
                p = PagoParticipacion(fecha=_fecha_obligatoria("pago_fecha", "Indique la fecha del pago."),
                                      banco_id=banco_id, valor=valor,
                                      referencia=request.form.get("pago_referencia", "").strip()[:80] or None)
                c.pagos.append(p)
                s.flush()
                s.refresh(p)
                contab.contabilizar_pago_participacion(s, p)
                s.commit()
                flash(f"Pago registrado: {pesos(valor)}. Saldo por cobrar {pesos(c.saldo)}.", "ok")
                return redirect(url_for("participacion.certificacion", id=c.id))
            if accion == "borrar_pago":
                p = s.get(PagoParticipacion, request.form.get("pago_id", type=int) or 0)
                if p is None or c is None or p.certificacion_id != c.id:
                    raise ValueError("El pago no existe.")
                bancos.liberar(s, "pagoparticipacion", p.id)
                contab.borrar_asientos(s, f"pagoparticipacion:{p.id}")
                c.pagos.remove(p)
                s.delete(p)
                s.commit()
                flash("Pago eliminado; si estaba conciliado, la línea del extracto volvió a pendiente.", "ok")
                return redirect(url_for("participacion.certificacion", id=c.id))
            nuevo = c is None
            c = c or CertificacionParticipacion()
            gestor_antes = c.contrato.gestor_id if not nuevo else None
            _llenar_certificacion(s, c)
            s.add(c)
            s.flush()
            s.refresh(c)
            contab.contabilizar_certificacion_participacion(s, c)
            if gestor_antes is not None and gestor_antes != c.contrato.gestor_id:
                for p in c.pagos:  # los pagos se contabilizan a nombre del gestor del contrato
                    contab.contabilizar_pago_participacion(s, p)
            soporte = archivos.guardar_upload("participacion", request.files.get("soporte"))
            if soporte:
                c.soporte_archivo = soporte
            s.commit()
            flash(("Ingreso reconocido" if nuevo else "Certificación actualizada")
                  + f": {pesos(c.valor)} de {c.contrato.nombre}, sin IVA.", "ok")
            return redirect(url_for("participacion.certificacion", id=c.id) if nuevo
                            else url_for("participacion.lista", anio=c.fecha.year))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {explicar(e)}", "error")
            c = s.get(CertificacionParticipacion, id) if id else None
    contratos = (s.query(ContratoParticipacion).options(selectinload(ContratoParticipacion.gestor))
                 .filter((ContratoParticipacion.activo.is_(True))
                         | (ContratoParticipacion.id == (c.contrato_id if c else 0)))
                 .order_by(ContratoParticipacion.nombre).all())
    if not contratos and c is None:
        flash("Primero registre el contrato de cuentas en participación con el socio gestor.", "info")
        return redirect(url_for("participacion.contrato"))
    conciliados = {p.id for p in c.pagos if _conciliado(s, p.id)} if c else set()
    return render_template("participacion/certificacion.html", c=c, form=request.form, contratos=contratos,
                           bancos=s.query(Banco).filter((Banco.activo.is_(True))).order_by(Banco.id).all(),
                           contrato_sugerido=request.args.get("contrato", type=int), conciliados=conciliados,
                           hoy=date.today())
