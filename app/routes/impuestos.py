"""IVA, SIMPLE (2593, F260), IVA anual (F300), calendario y exógena."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import archivos, bancos, contab, exogena, exportar, impuestos
from ..config import BIMESTRES, SIMPLE_TARIFA_ANUAL, SIMPLE_TARIFA_BIMESTRAL
from ..db import Session
from ..models import Banco, PagoImpuesto, Vencimiento
from . import XLSX, anio_arg, check, descargar, destino_seguro, dinero, entero_requerido, explicar, fecha_arg

bp = Blueprint("impuestos", __name__, url_prefix="/impuestos")


@bp.route("/iva")
def iva():
    s = Session()
    anio = anio_arg()
    filas = [(b, impuestos.nombre_bimestre(b), impuestos.resumen_iva_bimestre(s, anio, b)) for b in BIMESTRES]
    anual = impuestos.declaracion_iva(s, anio)
    return render_template("impuestos/iva.html", filas=filas, anio=anio, anual=anual)


@bp.route("/iva/<int:anio>/<int:bim>")
def iva_detalle(anio, bim):
    if bim not in BIMESTRES or not 2000 <= anio <= 2100:
        abort(404)
    s = Session()
    r = impuestos.resumen_iva_bimestre(s, anio, bim)
    if request.args.get("xlsx"):
        hojas = {
            "IVA generado": (["Tipo", "Número", "Fecha", "Cliente", "NIT", "Base", "IVA"],
                             [[v.tipo, v.numero, v.fecha, v.cliente.nombre, v.cliente.nit, v.signo * v.ingreso,
                               v.signo * v.iva] for v in r.ventas]),
            "IVA descontable": (["Fecha", "Número", "Proveedor", "NIT", "Categoría", "Base", "IVA", "Descontable"],
                                [[g.fecha, g.numero or "", g.proveedor.nombre if g.proveedor else "",
                                  g.proveedor.nit if g.proveedor else "", g.categoria.nombre, g.signo * g.subtotal,
                                  g.signo * g.iva, g.signo * g.iva_desc_valor] for g in r.gastos]),
            "ReteIVA": (["Factura", "Fecha retención", "Cliente", "NIT", "Valor", "Certificado"],
                        [[v.numero, v.reteiva_fecha or v.fecha, v.cliente.nombre, v.cliente.nit, v.reteiva_valor,
                          "Sí" if v.cert_recibido else "No"] for v in r.retenciones]),
        }
        return descargar(exportar.excel(hojas), f"iva_{anio}_bimestre{bim}.xlsx", XLSX)
    return render_template("impuestos/iva_detalle.html", r=r, anio=anio, bim=bim,
                           nombre=impuestos.nombre_bimestre(bim))


@bp.route("/simple")
def simple():
    s = Session()
    anio = anio_arg()
    recibos = [impuestos.recibo_2593(s, anio, b) for b in BIMESTRES]
    return render_template("impuestos/simple.html", recibos=recibos, anio=anio, nombres=impuestos.nombre_bimestre,
                           bancos=s.query(Banco).filter_by(activo=True).all(),
                           tabla=SIMPLE_TARIFA_BIMESTRAL, uvt=impuestos.uvt(anio, s),
                           base=contab.config(s, "simple_base", "causacion"))


@bp.route("/pago", methods=["POST"])
def registrar_pago():
    s = Session()
    try:
        p = PagoImpuesto(formulario=request.form.get("formulario", "2593"),
                         anio=entero_requerido("anio", "Indique el año del pago."),
                         bimestre=request.form.get("bimestre", type=int), fecha=fecha_arg("fecha", date.today()),
                         valor_simple=dinero("valor_simple"), valor_iva=dinero("valor_iva"),
                         banco_id=entero_requerido("banco_id", "Seleccione el banco con el que pagó."),
                         numero_formulario=request.form.get("numero_formulario") or None)
        if p.total <= 0 or p.valor_simple < 0 or p.valor_iva < 0:
            raise ValueError("Los valores pagados deben ser positivos.")
        if p.formulario not in ("2593", "260", "300") or (p.formulario == "2593" and p.bimestre not in BIMESTRES):
            raise ValueError("Formulario o bimestre no válido.")
        p.archivo = archivos.guardar_upload("impuestos", request.files.get("archivo"))
        s.add(p)
        s.flush()
        s.refresh(p)
        contab.contabilizar_pago_impuesto(s, p)
        if p.formulario == "2593":
            v = (s.query(Vencimiento).filter(Vencimiento.obligacion.like("Recibo 2593%"),
                                             Vencimiento.periodo.like(f"Bimestre {p.bimestre} %{p.anio}")).first())
            if v:
                v.cumplido = True
            # Al pagar el recibo, el bimestre queda declarado: se bloquea para evitar cambios accidentales.
            fin = impuestos.rango_bimestre(p.anio, p.bimestre)[1] if p.bimestre else None
            tope = contab.periodo_bloqueado_hasta(s)
            if fin and (tope is None or fin > tope):
                contab.set_config(s, "periodo_bloqueado_hasta", fin.isoformat())
                flash(f"Contabilidad bloqueada hasta el {fin:%d/%m/%Y}. Se puede cambiar en Configuración.", "info")
        s.commit()
        flash("Pago registrado y contabilizado.", "ok")
    except Exception as e:  # noqa: BLE001
        s.rollback()
        flash(f"No se pudo registrar el pago: {explicar(e)}", "error")
    return redirect(destino_seguro(request.form.get("volver"), url_for("impuestos.simple")))


@bp.route("/pago/<int:id>/eliminar", methods=["POST"])
def eliminar_pago(id):
    s = Session()
    p = s.get(PagoImpuesto, id) or abort(404)
    try:
        llevaba_reteiva = p.formulario == "2593" and bool(p.bimestre) and contab.pago_lleva_reteiva(s, p)
        bancos.liberar(s, "impuesto", p.id)
        contab.borrar_asientos(s, f"impuesto:{p.id}")
        anio, bimestre, formulario = p.anio, p.bimestre, p.formulario
        s.delete(p)
        s.flush()
        if formulario == "2593" and bimestre:
            if llevaba_reteiva:
                contab.recontabilizar_pagos_bimestre(s, anio, bimestre)  # el siguiente pago asume la reteIVA
            if not s.query(PagoImpuesto).filter_by(formulario="2593", anio=anio, bimestre=bimestre).first():
                v = (s.query(Vencimiento).filter(Vencimiento.obligacion.like("Recibo 2593%"),
                                                 Vencimiento.periodo.like(f"Bimestre {bimestre} %{anio}")).first())
                if v:
                    v.cumplido = False  # vuelve a aparecer como pendiente en el calendario
                tope = contab.periodo_bloqueado_hasta(s)
                if tope and tope == impuestos.rango_bimestre(anio, bimestre)[1]:
                    flash(f"La contabilidad sigue bloqueada hasta el {tope:%d/%m/%Y}; si el bimestre no está declarado, "
                          "cambie la fecha de bloqueo en Configuración.", "info")
        s.commit()
        flash("Pago eliminado.", "ok")
    except Exception as e:  # noqa: BLE001
        s.rollback()
        flash(f"No se pudo eliminar: {explicar(e)}", "error")
    return redirect(destino_seguro(request.form.get("volver"), url_for("impuestos.simple")))


@bp.route("/f260", methods=["GET", "POST"])
def f260():
    s = Session()
    anio = anio_arg()
    if request.method == "POST":
        try:
            impuestos.causar_simple_anual(s, anio)
            s.commit()
            flash(f"Impuesto SIMPLE {anio} causado en la contabilidad (asiento al 31 de diciembre).", "ok")
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo causar: {explicar(e)}", "error")
        return redirect(url_for("impuestos.f260", anio=anio))
    dec = impuestos.declaracion_simple(s, anio)
    pagos = s.query(PagoImpuesto).filter_by(formulario="260", anio=anio).all()
    return render_template("impuestos/f260.html", dec=dec, anio=anio, tabla=SIMPLE_TARIFA_ANUAL,
                           uvt=impuestos.uvt(anio, s), pagos=pagos, bancos=s.query(Banco).filter_by(activo=True).all())


@bp.route("/f300")
def f300():
    s = Session()
    anio = anio_arg()
    dec = impuestos.declaracion_iva(s, anio)
    pagos = s.query(PagoImpuesto).filter_by(formulario="300", anio=anio).all()
    bimestres = [(b, impuestos.nombre_bimestre(b), impuestos.resumen_iva_bimestre(s, anio, b)) for b in BIMESTRES]
    return render_template("impuestos/f300.html", dec=dec, anio=anio, pagos=pagos, bimestres=bimestres,
                           bancos=s.query(Banco).filter_by(activo=True).all())


@bp.route("/calendario", methods=["GET", "POST"])
def calendario():
    s = Session()
    if request.method == "POST":
        if request.form.get("accion") == "agregar":
            if not request.form.get("obligacion", "").strip() or fecha_arg("fecha") is None:
                flash("Indique la obligación y una fecha válida.", "error")
                return redirect(url_for("impuestos.calendario"))
            s.add(Vencimiento(obligacion=request.form["obligacion"].strip(), periodo=request.form.get("periodo"),
                              fecha=fecha_arg("fecha"), notas=request.form.get("notas") or None))
        elif request.form.get("eliminar"):
            v = s.get(Vencimiento, request.form.get("eliminar", type=int) or 0)
            if v is not None:
                s.delete(v)
        else:
            for v in s.query(Vencimiento):
                v.fecha = fecha_arg(f"fecha_{v.id}", v.fecha)
                v.cumplido = check(f"cumplido_{v.id}")
        s.commit()
        flash("Calendario actualizado.", "ok")
        return redirect(url_for("impuestos.calendario"))
    return render_template("impuestos/calendario.html", vencimientos=s.query(Vencimiento).order_by(Vencimiento.fecha))


@bp.route("/exogena")
def exogena_vista():
    s = Session()
    anio = anio_arg()
    hojas, incompletos = exogena.generar(s, anio)
    if request.args.get("xlsx"):
        return descargar(exportar.excel(hojas), f"exogena_{anio}.xlsx", XLSX)
    return render_template("impuestos/exogena.html", hojas=hojas, incompletos=incompletos, anio=anio)


# ------------------------------------------------------------------ Formulario 2593 en PDF

def _marcar_bimestre_pagado(s, p):
    """Cumple el vencimiento del calendario y bloquea el bimestre declarado. Devuelve la nueva fecha de bloqueo."""
    v = (s.query(Vencimiento).filter(Vencimiento.obligacion.like("Recibo 2593%"),
                                     Vencimiento.periodo.like(f"Bimestre {p.bimestre} %{p.anio}")).first())
    if v:
        v.cumplido = True
    fin = impuestos.rango_bimestre(p.anio, p.bimestre)[1]
    tope = contab.periodo_bloqueado_hasta(s)
    if tope is None or fin > tope:
        contab.set_config(s, "periodo_bloqueado_hasta", fin.isoformat())
        return fin
    return None


def _leer_guardado(relativa):
    """Vuelve a leer un 2593 ya guardado en la carpeta de adjuntos (no se confía en cifras enviadas por el formulario)."""
    from .. import recibos_dian
    if not relativa.startswith("impuestos/") or not relativa.lower().endswith(".pdf"):
        raise ValueError("Archivo no válido.")
    ruta = archivos.ruta_absoluta(relativa)
    if not ruta.is_file():
        raise ValueError("El archivo adjunto ya no está; vuelva a adjuntarlo.")
    return recibos_dian.leer_2593(ruta.read_bytes())


def _propuesta(s, r, archivo):
    """Lo que se registraría con un 2593: pago, gasto por impuesto e ingreso por reconocer."""
    from ..models import CertificacionParticipacion
    inicio, fin = impuestos.rango_bimestre(r.anio, r.bimestre)
    pagos = s.query(PagoImpuesto).filter_by(formulario="2593", anio=r.anio, bimestre=r.bimestre).all()
    mismo = next((p for p in pagos if p.numero_formulario == r.numero), None)
    igual = next((p for p in pagos if p.total == r.total and not p.numero_formulario), None)
    registrado = impuestos.ingresos_brutos(s, inicio, fin)
    cert = s.query(CertificacionParticipacion).filter_by(referencia=f"Formulario 2593 {r.numero}").first()
    nit = (contab.config(s, "empresa_nit", "") or "").split("-")[0].replace(".", "").strip()
    avisos = list(r.advertencias)
    if nit and r.nit[:-1] != nit and r.nit != nit:
        avisos.append(f"El NIT del formulario ({r.nit[:-1]}-{r.nit[-1]}) no es el de la empresa.")
    if mismo:
        estado = "registrado"
    elif igual:
        estado = "completar"
        avisos.append("Ya hay un pago de este bimestre por el mismo valor, sin formulario: se le adjunta este.")
    elif pagos:
        estado = "otro"
        avisos.append(f"Ya hay {len(pagos)} pago(s) de este bimestre por otro valor; revise antes de registrar.")
    else:
        estado = "nuevo"
    return {"r": r, "archivo": archivo, "estado": estado, "avisos": avisos, "registrado": registrado,
            "por_reconocer": max(r.ingresos - registrado, contab.CERO) if not cert else contab.CERO,
            "certificado": cert is not None, "nombre": impuestos.nombre_bimestre(r.bimestre),
            "fecha": r.fecha_pago or (fin if r.total == 0 else None)}


@bp.route("/recibos", methods=["GET", "POST"])
def recibos():
    """Adjuntar uno o varios Formularios 2593 en PDF: registra el pago, el gasto por impuesto y, si se pide,
    el ingreso de cuentas en participación del bimestre."""
    from sqlalchemy.orm import selectinload
    from .. import recibos_dian
    from ..models import CertificacionParticipacion, ContratoParticipacion, Tercero
    s = Session()
    contratos = (s.query(ContratoParticipacion).options(selectinload(ContratoParticipacion.gestor))
                 .filter_by(activo=True).order_by(ContratoParticipacion.nombre).all())
    bancos_activos = s.query(Banco).filter_by(activo=True).order_by(Banco.id).all()
    if request.method == "POST" and request.form.get("accion") == "registrar":
        try:
            filas = [i for i in request.form.getlist("fila") if i.isdecimal()]
            banco_id = entero_requerido("banco_id", "Seleccione el banco desde el que pagó.")
            if s.get(Banco, banco_id) is None:
                raise ValueError("Seleccione el banco desde el que pagó.")
            leidos = []
            for i in filas:
                r = _leer_guardado(request.form.get(f"archivo_{i}", ""))
                leidos.append((i, r, request.form.get(f"archivo_{i}")))
            leidos.sort(key=lambda x: (x[1].anio, x[1].bimestre))
            # Bimestres ya bloqueados: solo con permiso expreso se corre la fecha de bloqueo (y luego se restituye)
            tope_original = contab.periodo_bloqueado_hasta(s)
            fechas = [impuestos.rango_bimestre(r.anio, r.bimestre)[1] for i, r, _ in leidos if request.form.get(f"reconocer_{i}")]
            fechas += [f for i, _, _ in leidos if request.form.get(f"pagar_{i}") and (f := fecha_arg(f"fecha_{i}"))]
            if tope_original and fechas and min(fechas) <= tope_original:
                if not request.form.get("mover_bloqueo"):
                    raise ValueError(f"La contabilidad está bloqueada hasta el {tope_original:%d/%m/%Y}. Marque «Permitir "
                                     "registrar en bimestres bloqueados» o cambie la fecha en Configuración.")
                primero = min(min(fechas), min(impuestos.rango_bimestre(r.anio, r.bimestre)[0] for _, r, _ in leidos))
                contab.set_config(s, "periodo_bloqueado_hasta", date.fromordinal(primero.toordinal() - 1).isoformat())
            # 1. Ingresos de cuentas en participación (antes de los pagos, que bloquean el bimestre)
            contrato = None
            reconocer = [(i, r) for i, r, _ in leidos if request.form.get(f"reconocer_{i}")]
            if reconocer:
                cid = request.form.get("contrato_id", "")
                if cid == "nuevo":
                    nit = request.form.get("gestor_nit", "").replace(".", "").split("-")[0].strip()
                    nombre = request.form.get("gestor_nombre", "").strip()
                    if not nit.isdecimal() or not nombre:
                        raise ValueError("Para crear el contrato indique el NIT (sin DV) y la razón social del socio gestor.")
                    gestor = s.query(Tercero).filter_by(nit=nit).one_or_none()
                    if gestor is None:
                        gestor = Tercero(nit=nit, nombre=nombre, tipo_doc="31", es_cliente=True)
                        s.add(gestor)
                        s.flush()
                    contrato = ContratoParticipacion(nombre=f"Cuentas en participación {nombre}"[:160], gestor=gestor)
                    s.add(contrato)
                    s.flush()
                else:
                    contrato = s.get(ContratoParticipacion, int(cid)) if cid.isdecimal() else None
                    if contrato is None:
                        raise ValueError("Seleccione el contrato de cuentas en participación del socio gestor.")
                from ..contab import d
                for i, r in reconocer:
                    inicio, fin = impuestos.rango_bimestre(r.anio, r.bimestre)
                    referencia = f"Formulario 2593 {r.numero}"
                    if s.query(CertificacionParticipacion).filter_by(referencia=referencia).first():
                        continue
                    valor = dinero(f"ingreso_{i}")
                    if valor <= 0:
                        continue
                    if valor > r.ingresos:
                        raise ValueError(f"El ingreso a reconocer del bimestre {r.bimestre} supera los ingresos declarados.")
                    c = CertificacionParticipacion(contrato=contrato, fecha=fin, periodo_desde=inicio, periodo_hasta=fin,
                                                   valor=valor, referencia=referencia,
                                                   notas=f"Ingresos declarados en el 2593: {r.ingresos:,.0f}".replace(",", "."))
                    s.add(c)
                    s.flush()
                    contab.contabilizar_certificacion_participacion(s, c)
            # 2. Pagos y gasto por impuesto
            hechos, bloqueo = 0, None
            for i, r, archivo in leidos:
                if not request.form.get(f"pagar_{i}"):
                    continue
                fecha = fecha_arg(f"fecha_{i}")
                if fecha is None:
                    raise ValueError(f"Indique la fecha de pago del bimestre {r.bimestre} de {r.anio}.")
                pagos = s.query(PagoImpuesto).filter_by(formulario="2593", anio=r.anio, bimestre=r.bimestre).all()
                if any(p.numero_formulario == r.numero for p in pagos):
                    continue  # ya registrado
                p = next((p for p in pagos if p.total == r.total and not p.numero_formulario), None)
                if p is None:
                    p = PagoImpuesto(formulario="2593", anio=r.anio, bimestre=r.bimestre)
                    s.add(p)
                p.fecha, p.banco_id = fecha, banco_id
                p.valor_simple, p.valor_iva = r.componente_ica + r.simple_a_pagar, r.iva_a_pagar
                p.numero_formulario, p.archivo, p.recibo_490 = r.numero, archivo, r.recibo_490
                p.ingresos_declarados, p.componente_ica, p.aportes_pension = r.ingresos, r.componente_ica, r.aportes_pension
                s.flush()
                s.refresh(p)
                if p.total > 0:
                    contab.contabilizar_pago_impuesto(s, p)
                bloqueo = _marcar_bimestre_pagado(s, p) or bloqueo
                hechos += 1
            actual = contab.periodo_bloqueado_hasta(s)
            if tope_original and (actual is None or actual < tope_original):
                contab.set_config(s, "periodo_bloqueado_hasta", tope_original.isoformat())
                bloqueo = tope_original
            s.commit()
            partes = []
            if reconocer:
                partes.append(f"{len(reconocer)} ingreso(s) de cuentas en participación reconocido(s)")
            partes.append(f"{hechos} pago(s) del 2593 registrado(s) con su gasto por impuesto")
            flash(". ".join(partes) + ".", "ok")
            if bloqueo:
                flash(f"Contabilidad bloqueada hasta el {bloqueo:%d/%m/%Y}. Se puede cambiar en Configuración.", "info")
            anio = leidos[0][1].anio if leidos else date.today().year
            return redirect(url_for("impuestos.simple", anio=anio))
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se registró nada: {explicar(e)}", "error")
            return redirect(url_for("impuestos.recibos"))
    propuestas, errores = [], []
    if request.method == "POST":  # adjuntar y leer
        for f in request.files.getlist("pdf"):
            if not f or not f.filename:
                continue
            contenido = f.read()
            try:
                r = recibos_dian.leer_2593(contenido)
            except ValueError as e:
                errores.append(f"{f.filename}: {e}")
                continue
            if any(x["r"].numero == r.numero for x in propuestas):
                continue
            nombre = f"2593_{r.anio}_bim{r.bimestre}_{r.numero}.pdf"
            propuestas.append(_propuesta(s, r, archivos.guardar("impuestos", nombre, contenido)))
        if not propuestas and not errores:
            errores.append("Seleccione uno o varios PDF del Formulario 2593.")
        propuestas.sort(key=lambda x: (x["r"].anio, x["r"].bimestre))
    tope = contab.periodo_bloqueado_hasta(s)
    return render_template("impuestos/recibos.html", propuestas=propuestas, errores=errores, contratos=contratos,
                           bancos=bancos_activos, tope=tope,
                           bloqueado=bool(tope) and any(
                               impuestos.rango_bimestre(x["r"].anio, x["r"].bimestre)[1] <= tope
                               or (x["fecha"] and x["fecha"] <= tope) for x in propuestas),
                           pagos=(s.query(PagoImpuesto).filter(PagoImpuesto.formulario == "2593",
                                                               PagoImpuesto.archivo.isnot(None))
                                  .order_by(PagoImpuesto.anio.desc(), PagoImpuesto.bimestre.desc()).limit(24).all()))
