"""Saldos de facturas, aplicación de recaudos y cartera por edades."""
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from .models import CERO, AplicacionRecaudo, DocumentoVenta, Recaudo

EDADES = [("Por vencer", None, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("+90", 91, None)]


def saldo_documento(session, doc, al: date | None = None) -> Decimal:
    """Saldo por cobrar de una factura/ND: total − reteIVA − notas crédito − abonos."""
    if doc.anulada or doc.tipo == "NC":
        return CERO
    saldo = doc.total
    if doc.reteiva_aplica and (al is None or (doc.reteiva_fecha or doc.fecha) <= al):
        saldo -= doc.reteiva_valor
    q = session.query(DocumentoVenta).filter_by(referencia_id=doc.id, tipo="NC", anulada=False)
    for nc in q:
        if al is None or nc.fecha <= al:
            saldo -= nc.total
    for ap in doc.aplicaciones:
        if al is None or ap.recaudo.fecha <= al:
            saldo -= ap.valor
    return saldo


def saldos_en_lote(session, docs, al: date | None = None) -> dict:
    """Saldo de muchos documentos con dos consultas agrupadas (evita una consulta por factura en los listados)."""
    ids = [doc.id for doc in docs]
    if not ids:
        return {}
    q_nc = (session.query(DocumentoVenta.referencia_id, func.sum(DocumentoVenta.total))
            .filter(DocumentoVenta.referencia_id.in_(ids), DocumentoVenta.tipo == "NC",
                    DocumentoVenta.anulada.is_(False)))
    q_ab = (session.query(AplicacionRecaudo.documento_id, func.sum(AplicacionRecaudo.valor)).join(Recaudo)
            .filter(AplicacionRecaudo.documento_id.in_(ids)))
    if al is not None:
        q_nc = q_nc.filter(DocumentoVenta.fecha <= al)
        q_ab = q_ab.filter(Recaudo.fecha <= al)
    notas = {k: Decimal(str(v)) for k, v in q_nc.group_by(DocumentoVenta.referencia_id)}
    abonos = {k: Decimal(str(v)) for k, v in q_ab.group_by(AplicacionRecaudo.documento_id)}
    res = {}
    for doc in docs:
        if doc.anulada or doc.tipo == "NC":
            res[doc.id] = CERO
            continue
        saldo = doc.total
        if doc.reteiva_aplica and (al is None or (doc.reteiva_fecha or doc.fecha) <= al):
            saldo -= doc.reteiva_valor
        res[doc.id] = saldo - notas.get(doc.id, CERO) - abonos.get(doc.id, CERO)
    return res


def estado_documento(session, doc, hoy=None, saldo=None):
    hoy = hoy or date.today()
    if doc.anulada:
        return "Anulada"
    if doc.tipo == "NC":
        return "Nota crédito"
    if saldo is None:
        saldo = saldo_documento(session, doc)
    if saldo <= 0:
        return "Pagada"
    abonado = doc.total - (doc.reteiva_valor if doc.reteiva_aplica else 0) - saldo
    if doc.vencimiento and doc.vencimiento < hoy:
        return "Vencida"
    return "Abonada" if abonado > 0 else "Pendiente"


def documentos_abiertos(session, cliente_id=None, al=None):
    q = session.query(DocumentoVenta).filter(DocumentoVenta.tipo != "NC", DocumentoVenta.anulada.is_(False))
    if cliente_id:
        q = q.filter(DocumentoVenta.cliente_id == cliente_id)
    if al:
        q = q.filter(DocumentoVenta.fecha <= al)
    docs = q.order_by(DocumentoVenta.fecha, DocumentoVenta.id).all()
    saldos = saldos_en_lote(session, docs, al)
    return [(doc, saldos[doc.id]) for doc in docs if saldos[doc.id] > 0]


def anticipos_cliente(session, cliente_id):
    return sum((r.sin_aplicar for r in session.query(Recaudo).filter_by(cliente_id=cliente_id)), CERO)


def cartera_por_edades(session, al: date | None = None):
    """Devuelve filas por cliente con saldos por rango de días vencidos."""
    al = al or date.today()
    clientes = {}
    for doc, saldo in documentos_abiertos(session, al=al):
        fila = clientes.setdefault(doc.cliente_id, {"cliente": doc.cliente, "total": CERO,
                                                    **{e[0]: CERO for e in EDADES}})
        venc = doc.vencimiento or doc.fecha
        dias = (al - venc).days
        for nombre, desde, hasta in EDADES:
            if desde is None and dias <= 0 or desde is not None and dias >= desde and (hasta is None or dias <= hasta):
                fila[nombre] += saldo
                break
        fila["total"] += saldo
    filas = sorted(clientes.values(), key=lambda f: -f["total"])
    totales = {"total": sum((f["total"] for f in filas), CERO)}
    for e in EDADES:
        totales[e[0]] = sum((f[e[0]] for f in filas), CERO)
    return filas, totales


def aplicar_automatico(session, cliente_id, valor: Decimal):
    """Sugiere cómo repartir un pago entre las facturas abiertas, de la más antigua a la más nueva."""
    sugerencia = []
    restante = valor
    for doc, saldo in documentos_abiertos(session, cliente_id):
        if restante <= 0:
            break
        v = min(saldo, restante)
        sugerencia.append((doc, saldo, v))
        restante -= v
    return sugerencia, restante


def registrar_aplicaciones(rec: Recaudo, valores: dict):
    """valores: {documento: valor}. Reemplaza las aplicaciones del recaudo."""
    rec.aplicaciones.clear()
    for doc, v in valores.items():
        if v and v > 0:
            rec.aplicaciones.append(AplicacionRecaudo(documento=doc, valor=v))
