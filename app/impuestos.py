"""IVA bimestral, recibo 2593, declaración anual SIMPLE (F260) e IVA anual (F300)."""
import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from . import config as cfg
from . import contab
from .models import CERO, Asiento, DocumentoVenta, Gasto, Movimiento, PagoImpuesto, Recaudo

MIL = Decimal("1000")


def redondeo_dian(valor: Decimal) -> Decimal:
    """Los valores de declaraciones y recibos se aproximan al múltiplo de mil más cercano."""
    return (Decimal(valor) / MIL).quantize(Decimal("1"), rounding="ROUND_HALF_UP") * MIL


def rango_bimestre(anio, bim):
    inicio = date(anio, 2 * bim - 1, 1)
    mes_fin = 2 * bim
    return inicio, date(anio, mes_fin, calendar.monthrange(anio, mes_fin)[1])


def bimestre_de(f: date) -> int:
    return (f.month + 1) // 2


def nombre_bimestre(bim):
    a, b = cfg.BIMESTRES[bim]
    return f"{a}-{b}"


def uvt(anio):
    return Decimal(cfg.UVT.get(anio) or cfg.UVT[max(cfg.UVT)])


def tarifa(ingresos: Decimal, anio: int, tabla) -> Decimal:
    en_uvt = ingresos / uvt(anio)
    for hasta, t in tabla:
        if en_uvt <= hasta:
            return Decimal(str(t))
    return Decimal(str(tabla[-1][1]))


# ------------------------------------------------------------------------- IVA

@dataclass
class ResumenIVA:
    inicio: date
    fin: date
    ventas: list = field(default_factory=list)
    gastos: list = field(default_factory=list)
    retenciones: list = field(default_factory=list)
    ingresos_gravados: Decimal = CERO
    iva_generado: Decimal = CERO
    iva_devoluciones: Decimal = CERO
    compras_base: Decimal = CERO
    iva_descontable: Decimal = CERO
    iva_no_descontable: Decimal = CERO
    reteiva: Decimal = CERO
    pagado: Decimal = CERO

    @property
    def iva_generado_neto(self):
        return self.iva_generado - self.iva_devoluciones

    @property
    def neto(self):
        """Positivo: IVA a pagar. Negativo: saldo a favor del periodo."""
        return self.iva_generado_neto - self.iva_descontable - self.reteiva

    @property
    def a_pagar(self):
        return redondeo_dian(max(self.neto, CERO))


def resumen_iva(session, inicio: date, fin: date) -> ResumenIVA:
    r = ResumenIVA(inicio, fin)
    r.ventas = (session.query(DocumentoVenta)
                .filter(DocumentoVenta.fecha.between(inicio, fin), DocumentoVenta.anulada.is_(False))
                .order_by(DocumentoVenta.fecha).all())
    for v in r.ventas:
        if v.tipo == "NC":
            r.iva_devoluciones += v.iva
            r.ingresos_gravados -= v.ingreso
        else:
            r.iva_generado += v.iva
            r.ingresos_gravados += v.ingreso
    r.gastos = session.query(Gasto).filter(Gasto.fecha.between(inicio, fin)).order_by(Gasto.fecha).all()
    for g in r.gastos:
        r.compras_base += g.signo * g.subtotal
        r.iva_descontable += g.signo * g.iva_desc_valor
        r.iva_no_descontable += g.signo * (g.iva - g.iva_desc_valor)
    candidatos = (session.query(DocumentoVenta)
                  .filter(DocumentoVenta.reteiva_aplica.is_(True), DocumentoVenta.anulada.is_(False),
                          DocumentoVenta.tipo != "NC",
                          func.coalesce(DocumentoVenta.reteiva_fecha, DocumentoVenta.fecha).between(inicio, fin))
                  .order_by(DocumentoVenta.fecha).all())
    r.retenciones = candidatos
    r.reteiva = sum((v.reteiva_valor for v in candidatos), CERO)
    return r


def resumen_iva_bimestre(session, anio, bim) -> ResumenIVA:
    r = resumen_iva(session, *rango_bimestre(anio, bim))
    r.pagado = sum((p.valor_iva for p in session.query(PagoImpuesto)
                    .filter_by(formulario="2593", anio=anio, bimestre=bim)), CERO)
    return r


# ---------------------------------------------------------------------- SIMPLE

def ingresos_brutos(session, inicio: date, fin: date, base: str | None = None) -> Decimal:
    """Ingresos brutos (ordinarios y extraordinarios) del periodo, netos de notas crédito."""
    base = base or contab.config(session, "simple_base", "causacion")
    if base == "caja":
        total = CERO
        for rec in session.query(Recaudo).filter(Recaudo.fecha.between(inicio, fin)):
            for ap in rec.aplicaciones:
                doc = ap.documento
                cobrable = doc.total - (doc.reteiva_valor if doc.reteiva_aplica else CERO)
                if cobrable > 0:
                    total += ap.valor * doc.ingreso / cobrable
        # otros ingresos (financieros, recuperaciones) según contabilidad
        total += _saldo_clase(session, inicio, fin, prefijo="42")
        return contab.redondear(total)
    return _saldo_clase(session, inicio, fin, prefijo="4")


def _saldo_clase(session, inicio, fin, prefijo):
    q = (session.query(func.coalesce(func.sum(Movimiento.credito - Movimiento.debito), 0))
         .join(Asiento).filter(Asiento.fecha.between(inicio, fin), Movimiento.cuenta.like(f"{prefijo}%")))
    return contab.redondear(q.scalar() or 0)


@dataclass
class Recibo2593:
    anio: int
    bimestre: int
    inicio: date
    fin: date
    ingresos: Decimal
    tarifa: Decimal
    anticipo_simple: Decimal
    iva: ResumenIVA
    pagos: list
    vencimiento: date | None

    @property
    def iva_a_pagar(self):
        return self.iva.a_pagar

    @property
    def total(self):
        return self.anticipo_simple + self.iva_a_pagar

    @property
    def pagado(self):
        return sum((p.total for p in self.pagos), CERO)


def recibo_2593(session, anio, bim) -> Recibo2593:
    from .models import Vencimiento
    inicio, fin = rango_bimestre(anio, bim)
    ingresos = ingresos_brutos(session, inicio, fin)
    t = tarifa(ingresos, anio, cfg.SIMPLE_TARIFA_BIMESTRAL)
    anticipo = redondeo_dian(max(ingresos, CERO) * t)
    pagos = session.query(PagoImpuesto).filter_by(formulario="2593", anio=anio, bimestre=bim).all()
    venc = (session.query(Vencimiento).filter(Vencimiento.obligacion.like("Recibo 2593%"),
                                              Vencimiento.periodo.like(f"Bimestre {bim} %{anio}")).first())
    return Recibo2593(anio, bim, inicio, fin, ingresos, t, anticipo, resumen_iva_bimestre(session, anio, bim),
                      pagos, venc.fecha if venc else None)


@dataclass
class DeclaracionSimple:
    anio: int
    ingresos: Decimal
    ingresos_uvt: Decimal
    tarifa: Decimal
    impuesto: Decimal
    ingresos_medios_electronicos: Decimal
    descuento_medios_electronicos: Decimal
    anticipos: Decimal
    supera_limite: bool

    @property
    def impuesto_neto(self):
        return self.impuesto - self.descuento_medios_electronicos

    @property
    def saldo(self):
        """Positivo: saldo a pagar. Negativo: saldo a favor."""
        return self.impuesto_neto - self.anticipos


def declaracion_simple(session, anio) -> DeclaracionSimple:
    inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
    ingresos = ingresos_brutos(session, inicio, fin)
    t = tarifa(ingresos, anio, cfg.SIMPLE_TARIFA_ANUAL)
    impuesto = redondeo_dian(max(ingresos, CERO) * t)
    # Ingresos recibidos por tarjetas y medios electrónicos (art. 912 E.T.), sin IVA.
    electronicos = CERO
    for rec in session.query(Recaudo).filter(Recaudo.fecha.between(inicio, fin), Recaudo.medio_electronico.is_(True)):
        for ap in rec.aplicaciones:
            doc = ap.documento
            cobrable = doc.total - (doc.reteiva_valor if doc.reteiva_aplica else CERO)
            if cobrable > 0:
                electronicos += ap.valor * doc.ingreso / cobrable
    descuento = redondeo_dian(electronicos * contab.d(cfg.SIMPLE_DESCUENTO_MEDIOS_ELECTRONICOS))
    descuento = min(descuento, impuesto)
    anticipos = sum((p.valor_simple for p in session.query(PagoImpuesto).filter_by(formulario="2593", anio=anio)),
                    CERO)
    return DeclaracionSimple(anio, ingresos, contab.redondear(ingresos / uvt(anio)), t, impuesto,
                             contab.redondear(electronicos), descuento, anticipos,
                             ingresos > uvt(anio) * cfg.SIMPLE_LIMITE_UVT_PROFESIONALES)


def causar_simple_anual(session, anio):
    """Registra el gasto del impuesto SIMPLE del año contra los anticipos y el saldo por pagar."""
    dec = declaracion_simple(session, anio)
    contra_anticipos = min(dec.anticipos, dec.impuesto_neto)
    por_pagar = max(dec.impuesto_neto - dec.anticipos, CERO)
    contab.guardar_asiento(
        session, origen=f"simple:{anio}", tipo="AJ", fecha=date(anio, 12, 31),
        descripcion=f"Impuesto unificado SIMPLE año gravable {anio}", tercero_id=None,
        lineas=[(contab.CTA_GASTO_SIMPLE, dec.impuesto_neto, 0, None, None),
                (contab.CTA_ANTICIPO_SIMPLE, 0, contra_anticipos, None, None),
                (contab.CTA_SIMPLE_POR_PAGAR, 0, por_pagar, None, None)])
    return dec


@dataclass
class DeclaracionIVA:
    anio: int
    resumen: ResumenIVA
    pagos_bimestrales: Decimal

    @property
    def saldo(self):
        """Positivo: saldo a pagar con la declaración anual. Negativo: saldo a favor."""
        return redondeo_dian(self.resumen.neto) - self.pagos_bimestrales


def declaracion_iva(session, anio) -> DeclaracionIVA:
    r = resumen_iva(session, date(anio, 1, 1), date(anio, 12, 31))
    pagos = sum((p.valor_iva for p in session.query(PagoImpuesto).filter_by(formulario="2593", anio=anio)), CERO)
    return DeclaracionIVA(anio, r, pagos)
