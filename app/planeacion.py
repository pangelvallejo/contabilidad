"""Flujo de caja proyectado, depreciación de activos y cierre del ejercicio."""
import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import cartera, contab, impuestos, reportes
from .models import CERO, Asiento, Gasto, Vencimiento

CTA_DEPRECIACION_GASTO = {"1524": "516015", "1528": "516020"}
CTA_DEPRECIACION_ACUM = {"1524": "159215", "1528": "159220"}
VIDA_UTIL_DEFECTO = {"1524": 120, "1528": 60}  # meses: muebles 10 años, cómputo 5 años


def _sumar_mes(f: date, n: int) -> date:
    m = f.month - 1 + n
    return date(f.year + m // 12, m % 12 + 1, 1)


def _fin_mes(f: date) -> date:
    return date(f.year, f.month, calendar.monthrange(f.year, f.month)[1])


@dataclass
class MesFlujo:
    inicio: date
    cobros: Decimal = CERO
    cobros_vencidos: Decimal = CERO
    retainers: Decimal = CERO
    pagos_proveedores: Decimal = CERO
    impuestos: Decimal = CERO
    gastos_recurrentes: Decimal = CERO
    detalle_impuestos: list = field(default_factory=list)
    saldo_inicial: Decimal = CERO

    @property
    def entradas(self):
        return self.cobros + self.cobros_vencidos + self.retainers

    @property
    def salidas(self):
        return self.pagos_proveedores + self.impuestos + self.gastos_recurrentes

    @property
    def saldo_final(self):
        return self.saldo_inicial + self.entradas - self.salidas


def flujo_de_caja(session, meses=6, hoy: date | None = None):
    """Proyección mensual: cartera por vencimiento, cuentas por pagar, impuestos pendientes y gastos recurrentes."""
    hoy = hoy or date.today()
    primero = hoy.replace(day=1)
    filas = [MesFlujo(_sumar_mes(primero, i)) for i in range(meses)]

    def fila_para(fecha):
        if fecha < primero:
            return filas[0]
        idx = (fecha.year - primero.year) * 12 + fecha.month - primero.month
        return filas[idx] if idx < meses else None

    for doc, saldo in cartera.documentos_abiertos(session):
        venc = doc.vencimiento or doc.fecha
        f = fila_para(venc)
        if f is None:
            continue
        if venc < hoy:
            f.cobros_vencidos += saldo
        else:
            f.cobros += saldo
    for g in session.query(Gasto).filter(Gasto.forma_pago == "credito", Gasto.tipo_soporte != "NC"):
        if g.saldo > 0:
            f = fila_para(g.vencimiento or g.fecha)
            if f is not None:
                f.pagos_proveedores += g.saldo
    # Impuestos: recibos 2593 pendientes (estimados) y vencimientos del calendario sin cumplir
    for v in session.query(Vencimiento).filter(Vencimiento.cumplido.is_(False)):
        f = fila_para(v.fecha)
        if f is None:
            continue
        valor = CERO
        if v.obligacion.startswith("Recibo 2593") and v.periodo:
            try:
                bim = int(v.periodo.split()[1])
                anio = int(v.periodo.split()[-1])
                rec = impuestos.recibo_2593(session, anio, bim)
                valor = max(rec.total - rec.pagado, CERO)
            except (ValueError, IndexError):
                valor = CERO
        f.impuestos += valor
        f.detalle_impuestos.append((v.obligacion + (" (VENCIDO)" if v.fecha < hoy else ""), v.periodo, valor))
    # Gastos recurrentes: promedio mensual de los últimos 3 meses de gastos de contado (sin activos)
    desde = _sumar_mes(primero, -3)
    total3 = CERO
    for g in session.query(Gasto).filter(Gasto.fecha >= desde, Gasto.fecha < primero, Gasto.forma_pago == "contado"):
        if not g.categoria.cuenta.startswith("15"):
            total3 += g.signo * g.total
    promedio = contab.redondear(total3 / 3)
    retainers = sum((t.retainer_mensual for t in _clientes_retainer(session)), CERO)
    for i, f in enumerate(filas):
        f.gastos_recurrentes = promedio
        if i > 0:
            f.retainers = retainers
    saldo = reportes.saldo_cuenta(session, "11", hoy)
    for f in filas:
        f.saldo_inicial = saldo
        saldo = f.saldo_final
    return filas, promedio


def _clientes_retainer(session):
    from .models import Tercero
    return session.query(Tercero).filter(Tercero.retainer_mensual.isnot(None), Tercero.retainer_mensual > 0).all()


# ------------------------------------------------------------------ depreciación

def activos(session):
    return [g for g in session.query(Gasto).order_by(Gasto.fecha)
            if g.categoria.cuenta.startswith("15") and g.tipo_soporte != "NC"]


def costo_activo(session, g):
    """Costo depreciable: valor sin IVA descontable, menos notas crédito del mismo proveedor y categoría."""
    nc = sum((n.total - n.iva_desc_valor for n in session.query(Gasto)
              .filter(Gasto.tipo_soporte == "NC", Gasto.proveedor_id == g.proveedor_id,
                      Gasto.categoria_id == g.categoria_id, Gasto.fecha >= g.fecha)), CERO)
    return g.total - g.iva_desc_valor - nc


def depreciacion_acumulada(session, g):
    return sum((m.credito for a in session.query(Asiento).filter(Asiento.origen.like(f"depre:{g.id}:%"))
                for m in a.lineas if m.cuenta.startswith("1592")), CERO)


def borrar_depreciaciones(session, gasto_id, forzar=False):
    for a in session.query(Asiento).filter(Asiento.origen.like(f"depre:{gasto_id}:%")).all():
        contab.verificar_periodo(session, a.fecha, forzar)
        session.delete(a)
    session.flush()


def causar_depreciaciones(session, hasta: date | None = None, forzar=False):
    """Crea el asiento mensual de depreciación de cada activo hasta el mes indicado (línea recta).

    Cada cuota nueva reparte lo que falta por depreciar entre las cuotas que quedan, de modo que un
    cambio de vida útil o una nota crédito posterior se absorben sin descuadrar el total.
    Devuelve (creados, omitidos por periodo bloqueado).
    """
    hasta = hasta or date.today()
    creados = omitidos = 0
    for g in activos(session):
        grupo = g.categoria.cuenta[:4]
        if grupo not in CTA_DEPRECIACION_GASTO:
            continue
        vida = max(1, g.vida_util_meses or VIDA_UTIL_DEFECTO[grupo])
        costo = costo_activo(session, g)
        acumulado = depreciacion_acumulada(session, g)
        existentes = {a.origen for a in session.query(Asiento).filter(Asiento.origen.like(f"depre:{g.id}:%"))}
        mes = _sumar_mes(g.fecha.replace(day=1), 1)  # se deprecia desde el mes siguiente a la compra
        n = 0
        while n < vida and _fin_mes(mes) <= hasta:
            n += 1
            origen = f"depre:{g.id}:{mes:%Y%m}"
            if origen not in existentes:
                restante = costo - acumulado
                valor = restante if n == vida else contab.redondear(restante / (vida - n + 1))
                if valor <= 0:
                    break
                try:
                    contab.guardar_asiento(
                        session, origen=origen, tipo="AJ", fecha=_fin_mes(mes),
                        descripcion=f"Depreciación {mes:%m/%Y} {g.descripcion or g.categoria.nombre}"[:250],
                        tercero_id=None, forzar=forzar,
                        lineas=[(CTA_DEPRECIACION_GASTO[grupo], valor, 0, None, None),
                                (CTA_DEPRECIACION_ACUM[grupo], 0, valor, None, None)])
                    creados += 1
                    acumulado += valor
                except contab.ErrorContable:
                    omitidos += 1  # periodo bloqueado: se deja como está
            mes = _sumar_mes(mes, 1)
    session.commit()
    return creados, omitidos


def resumen_activos(session):
    filas = []
    for g in activos(session):
        grupo = g.categoria.cuenta[:4]
        vida = max(1, g.vida_util_meses or VIDA_UTIL_DEFECTO.get(grupo, 60))
        costo = costo_activo(session, g)
        depreciado = depreciacion_acumulada(session, g)
        filas.append({"gasto": g, "costo": costo, "vida": vida, "cuota": contab.redondear(costo / vida),
                      "depreciado": depreciado, "neto": costo - depreciado})
    return filas


# ------------------------------------------------------------------ cierre de ejercicio

def cerrar_anio(session, anio):
    """Asiento de cierre al 31-12: cancela ingresos y gastos contra 360505/361005 y bloquea el año."""
    fin = date(anio, 12, 31)
    sumas = reportes._sumas_por_cuenta(session, date(anio, 1, 1), fin, incluir_cierre=False)
    lineas = []
    resultado = CERO
    for cuenta, (dbt, cr) in sorted(sumas.items()):
        if cuenta[0] not in "456":
            continue
        saldo = dbt - cr
        if saldo > 0:
            lineas.append((cuenta, 0, saldo, None, "Cierre"))
        elif saldo < 0:
            lineas.append((cuenta, -saldo, 0, None, "Cierre"))
        resultado -= saldo
    if resultado > 0:
        lineas.append(("370505", 0, resultado, None, "Utilidad del ejercicio trasladada a acumuladas"))
    elif resultado < 0:
        lineas.append(("371005", -resultado, 0, None, "Pérdida del ejercicio trasladada a acumuladas"))
    if not lineas:
        raise contab.ErrorContable(f"El año {anio} no tiene ingresos ni gastos registrados; no hay nada que cerrar.")
    contab.guardar_asiento(session, origen=f"cierre:{anio}", tipo="CI", fecha=fin,
                           descripcion=f"Cierre del ejercicio {anio}", tercero_id=None, lineas=lineas, forzar=True)
    tope = contab.periodo_bloqueado_hasta(session)
    if tope is None or fin > tope:
        contab.set_config(session, "periodo_bloqueado_hasta", fin.isoformat())
    session.commit()
    return resultado


def anio_cerrado(session, anio):
    return session.query(Asiento).filter_by(origen=f"cierre:{anio}").first() is not None


def reabrir_anio(session, anio):
    contab.borrar_asientos(session, f"cierre:{anio}", forzar=True)
    session.commit()
