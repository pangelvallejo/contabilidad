"""Tablero principal."""
from datetime import date, timedelta

from flask import Blueprint, render_template

from .. import cartera, impuestos, reportes
from ..db import Session
from ..models import CERO, DocumentoVenta, Gasto, Recaudo, Tercero, Vencimiento

bp = Blueprint("inicio", __name__)


def alertas(s, hoy):
    res = []
    vencidas = [(doc, saldo) for doc, saldo in cartera.documentos_abiertos(s)
                if doc.vencimiento and doc.vencimiento < hoy]
    if vencidas:
        total = sum((x[1] for x in vencidas), CERO)
        res.append(("critico", f"{len(vencidas)} factura(s) vencida(s) por cobrar", total, "/cartera"))
    sin_cert = (s.query(DocumentoVenta).filter(DocumentoVenta.reteiva_aplica.is_(True),
                                               DocumentoVenta.cert_recibido.is_(False),
                                               DocumentoVenta.anulada.is_(False)).all())
    if sin_cert:
        res.append(("advertencia", f"{len(sin_cert)} certificado(s) de reteIVA pendiente(s)",
                    sum((v.reteiva_valor for v in sin_cert), CERO), "/certificados"))
    por_revisar = s.query(Gasto).filter(Gasto.revisado.is_(False)).count()
    if por_revisar:
        res.append(("advertencia", f"{por_revisar} gasto(s) importado(s) para revisar la categoría", None,
                    "/gastos?revisar=1"))
    sin_soporte = s.query(Gasto).filter(Gasto.soporte_archivo.is_(None), Gasto.xml_archivo.is_(None)).count()
    if sin_soporte:
        res.append(("info", f"{sin_soporte} gasto(s) sin soporte adjunto", None, "/gastos?sin_soporte=1"))
    # Gastos mensuales recurrentes que no se han registrado este mes
    inicio_mes = hoy.replace(day=1)
    recurrentes = (s.query(Gasto).filter(Gasto.recurrente.is_(True), Gasto.fecha < inicio_mes)
                   .order_by(Gasto.fecha.desc()).all())
    vistos, faltan = set(), []
    for g in recurrentes:
        clave = (g.proveedor_id, g.categoria_id)
        if clave in vistos:
            continue
        vistos.add(clave)
        existe = (s.query(Gasto).filter(Gasto.fecha >= inicio_mes, Gasto.proveedor_id == g.proveedor_id,
                                        Gasto.categoria_id == g.categoria_id).first())
        if existe is None:
            faltan.append(g)
    for g in faltan:
        nombre = g.proveedor.nombre if g.proveedor else g.categoria.nombre
        res.append(("info", f"Gasto mensual sin registrar: {nombre} ({g.categoria.nombre})", g.total,
                    f"/gastos/nuevo?copiar={g.id}"))
    # Clientes con honorarios mensuales a los que no se les ha facturado este mes
    for t in s.query(Tercero).filter(Tercero.retainer_mensual.isnot(None), Tercero.retainer_mensual > 0):
        facturado = (s.query(DocumentoVenta).filter(DocumentoVenta.cliente_id == t.id, DocumentoVenta.tipo == "FV",
                                                    DocumentoVenta.anulada.is_(False),
                                                    DocumentoVenta.fecha >= inicio_mes).first())
        if facturado is None:
            res.append(("info", f"Honorarios mensuales sin facturar este mes: {t.nombre}", t.retainer_mensual,
                        f"/ventas?cliente={t.id}"))
    for v in (s.query(Vencimiento).filter(Vencimiento.cumplido.is_(False), Vencimiento.fecha <= hoy + timedelta(days=30))
              .order_by(Vencimiento.fecha)):
        nivel = "critico" if v.fecha < hoy else "advertencia"
        texto = "Vencido" if v.fecha < hoy else f"Vence el {v.fecha:%d/%m/%Y}"
        res.append((nivel, f"{texto}: {v.obligacion} — {v.periodo}", None, "/impuestos/calendario"))
    return res


@bp.route("/")
def tablero():
    s = Session()
    hoy = date.today()
    inicio_mes = hoy.replace(day=1)
    inicio_anio = date(hoy.year, 1, 1)
    bim = impuestos.bimestre_de(hoy)

    ventas_mes = impuestos.resumen_iva(s, inicio_mes, hoy)
    ventas_anio = impuestos.resumen_iva(s, inicio_anio, hoy)
    recaudado_mes = sum((r.valor for r in s.query(Recaudo).filter(Recaudo.fecha.between(inicio_mes, hoy))), CERO)
    filas_cartera, tot_cartera = cartera.cartera_por_edades(s, hoy)
    recibo = impuestos.recibo_2593(s, hoy.year, bim)
    serie = reportes.serie_mensual(s, hoy.year)
    por_categoria = reportes.gastos_por_categoria(s, inicio_anio, hoy)
    gastos_mes = sum((v for _, v in reportes.gastos_por_categoria(s, inicio_mes, hoy)), CERO)

    return render_template(
        "tablero.html", ventas_mes=ventas_mes, ventas_anio=ventas_anio, recaudado_mes=recaudado_mes,
        filas_cartera=filas_cartera[:5], tot_cartera=tot_cartera, edades=cartera.EDADES, recibo=recibo,
        serie=[x for x in serie if x[0] <= hoy.month], por_categoria=por_categoria[:8], gastos_mes=gastos_mes,
        alertas=alertas(s, hoy), nombre_bimestre=impuestos.nombre_bimestre(bim),
        proyeccion=impuestos.proyeccion_anual(s, hoy.year, hoy))
