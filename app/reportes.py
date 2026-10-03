"""Libros y estados financieros."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from . import contab
from .models import CERO, Asiento, Cuenta, Gasto, Movimiento

NIVELES = (1, 2, 4, 6, 8)


def _sumas_por_cuenta(session, desde=None, hasta=None):
    q = (session.query(Movimiento.cuenta, func.sum(Movimiento.debito), func.sum(Movimiento.credito))
         .join(Asiento))
    if desde:
        q = q.filter(Asiento.fecha >= desde)
    if hasta:
        q = q.filter(Asiento.fecha <= hasta)
    return {c: (contab.redondear(dbt or 0), contab.redondear(cr or 0)) for c, dbt, cr in q.group_by(Movimiento.cuenta)}


@dataclass
class FilaBalance:
    cuenta: Cuenta
    saldo_inicial: Decimal
    debitos: Decimal
    creditos: Decimal

    @property
    def saldo_final(self):
        mov = self.debitos - self.creditos
        return self.saldo_inicial + (mov if self.cuenta.naturaleza == "D" else -mov)

    @property
    def nivel(self):
        return len(self.cuenta.codigo)


def balance_de_prueba(session, desde: date, hasta: date, nivel_max=8):
    cuentas = {c.codigo: c for c in session.query(Cuenta)}
    previas = _sumas_por_cuenta(session, hasta=date.fromordinal(desde.toordinal() - 1))
    periodo = _sumas_por_cuenta(session, desde, hasta)
    agregados = defaultdict(lambda: [CERO, CERO, CERO, CERO])  # deb_prev, cre_prev, deb, cre
    for origen, idx in ((previas, 0), (periodo, 2)):
        for codigo, (dbt, cr) in origen.items():
            for n in NIVELES:
                if len(codigo) >= n:
                    a = agregados[codigo[:n]]
                    a[idx] += dbt
                    a[idx + 1] += cr
    filas = []
    for codigo in sorted(agregados):
        if codigo not in cuentas or len(codigo) > nivel_max:
            continue
        dp, cp, dbt, cr = agregados[codigo]
        c = cuentas[codigo]
        inicial = dp - cp if c.naturaleza == "D" else cp - dp
        # Las clases 4, 5 y 6 se cierran cada año: el saldo inicial solo cuenta desde el 1 de enero.
        if codigo[0] in "456" and desde.month == 1 and desde.day == 1:
            inicial = CERO
        elif codigo[0] in "456":
            ini_anio = _sumas_por_cuenta(session, date(desde.year, 1, 1), date.fromordinal(desde.toordinal() - 1))
            dp2 = sum((v[0] for k, v in ini_anio.items() if k.startswith(codigo)), CERO)
            cp2 = sum((v[1] for k, v in ini_anio.items() if k.startswith(codigo)), CERO)
            inicial = dp2 - cp2 if c.naturaleza == "D" else cp2 - dp2
        fila = FilaBalance(c, inicial, dbt, cr)
        if fila.saldo_inicial or fila.debitos or fila.creditos:
            filas.append(fila)
    tot_d = sum((f.debitos for f in filas if f.nivel == 1), CERO)
    tot_c = sum((f.creditos for f in filas if f.nivel == 1), CERO)
    return filas, tot_d, tot_c


def _saldo_prefijo(sumas, prefijo, naturaleza):
    dbt = sum((v[0] for k, v in sumas.items() if k.startswith(prefijo)), CERO)
    cr = sum((v[1] for k, v in sumas.items() if k.startswith(prefijo)), CERO)
    return dbt - cr if naturaleza == "D" else cr - dbt


def _detalle(session, sumas, prefijos, nivel=4):
    """Filas (cuenta, saldo) agrupadas al nivel indicado, para las cuentas bajo los prefijos."""
    cuentas = {c.codigo: c for c in session.query(Cuenta)}
    grupos = defaultdict(lambda: CERO)
    for codigo in sumas:
        if any(codigo.startswith(p) for p in prefijos):
            grupos[codigo[:nivel]] = CERO
    filas = []
    for codigo in sorted(grupos):
        c = cuentas.get(codigo)
        if c is None:
            continue
        nat = cuentas[codigo[0]].naturaleza
        saldo = _saldo_prefijo(sumas, codigo, nat)
        if saldo:
            filas.append((c, saldo))
    return filas


def estado_resultados(session, desde: date, hasta: date):
    s = _sumas_por_cuenta(session, desde, hasta)
    ingresos_op = _saldo_prefijo(s, "41", "C")
    ingresos_no_op = _saldo_prefijo(s, "42", "C")
    gastos_admin = _saldo_prefijo(s, "51", "D")
    gastos_ventas = _saldo_prefijo(s, "52", "D")
    gastos_no_op = _saldo_prefijo(s, "53", "D")
    impuesto = _saldo_prefijo(s, "54", "D")
    utilidad_operacional = ingresos_op - gastos_admin - gastos_ventas
    antes_impuestos = utilidad_operacional + ingresos_no_op - gastos_no_op
    return {
        "ingresos_op": ingresos_op, "det_ingresos_op": _detalle(session, s, ["41"]),
        "gastos_admin": gastos_admin, "det_gastos_admin": _detalle(session, s, ["51"]),
        "gastos_ventas": gastos_ventas, "det_gastos_ventas": _detalle(session, s, ["52"]),
        "utilidad_operacional": utilidad_operacional,
        "ingresos_no_op": ingresos_no_op, "det_ingresos_no_op": _detalle(session, s, ["42"]),
        "gastos_no_op": gastos_no_op, "det_gastos_no_op": _detalle(session, s, ["53"]),
        "antes_impuestos": antes_impuestos,
        "impuesto": impuesto,
        "utilidad_neta": antes_impuestos - impuesto,
    }


def balance_general(session, corte: date):
    s = _sumas_por_cuenta(session, hasta=corte)
    activo = _saldo_prefijo(s, "1", "D")
    pasivo = _saldo_prefijo(s, "2", "C")
    patrimonio = _saldo_prefijo(s, "3", "C")
    # Resultado del ejercicio en curso y de años anteriores aún no trasladados a patrimonio
    resultado = (_saldo_prefijo(s, "4", "C") - _saldo_prefijo(s, "5", "D") - _saldo_prefijo(s, "6", "D"))
    return {
        "activo": activo, "det_activo": _detalle(session, s, ["1"]),
        "pasivo": pasivo, "det_pasivo": _detalle(session, s, ["2"]),
        "patrimonio": patrimonio, "det_patrimonio": _detalle(session, s, ["3"]),
        "resultado": resultado,
        "total_patrimonio": patrimonio + resultado,
        "cuadre": activo - pasivo - patrimonio - resultado,
    }


def libro_diario(session, desde, hasta, tipo=None):
    q = session.query(Asiento).filter(Asiento.fecha.between(desde, hasta))
    if tipo:
        q = q.filter(Asiento.tipo == tipo)
    return q.order_by(Asiento.fecha, Asiento.tipo, Asiento.numero).all()


def libro_mayor(session, cuenta: str, desde, hasta, tercero_id=None):
    c = session.get(Cuenta, cuenta)
    q_prev = (session.query(func.sum(Movimiento.debito), func.sum(Movimiento.credito)).join(Asiento)
              .filter(Movimiento.cuenta.like(f"{cuenta}%"), Asiento.fecha < desde))
    q = (session.query(Movimiento).join(Asiento)
         .filter(Movimiento.cuenta.like(f"{cuenta}%"), Asiento.fecha.between(desde, hasta)))
    if tercero_id:
        q_prev = q_prev.filter(Movimiento.tercero_id == tercero_id)
        q = q.filter(Movimiento.tercero_id == tercero_id)
    dp, cp = q_prev.one()
    dp, cp = contab.redondear(dp or 0), contab.redondear(cp or 0)
    nat = c.naturaleza if c else "D"
    saldo = dp - cp if nat == "D" else cp - dp
    inicial = saldo
    filas = []
    for m in q.order_by(Asiento.fecha, Asiento.id, Movimiento.id):
        saldo += (m.debito - m.credito) if nat == "D" else (m.credito - m.debito)
        filas.append((m, saldo))
    return c, inicial, filas, saldo


def saldo_cuenta(session, prefijo, corte=None):
    q = (session.query(func.sum(Movimiento.debito), func.sum(Movimiento.credito)).join(Asiento)
         .filter(Movimiento.cuenta.like(f"{prefijo}%")))
    if corte:
        q = q.filter(Asiento.fecha <= corte)
    dbt, cr = q.one()
    return contab.redondear((dbt or 0)) - contab.redondear((cr or 0))


def serie_mensual(session, anio):
    """Ingresos operacionales y gastos (clases 51-53) por mes."""
    filas = []
    for mes in range(1, 13):
        inicio = date(anio, mes, 1)
        fin = date(anio + (mes == 12), (mes % 12) + 1, 1)
        s = _sumas_por_cuenta(session, inicio, date.fromordinal(fin.toordinal() - 1))
        ingresos = _saldo_prefijo(s, "4", "C")
        gastos = sum((_saldo_prefijo(s, p, "D") for p in ("51", "52", "53")), CERO)
        filas.append((mes, ingresos, gastos))
    return filas


def gastos_por_categoria(session, desde, hasta):
    totales = defaultdict(lambda: CERO)
    for g in session.query(Gasto).filter(Gasto.fecha.between(desde, hasta)):
        totales[g.categoria.nombre] += g.signo * (g.total - g.iva_desc_valor)
    return sorted(((k, v) for k, v in totales.items() if v), key=lambda x: -x[1])
