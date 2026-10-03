"""Informes gerenciales y financieros adicionales: flujo de efectivo, cambios en el patrimonio, comparativos,
estado de resultados mensual, indicadores, cuentas por pagar por edades, gastos por proveedor, rentabilidad por
cliente y asunto, presupuesto, comparación SIMPLE vs régimen ordinario e informe de conciliación bancaria."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import selectinload

from . import cartera, contab, impuestos, reportes
from .models import (CERO, Asiento, Asunto, Banco, CategoriaGasto, Cuenta, DocumentoVenta, Gasto, Movimiento,
                     MovimientoBanco, Presupuesto)

PREFIJO_EFECTIVO = "11"


def _dia_anterior(f: date) -> date:
    return f - timedelta(days=1)


def _pct(parte, total):
    if not total:
        return None
    return (Decimal(parte) / Decimal(total) * 100).quantize(Decimal("0.1"))


# ------------------------------------------------------------------ flujo de efectivo (método directo)

CONCEPTOS_FLUJO = [
    # (clave, nombre, actividad, prefijos de la contrapartida)
    ("clientes", "Recaudos de clientes", "operacion", ("13", "28")),
    ("otros_ingresos", "Otros ingresos recibidos (intereses, reintegros)", "operacion", ("4",)),
    ("proveedores", "Pagos a proveedores y gastos", "operacion", ("22", "23", "25", "26", "5", "6")),
    ("impuestos", "Impuestos pagados (SIMPLE, IVA)", "operacion", ("24", "1355")),
    ("activos", "Compra de activos fijos", "inversion", ("15", "16", "12")),
    ("socios", "Socios: aportes, préstamos y reembolsos", "financiacion", ("21", "31", "32", "33", "36", "37")),
]
ACTIVIDADES = [("operacion", "Actividades de operación"), ("inversion", "Actividades de inversión"),
               ("financiacion", "Actividades de financiación"), ("otros", "Otros movimientos")]


def flujo_de_efectivo(session, desde: date, hasta: date):
    """Entradas y salidas de caja y bancos clasificadas por la contrapartida de cada asiento.

    En un asiento cuadrado, la variación del efectivo es igual a (créditos − débitos) de las demás líneas,
    así que cada contrapartida explica exactamente su parte del movimiento, incluso en asientos mixtos."""
    conceptos = {c[0]: CERO for c in CONCEPTOS_FLUJO}
    conceptos["otros"] = CERO
    # Préstamos del socio (2355) son financiación aunque 23 esté en "proveedores"
    asientos = (session.query(Asiento).options(selectinload(Asiento.lineas))
                .join(Movimiento).filter(Asiento.fecha.between(desde, hasta), Asiento.tipo != "CI",
                                         Movimiento.cuenta.like(f"{PREFIJO_EFECTIVO}%")).distinct().all())
    for a in asientos:
        efectivo = sum((m.debito - m.credito for m in a.lineas if m.cuenta.startswith(PREFIJO_EFECTIVO)), CERO)
        if efectivo == 0:  # traslado entre cuentas propias
            continue
        for m in a.lineas:
            if m.cuenta.startswith(PREFIJO_EFECTIVO):
                continue
            aporte = m.credito - m.debito
            if m.cuenta.startswith("2355"):
                conceptos["socios"] += aporte
                continue
            for clave, _, _, prefijos in CONCEPTOS_FLUJO:
                if any(m.cuenta.startswith(p) for p in prefijos):
                    conceptos[clave] += aporte
                    break
            else:
                conceptos["otros"] += aporte
    saldo_inicial = reportes.saldo_cuenta(session, PREFIJO_EFECTIVO, _dia_anterior(desde))
    saldo_final = reportes.saldo_cuenta(session, PREFIJO_EFECTIVO, hasta)
    secciones = []
    for clave_act, nombre_act in ACTIVIDADES:
        filas = [(nombre, conceptos[clave]) for clave, nombre, act, _ in CONCEPTOS_FLUJO if act == clave_act]
        if clave_act == "otros":
            filas = [("Otros movimientos", conceptos["otros"])]
        filas = [f for f in filas if f[1]]
        total = sum((v for _, v in filas), CERO)
        if filas:
            secciones.append({"nombre": nombre_act, "filas": filas, "total": total})
    neto = sum((s["total"] for s in secciones), CERO)
    return {"secciones": secciones, "neto": neto, "saldo_inicial": saldo_inicial, "saldo_final": saldo_final,
            "cuadre": saldo_inicial + neto - saldo_final}


# ------------------------------------------------------------------ cambios en el patrimonio

def cambios_patrimonio(session, anio: int):
    """Saldo inicial, aumentos, disminuciones y saldo final de cada cuenta del patrimonio más el resultado."""
    desde, hasta = date(anio, 1, 1), date(anio, 12, 31)
    cuentas = {c.codigo: c for c in session.query(Cuenta).filter(Cuenta.codigo.like("3%"))}
    previas = reportes._sumas_por_cuenta(session, hasta=_dia_anterior(desde))
    periodo = reportes._sumas_por_cuenta(session, desde, hasta)
    grupos = sorted({c[:4] for c in list(previas) + list(periodo) if c.startswith("3")})
    filas = []
    for g in grupos:
        c = cuentas.get(g)
        if c is None:
            continue
        inicial = reportes._saldo_prefijo(previas, g, "C")
        aumentos = sum((v[1] for k, v in periodo.items() if k.startswith(g)), CERO)
        disminuciones = sum((v[0] for k, v in periodo.items() if k.startswith(g)), CERO)
        if inicial or aumentos or disminuciones:
            filas.append({"cuenta": f"{c.codigo} {c.nombre}", "inicial": inicial, "aumentos": aumentos,
                          "disminuciones": disminuciones, "final": inicial + aumentos - disminuciones})
    anteriores = reportes.resultado_acumulado(session, _dia_anterior(desde))
    if anteriores and not any(f["cuenta"].startswith("3705") or f["cuenta"].startswith("3710") for f in filas):
        filas.append({"cuenta": "Resultados de ejercicios anteriores (sin cerrar)", "inicial": anteriores,
                      "aumentos": CERO, "disminuciones": CERO, "final": anteriores})
    er = reportes.estado_resultados(session, desde, hasta)
    resultado = er["utilidad_neta"]
    filas.append({"cuenta": "Resultado del ejercicio", "inicial": CERO, "aumentos": max(resultado, CERO),
                  "disminuciones": max(-resultado, CERO), "final": resultado})
    total = {k: sum((f[k] for f in filas), CERO) for k in ("inicial", "aumentos", "disminuciones", "final")}
    return filas, total


# ------------------------------------------------------------------ comparativos y análisis

def periodo_comparable(desde: date, hasta: date, modo="anio"):
    """'anio': mismas fechas del año anterior; 'anterior': el periodo inmediatamente anterior de igual duración."""
    if modo == "anterior":
        dias = (hasta - desde).days
        h = _dia_anterior(desde)
        return h - timedelta(days=dias), h
    try:
        return desde.replace(year=desde.year - 1), hasta.replace(year=hasta.year - 1)
    except ValueError:  # 29 de febrero
        return desde.replace(year=desde.year - 1, day=28), hasta.replace(year=hasta.year - 1, day=28)


def _filas_er(er):
    """Filas (etiqueta, valor, estilo) del estado de resultados, con el detalle por cuenta."""
    filas = [("Ingresos operacionales", er["ingresos_op"], "grupo")]
    filas += [(f"   {c.codigo} {c.nombre}", v, "") for c, v in er["det_ingresos_op"]]
    filas.append(("(−) Gastos de administración", er["gastos_admin"], "grupo"))
    filas += [(f"   {c.codigo} {c.nombre}", v, "") for c, v in er["det_gastos_admin"]]
    if er["gastos_ventas"]:
        filas.append(("(−) Gastos de ventas", er["gastos_ventas"], "grupo"))
    filas.append(("Utilidad operacional", er["utilidad_operacional"], "total"))
    filas.append(("(+) Ingresos no operacionales", er["ingresos_no_op"], "grupo"))
    filas.append(("(−) Gastos no operacionales", er["gastos_no_op"], "grupo"))
    filas.append(("Utilidad antes de impuestos", er["antes_impuestos"], "total"))
    filas.append(("(−) Impuesto SIMPLE", er["impuesto"], ""))
    filas.append(("Utilidad neta", er["utilidad_neta"], "total"))
    return filas


def comparativo_resultados(session, desde, hasta, modo="anio"):
    """Estado de resultados del periodo frente al comparable, con variación absoluta y relativa (análisis
    horizontal) y participación sobre los ingresos (análisis vertical)."""
    d2, h2 = periodo_comparable(desde, hasta, modo)
    actual, anterior = reportes.estado_resultados(session, desde, hasta), reportes.estado_resultados(session, d2, h2)
    a = {k: v for k, v, _ in _filas_er(actual)}
    b = {k: v for k, v, _ in _filas_er(anterior)}
    estilos = {k: e for k, _, e in _filas_er(actual)}
    estilos.update({k: e for k, _, e in _filas_er(anterior) if k not in estilos})
    orden = [k for k, _, _ in _filas_er(actual)]
    for k, _, _ in _filas_er(anterior):
        if k not in orden:
            # insertar las cuentas del periodo anterior junto a su grupo
            orden.append(k)
    base_a, base_b = actual["ingresos_op"] + actual["ingresos_no_op"], anterior["ingresos_op"] + anterior["ingresos_no_op"]
    filas = []
    for k in orden:
        va, vb = a.get(k, CERO), b.get(k, CERO)
        if not va and not vb:
            continue
        filas.append({"concepto": k, "actual": va, "anterior": vb, "variacion": va - vb,
                      "variacion_pct": _pct(va - vb, abs(vb)) if vb else None,
                      "vertical_actual": _pct(va, base_a), "vertical_anterior": _pct(vb, base_b),
                      "estilo": estilos.get(k, "")})
    return {"filas": filas, "desde": desde, "hasta": hasta, "desde2": d2, "hasta2": h2}


def comparativo_balance(session, corte, modo="anio"):
    corte2 = periodo_comparable(corte, corte, modo)[1]
    a, b = reportes.balance_general(session, corte), reportes.balance_general(session, corte2)

    def filas_de(bg):
        filas = [("ACTIVO", bg["activo"], "grupo")]
        filas += [(f"   {c.codigo} {c.nombre}", v, "") for c, v in bg["det_activo"]]
        filas.append(("PASIVO", bg["pasivo"], "grupo"))
        filas += [(f"   {c.codigo} {c.nombre}", v, "") for c, v in bg["det_pasivo"]]
        filas.append(("PATRIMONIO", bg["total_patrimonio"], "grupo"))
        filas += [(f"   {c.codigo} {c.nombre}", v, "") for c, v in bg["det_patrimonio"]]
        filas.append(("   Resultados de ejercicios anteriores", bg["anteriores"], ""))
        filas.append(("   Resultado del ejercicio", bg["resultado"], ""))
        filas.append(("Total pasivo y patrimonio", bg["pasivo"] + bg["total_patrimonio"], "total"))
        return filas
    fa, fb = filas_de(a), filas_de(b)
    da, db = {k: v for k, v, _ in fa}, {k: v for k, v, _ in fb}
    estilos = {k: e for k, _, e in fa + fb}
    orden = [k for k, _, _ in fa] + [k for k, _, _ in fb if k not in da]
    filas = []
    for k in orden:
        va, vb = da.get(k, CERO), db.get(k, CERO)
        if not va and not vb:
            continue
        filas.append({"concepto": k, "actual": va, "anterior": vb, "variacion": va - vb,
                      "variacion_pct": _pct(va - vb, abs(vb)) if vb else None,
                      "vertical_actual": _pct(va, a["activo"]), "vertical_anterior": _pct(vb, b["activo"]),
                      "estilo": estilos.get(k, "")})
    return {"filas": filas, "corte": corte, "corte2": corte2}


def resultados_mensuales(session, anio: int):
    """Estado de resultados con una columna por mes y el total del año."""
    meses = []
    for mes in range(1, 13):
        inicio = date(anio, mes, 1)
        fin = _dia_anterior(date(anio + (mes == 12), (mes % 12) + 1, 1))
        meses.append(reportes.estado_resultados(session, inicio, fin))
    anual = reportes.estado_resultados(session, date(anio, 1, 1), date(anio, 12, 31))
    claves = []
    for er in meses + [anual]:
        for k, _, _ in _filas_er(er):
            if k not in claves:
                claves.append(k)
    estilos = {k: e for er in meses + [anual] for k, _, e in _filas_er(er)}
    filas = []
    for k in claves:
        valores = [dict((kk, vv) for kk, vv, _ in _filas_er(er)).get(k, CERO) for er in meses]
        total = dict((kk, vv) for kk, vv, _ in _filas_er(anual)).get(k, CERO)
        if any(valores) or total:
            filas.append({"concepto": k, "meses": valores, "total": total, "estilo": estilos.get(k, "")})
    return filas


# ------------------------------------------------------------------ indicadores

def indicadores(session, anio: int, corte: date | None = None):
    corte = corte or date.today()
    if corte.year != anio:
        corte = date(anio, 12, 31)
    inicio = date(anio, 1, 1)
    er = reportes.estado_resultados(session, inicio, corte)
    bg = reportes.balance_general(session, corte)
    sumas = reportes._sumas_por_cuenta(session, hasta=corte)
    activo_corriente = sum((reportes._saldo_prefijo(sumas, p, "D") for p in ("11", "12", "13")), CERO)
    pasivo_corriente = sum((reportes._saldo_prefijo(sumas, p, "C") for p in ("21", "22", "23", "24", "25", "28")), CERO)
    ingresos = er["ingresos_op"] + er["ingresos_no_op"]
    gastos = er["gastos_admin"] + er["gastos_ventas"] + er["gastos_no_op"]
    dias = (corte - inicio).days + 1
    _, tot_cartera = cartera.cartera_por_edades(session, corte)
    cartera_total = tot_cartera["total"]
    vencida = cartera_total - tot_cartera["Por vencer"]
    ingresos_dia = ingresos / Decimal(dias) if dias else CERO
    dso = (cartera_total / ingresos_dia).quantize(Decimal("1")) if ingresos_dia else None
    meses = Decimal(corte.month) if corte.year == anio else Decimal(12)
    er_ant = reportes.estado_resultados(session, *periodo_comparable(inicio, corte))
    ingresos_ant = er_ant["ingresos_op"] + er_ant["ingresos_no_op"]
    proy = impuestos.proyeccion_anual(session, anio, corte)
    efectivo = reportes.saldo_cuenta(session, PREFIJO_EFECTIVO, corte)
    gasto_mensual = gastos / meses if meses else CERO
    return [
        {"grupo": "Rentabilidad", "nombre": "Margen operacional", "valor": _pct(er["utilidad_operacional"], ingresos),
         "formato": "pct", "ayuda": "Utilidad operacional / ingresos. Cuánto queda de cada peso facturado después de los gastos del despacho."},
        {"grupo": "Rentabilidad", "nombre": "Margen neto", "valor": _pct(er["utilidad_neta"], ingresos), "formato": "pct",
         "ayuda": "Utilidad neta / ingresos (ya con el impuesto SIMPLE causado)."},
        {"grupo": "Rentabilidad", "nombre": "Gastos sobre ingresos", "valor": _pct(gastos, ingresos), "formato": "pct",
         "ayuda": "Qué porcentaje de los ingresos se va en gastos."},
        {"grupo": "Rentabilidad", "nombre": "Crecimiento de ingresos vs. año anterior",
         "valor": _pct(ingresos - ingresos_ant, ingresos_ant) if ingresos_ant else None, "formato": "pct",
         "ayuda": "Mismo periodo del año anterior."},
        {"grupo": "Rentabilidad", "nombre": "Ingreso promedio mensual", "valor": (ingresos / meses).quantize(Decimal("1")) if meses else CERO,
         "formato": "pesos", "ayuda": f"Ingresos de {anio} divididos por los meses transcurridos."},
        {"grupo": "Rentabilidad", "nombre": "Punto de equilibrio mensual", "valor": gasto_mensual.quantize(Decimal("1")),
         "formato": "pesos", "ayuda": "Gasto promedio mensual: lo mínimo que hay que facturar al mes para no perder."},
        {"grupo": "Liquidez", "nombre": "Razón corriente", "valor": (activo_corriente / pasivo_corriente).quantize(Decimal("0.01")) if pasivo_corriente else None,
         "formato": "veces", "ayuda": "Activo corriente / pasivo corriente. Mayor que 1 significa que hay con qué pagar lo que vence pronto."},
        {"grupo": "Liquidez", "nombre": "Efectivo disponible", "valor": efectivo, "formato": "pesos", "ayuda": "Caja y bancos a la fecha."},
        {"grupo": "Liquidez", "nombre": "Meses de gastos cubiertos", "valor": (efectivo / gasto_mensual).quantize(Decimal("0.1")) if gasto_mensual else None,
         "formato": "veces", "ayuda": "Efectivo / gasto promedio mensual: cuántos meses aguanta el despacho sin facturar."},
        {"grupo": "Cartera", "nombre": "Días promedio de cobro (DSO)", "valor": dso, "formato": "dias",
         "ayuda": "Cartera pendiente / ingreso diario promedio. Cuántos días tardan en pagarle en promedio."},
        {"grupo": "Cartera", "nombre": "Cartera vencida sobre total", "valor": _pct(vencida, cartera_total), "formato": "pct",
         "ayuda": "Parte de la cartera que ya pasó su fecha de vencimiento."},
        {"grupo": "Impuestos", "nombre": "Carga tributaria estimada (SIMPLE + IVA neto)", "valor": _pct(proy.carga_total, proy.ingresos_proyectados) if proy.ingresos_proyectados else None,
         "formato": "pct", "ayuda": "Impuestos proyectados del año sobre los ingresos proyectados."},
        {"grupo": "Impuestos", "nombre": "Tarifa SIMPLE aplicable", "valor": (proy.tarifa * 100).quantize(Decimal("0.1")), "formato": "pct",
         "ayuda": "Según los ingresos proyectados del año (art. 908 E.T.)."},
        {"grupo": "Estructura", "nombre": "Endeudamiento", "valor": _pct(bg["pasivo"], bg["activo"]), "formato": "pct",
         "ayuda": "Pasivo / activo."},
    ]


# ------------------------------------------------------------------ cuentas por pagar

EDADES_CXP = cartera.EDADES


def saldo_gasto_al(g: Gasto, al: date) -> Decimal:
    if g.tipo_soporte == "NC" or g.forma_pago != "credito":
        return CERO
    pagado = sum((p.valor for p in g.pagos if p.fecha <= al), CERO)
    notas = sum((n.total for n in g.notas_credito if n.fecha <= al), CERO)
    return g.total - pagado - notas


def facturas_por_pagar(session, al: date | None = None, proveedor_id=None):
    al = al or date.today()
    q = (session.query(Gasto).options(selectinload(Gasto.pagos), selectinload(Gasto.notas_credito),
                                      selectinload(Gasto.proveedor), selectinload(Gasto.categoria))
         .filter(Gasto.forma_pago == "credito", Gasto.tipo_soporte != "NC", Gasto.fecha <= al))
    if proveedor_id:
        q = q.filter(Gasto.proveedor_id == proveedor_id)
    res = []
    for g in q.order_by(Gasto.fecha, Gasto.id):
        saldo = saldo_gasto_al(g, al)
        if saldo > 0:
            res.append((g, saldo))
    return res


def cuentas_por_pagar_edades(session, al: date | None = None):
    al = al or date.today()
    proveedores = {}
    for g, saldo in facturas_por_pagar(session, al):
        clave = g.proveedor_id or 0
        fila = proveedores.setdefault(clave, {"proveedor": g.proveedor, "total": CERO, **{e[0]: CERO for e in EDADES_CXP}})
        dias = (al - (g.vencimiento or g.fecha)).days
        for nombre, desde, hasta in EDADES_CXP:
            if desde is None and dias <= 0 or desde is not None and dias >= desde and (hasta is None or dias <= hasta):
                fila[nombre] += saldo
                break
        fila["total"] += saldo
    filas = sorted(proveedores.values(), key=lambda f: -f["total"])
    totales = {"total": sum((f["total"] for f in filas), CERO)}
    for e in EDADES_CXP:
        totales[e[0]] = sum((f[e[0]] for f in filas), CERO)
    return filas, totales


def gastos_por_proveedor(session, desde, hasta):
    totales = defaultdict(lambda: {"total": CERO, "iva": CERO, "n": 0, "proveedor": None})
    for g in (session.query(Gasto).options(selectinload(Gasto.proveedor))
              .filter(Gasto.fecha.between(desde, hasta))):
        f = totales[g.proveedor_id or 0]
        f["proveedor"] = g.proveedor
        f["total"] += g.signo * (g.total - g.iva_desc_valor)
        f["iva"] += g.signo * g.iva_desc_valor
        f["n"] += 1 if g.tipo_soporte != "NC" else 0
    filas = sorted((f for f in totales.values() if f["total"] or f["iva"]), key=lambda f: -f["total"])
    total = sum((f["total"] for f in filas), CERO)
    for f in filas:
        f["pct"] = _pct(f["total"], total)
    return filas, total


def gastos_por_categoria_mensual(session, anio: int):
    tabla = defaultdict(lambda: [CERO] * 12)
    for g in session.query(Gasto).options(selectinload(Gasto.categoria)).filter(
            Gasto.fecha.between(date(anio, 1, 1), date(anio, 12, 31))):
        tabla[g.categoria.nombre][g.fecha.month - 1] += g.signo * (g.total - g.iva_desc_valor)
    filas = [(nombre, meses, sum(meses, CERO)) for nombre, meses in tabla.items()]
    filas.sort(key=lambda f: -f[2])
    totales = [sum((f[1][i] for f in filas), CERO) for i in range(12)]
    return filas, totales, sum(totales, CERO)


# ------------------------------------------------------------------ rentabilidad por cliente y asunto

def rentabilidad(session, desde, hasta):
    """Ingresos (sin IVA, netos de NC) menos gastos imputados a cada cliente/asunto."""
    por_cliente = defaultdict(lambda: {"ingresos": CERO, "gastos": CERO, "reembolsables": CERO, "asuntos": {}})
    ventas = (session.query(DocumentoVenta).options(selectinload(DocumentoVenta.cliente), selectinload(DocumentoVenta.asunto))
              .filter(DocumentoVenta.fecha.between(desde, hasta), DocumentoVenta.anulada.is_(False)))
    clientes = {}
    for v in ventas:
        clientes[v.cliente_id] = v.cliente
        c = por_cliente[v.cliente_id]
        c["ingresos"] += v.signo * v.ingreso
        if v.asunto_id:
            a = c["asuntos"].setdefault(v.asunto_id, {"asunto": v.asunto, "ingresos": CERO, "gastos": CERO})
            a["ingresos"] += v.signo * v.ingreso
    gastos = (session.query(Gasto).options(selectinload(Gasto.asunto).selectinload(Asunto.cliente))
              .filter(Gasto.fecha.between(desde, hasta), Gasto.asunto_id.isnot(None)))
    for g in gastos:
        cid = g.asunto.cliente_id
        clientes.setdefault(cid, g.asunto.cliente)
        c = por_cliente[cid]
        costo = g.signo * (g.total - g.iva_desc_valor)
        c["gastos"] += costo
        if g.reembolsable and not g.reembolsado:
            c["reembolsables"] += costo
        a = c["asuntos"].setdefault(g.asunto_id, {"asunto": g.asunto, "ingresos": CERO, "gastos": CERO})
        a["gastos"] += costo
    filas = []
    for cid, c in por_cliente.items():
        margen = c["ingresos"] - c["gastos"]
        asuntos = sorted(c["asuntos"].values(), key=lambda a: -(a["ingresos"] - a["gastos"]))
        for a in asuntos:
            a["margen"] = a["ingresos"] - a["gastos"]
            a["margen_pct"] = _pct(a["margen"], a["ingresos"])
        filas.append({"cliente": clientes.get(cid), "ingresos": c["ingresos"], "gastos": c["gastos"], "margen": margen,
                      "margen_pct": _pct(margen, c["ingresos"]), "reembolsables": c["reembolsables"], "asuntos": asuntos})
    filas.sort(key=lambda f: -f["margen"])
    totales = {k: sum((f[k] for f in filas), CERO) for k in ("ingresos", "gastos", "margen", "reembolsables")}
    return filas, totales


def gastos_reembolsables_pendientes(session):
    return (session.query(Gasto).options(selectinload(Gasto.asunto).selectinload(Asunto.cliente), selectinload(Gasto.proveedor))
            .filter(Gasto.reembolsable.is_(True), Gasto.reembolsado.is_(False), Gasto.tipo_soporte != "NC")
            .order_by(Gasto.fecha).all())


# ------------------------------------------------------------------ presupuesto

def presupuesto_del_anio(session, anio: int) -> dict:
    return {p.clave: p.valor for p in session.query(Presupuesto).filter_by(anio=anio)}


def guardar_presupuesto(session, anio: int, valores: dict):
    actuales = {p.clave: p for p in session.query(Presupuesto).filter_by(anio=anio)}
    for clave, valor in valores.items():
        valor = contab.redondear(valor or 0)
        if clave in actuales:
            actuales[clave].valor = valor
        elif valor:
            session.add(Presupuesto(anio=anio, clave=clave, valor=valor))


def presupuesto_vs_real(session, anio: int, corte: date | None = None):
    """Compara lo presupuestado (anual, prorrateado a los meses transcurridos) con lo ejecutado."""
    corte = corte or date.today()
    if corte.year > anio:
        corte = date(anio, 12, 31)
    elif corte.year < anio:
        corte = date(anio, 1, 1)
    meses = corte.month
    factor = Decimal(meses) / Decimal(12)
    pres = presupuesto_del_anio(session, anio)
    inicio = date(anio, 1, 1)
    ingresos_real = impuestos.ingresos_brutos(session, inicio, corte)
    filas = []

    def fila(nombre, clave, real):
        anual = pres.get(clave, CERO)
        esperado = contab.redondear(anual * factor)
        filas.append({"concepto": nombre, "clave": clave, "anual": anual, "esperado": esperado, "real": real,
                      "diferencia": real - esperado, "ejecucion_anual": _pct(real, anual), "ejecucion": _pct(real, esperado)})
    fila("Ingresos", "ingresos", ingresos_real)
    reales = defaultdict(lambda: CERO)
    for g in session.query(Gasto).filter(Gasto.fecha.between(inicio, corte)):
        reales[g.categoria_id] += g.signo * (g.total - g.iva_desc_valor)
    categorias = session.query(CategoriaGasto).order_by(CategoriaGasto.nombre).all()
    total_gasto_pres = CERO
    total_gasto_real = CERO
    for c in categorias:
        clave = f"categoria:{c.id}"
        if clave in pres or reales.get(c.id):
            fila(c.nombre, clave, reales.get(c.id, CERO))
            total_gasto_pres += pres.get(clave, CERO)
            total_gasto_real += reales.get(c.id, CERO)
    return {"filas": filas, "meses": meses, "corte": corte, "categorias": categorias,
            "total_gastos_anual": total_gasto_pres, "total_gastos_real": total_gasto_real,
            "total_gastos_esperado": contab.redondear(total_gasto_pres * factor),
            "utilidad_presupuestada": pres.get("ingresos", CERO) - total_gasto_pres,
            "utilidad_real": ingresos_real - total_gasto_real}


# ------------------------------------------------------------------ SIMPLE vs régimen ordinario

TARIFA_RENTA_ORDINARIA = Decimal("0.35")
ICA_BOGOTA_POR_MIL = Decimal("9.66")  # actividades jurídicas (CIIU 6910) en Bogotá; se puede cambiar en Configuración


def comparar_regimenes(session, anio: int, corte: date | None = None):
    """Estimación: impuesto SIMPLE consolidado frente a renta ordinaria (35 % sobre la utilidad) más ICA.
    Es una aproximación para decidir cada enero si conviene seguir en el SIMPLE."""
    proy = impuestos.proyeccion_anual(session, anio, corte)
    inicio = date(anio, 1, 1)
    fin = min(corte or date.today(), date(anio, 12, 31)) if (corte or date.today()).year >= anio else date(anio, 12, 31)
    er = reportes.estado_resultados(session, inicio, fin)
    factor = (proy.ingresos_proyectados / proy.ingresos_a_la_fecha) if proy.ingresos_a_la_fecha else Decimal(1)
    gastos = er["gastos_admin"] + er["gastos_ventas"] + er["gastos_no_op"]
    gastos_proy = contab.redondear(gastos * factor, "1")
    ingresos = proy.ingresos_proyectados
    ica_por_mil = contab.d(contab.config(session, "ica_por_mil", str(ICA_BOGOTA_POR_MIL)))
    ica = contab.redondear(ingresos * ica_por_mil / 1000, "1")
    # En el ordinario el ICA pagado es deducible de la renta (art. 115 E.T.; el descuento tributario del 50 %
    # fue eliminado por la Ley 2277 de 2022 a partir de 2023).
    utilidad_fiscal = max(ingresos - gastos_proy - ica, CERO)
    renta = contab.redondear(utilidad_fiscal * TARIFA_RENTA_ORDINARIA, "1")
    renta_neta = renta
    ordinario = renta + ica
    simple = proy.simple_proyectado
    return {"proyeccion": proy, "ingresos": ingresos, "gastos": gastos_proy, "utilidad": utilidad_fiscal,
            "renta": renta, "ica": ica, "ica_por_mil": ica_por_mil, "renta_neta": renta_neta, "ordinario": ordinario,
            "simple": simple, "tarifa_simple": proy.tarifa, "diferencia": ordinario - simple,
            "tarifa_renta": TARIFA_RENTA_ORDINARIA, "margen": _pct(utilidad_fiscal, ingresos),
            # Punto de indiferencia: margen de utilidad a partir del cual el ordinario cuesta lo mismo que el SIMPLE
            "margen_indiferencia": _pct(simple, ingresos * TARIFA_RENTA_ORDINARIA) if ingresos else None}


# ------------------------------------------------------------------ informe de conciliación bancaria

def informe_conciliacion(session, banco: Banco, corte: date, saldo_extracto: Decimal | None):
    """Saldo según libros, partidas en libros que no aparecen en el extracto, partidas del extracto sin registrar
    y el saldo que debería mostrar el extracto."""
    saldo_libros = reportes.saldo_cuenta(session, banco.cuenta, corte)
    conciliados = {(m.origen_tipo, m.origen_id) for m in session.query(MovimientoBanco)
                   .filter_by(banco_id=banco.id, estado="conciliado")}
    en_libros_sin_extracto = []
    movs = (session.query(Movimiento).join(Asiento).options(selectinload(Movimiento.asiento))
            .filter(Movimiento.cuenta == banco.cuenta, Asiento.fecha <= corte).all())
    for m in movs:
        a = m.asiento
        if a.origen and ":" in a.origen:
            tipo, _, oid = a.origen.partition(":")
            if tipo == "venta" or tipo.startswith("depre") or tipo in ("simple", "cierre"):
                continue
            clave = (tipo, int(oid) if oid.isdigit() else None)
        else:
            clave = ("asiento", a.id)
        if clave not in conciliados:
            en_libros_sin_extracto.append((a, m.debito - m.credito))
    pendientes = (session.query(MovimientoBanco).filter_by(banco_id=banco.id, estado="pendiente")
                  .filter(MovimientoBanco.fecha <= corte).order_by(MovimientoBanco.fecha).all())
    total_libros_sin_extracto = sum((v for _, v in en_libros_sin_extracto), CERO)
    total_pendientes = sum((m.valor for m in pendientes), CERO)
    extracto_esperado = saldo_libros - total_libros_sin_extracto + total_pendientes
    tiene_extracto = session.query(MovimientoBanco).filter_by(banco_id=banco.id).count() > 0
    return {"banco": banco, "corte": corte, "saldo_libros": saldo_libros, "en_libros": en_libros_sin_extracto,
            "total_en_libros": total_libros_sin_extracto, "pendientes": pendientes, "total_pendientes": total_pendientes,
            "extracto_esperado": extracto_esperado, "saldo_extracto": saldo_extracto,
            "diferencia": (saldo_extracto - extracto_esperado) if saldo_extracto is not None else None,
            "tiene_extracto": tiene_extracto}


# ------------------------------------------------------------------ resumen mensual (para el correo)

@dataclass
class ResumenMes:
    anio: int
    mes: int
    ingresos: Decimal = CERO
    gastos: Decimal = CERO
    recaudado: Decimal = CERO
    cartera: Decimal = CERO
    cartera_vencida: Decimal = CERO
    efectivo: Decimal = CERO
    por_pagar: Decimal = CERO
    simple_estimado: Decimal = CERO
    alertas: list = field(default_factory=list)


def resumen_mensual(session, anio: int, mes: int) -> ResumenMes:
    from .models import Recaudo
    inicio = date(anio, mes, 1)
    fin = _dia_anterior(date(anio + (mes == 12), (mes % 12) + 1, 1))
    er = reportes.estado_resultados(session, inicio, fin)
    r = ResumenMes(anio, mes)
    r.ingresos = er["ingresos_op"] + er["ingresos_no_op"]
    r.gastos = er["gastos_admin"] + er["gastos_ventas"] + er["gastos_no_op"]
    r.recaudado = sum((x.valor for x in session.query(Recaudo).filter(Recaudo.fecha.between(inicio, fin))), CERO)
    _, tot = cartera.cartera_por_edades(session, fin)
    r.cartera, r.cartera_vencida = tot["total"], tot["total"] - tot["Por vencer"]
    r.efectivo = reportes.saldo_cuenta(session, PREFIJO_EFECTIVO, fin)
    r.por_pagar = cuentas_por_pagar_edades(session, fin)[1]["total"]
    bim = impuestos.bimestre_de(fin)
    r.simple_estimado = impuestos.recibo_2593(session, anio, bim).total
    return r
