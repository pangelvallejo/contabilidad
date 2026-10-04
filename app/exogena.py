"""Información exógena (medios magnéticos) en Excel, formatos 1001, 1005, 1006, 1007, 1008 y 1009.

Genera la información base por tercero. Antes de presentar se debe confirmar la obligación de
reportar, la versión vigente de cada formato y las cuantías menores de la resolución del año.
"""
from collections import defaultdict
from datetime import date

from . import cartera
from .models import CERO, DocumentoVenta, Gasto, OtroIngreso, Tercero

COL_TERCERO = ["Tipo de documento", "Número identificación", "DV", "Primer apellido", "Segundo apellido",
               "Primer nombre", "Otros nombres", "Razón social", "Dirección", "Código dpto.", "Código mcp.",
               "País de residencia"]


def _datos_tercero(t: Tercero):
    natural = t.tipo_doc == "13"
    return [t.tipo_doc, t.nit, t.dv or "",
            (t.primer_apellido or "") if natural else "", (t.segundo_apellido or "") if natural else "",
            (t.primer_nombre or "") if natural else "", (t.otros_nombres or "") if natural else "",
            "" if natural else t.nombre, t.direccion or "", t.cod_departamento or "", (t.cod_municipio or "")[2:],
            t.pais or "169"]


def faltantes(t: Tercero):
    falta = []
    if not t.direccion:
        falta.append("dirección")
    if not t.cod_municipio:
        falta.append("municipio")
    if t.tipo_doc == "13" and not (t.primer_apellido and t.primer_nombre):
        falta.append("nombres y apellidos")
    if t.tipo_doc == "31" and not t.dv:
        falta.append("DV")
    return falta


def generar(session, anio):
    inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
    gastos = session.query(Gasto).filter(Gasto.fecha.between(inicio, fin), Gasto.proveedor_id.isnot(None)).all()
    ventas = (session.query(DocumentoVenta)
              .filter(DocumentoVenta.fecha.between(inicio, fin), DocumentoVenta.anulada.is_(False)).all())

    f1001 = defaultdict(lambda: [CERO, CERO])  # (concepto, tercero) -> pago deducible, IVA mayor valor del costo
    f1005 = defaultdict(lambda: CERO)
    for g in gastos:
        clave = (g.categoria.concepto_exogena, g.proveedor_id)
        f1001[clave][0] += g.signo * (g.total - g.iva)
        f1001[clave][1] += g.signo * (g.iva - g.iva_desc_valor)
        f1005[g.proveedor_id] += g.signo * g.iva_desc_valor

    f1006 = defaultdict(lambda: [CERO, CERO])  # IVA generado, IVA de devoluciones
    f1007 = defaultdict(lambda: [CERO, CERO])  # ingresos brutos, devoluciones
    for v in ventas:
        if v.tipo == "NC":
            f1006[v.cliente_id][1] += v.iva
            f1007[v.cliente_id][1] += v.ingreso
        else:
            f1006[v.cliente_id][0] += v.iva
            f1007[v.cliente_id][0] += v.ingreso

    # Ingresos sin factura: 4003 intereses y rendimientos financieros, 4002 otros no operacionales
    f1007_otros = defaultdict(lambda: CERO)  # (concepto, tercero_id) -> valor
    sin_tercero = []
    for oi in session.query(OtroIngreso).filter(OtroIngreso.fecha.between(inicio, fin)):
        concepto = "4003" if oi.cuenta.startswith("4210") else ("4001" if oi.cuenta.startswith("41") else "4002")
        if oi.tercero_id is None:
            sin_tercero.append(oi)
            continue
        f1007_otros[(concepto, oi.tercero_id)] += oi.valor

    f1008 = defaultdict(lambda: CERO)
    for doc, saldo in cartera.documentos_abiertos(session, al=fin):
        f1008[doc.cliente_id] += saldo

    f1009 = defaultdict(lambda: CERO)
    for g in session.query(Gasto).filter(Gasto.fecha <= fin, Gasto.forma_pago == "credito",
                                         Gasto.proveedor_id.isnot(None)):
        pagado = sum((p.valor for p in g.pagos if p.fecha <= fin), CERO)
        saldo = g.total - pagado if g.tipo_soporte != "NC" else -g.total
        if saldo:
            f1009[g.proveedor_id] += saldo
    f1009 = {t: v for t, v in f1009.items() if v > 0}  # un saldo a favor con el proveedor no es cuenta por pagar

    terceros = {t.id: t for t in session.query(Tercero)}
    hojas = {
        "1001 Pagos": (["Concepto"] + COL_TERCERO + ["Pago o abono en cuenta deducible",
                                                    "Pago o abono en cuenta no deducible",
                                                    "IVA mayor valor del costo deducible",
                                                    "IVA mayor valor del costo no deducible",
                                                    "Retención en la fuente practicada renta",
                                                    "Retención en la fuente asumida renta",
                                                    "Retención IVA régimen común", "Retención IVA no domiciliados"],
                       [[c] + _datos_tercero(terceros[t]) + [v[0], 0, v[1], 0, 0, 0, 0, 0]
                        for (c, t), v in sorted(f1001.items()) if v[0] or v[1]]),
        "1005 IVA descontable": (COL_TERCERO[:8] + ["Impuesto descontable", "IVA resultante por devoluciones"],
                                 [_datos_tercero(terceros[t])[:8] + [v, 0] for t, v in f1005.items() if v]),
        "1006 IVA generado": (COL_TERCERO[:8] + ["Impuesto generado", "IVA recuperado en devoluciones",
                                                 "Impuesto al consumo"],
                              [_datos_tercero(terceros[t])[:8] + [v[0], v[1], 0] for t, v in f1006.items()
                               if v[0] or v[1]]),
        "1007 Ingresos": (["Concepto"] + COL_TERCERO[:8] + ["País", "Ingresos brutos recibidos",
                                                           "Devoluciones, rebajas y descuentos"],
                          [["4001"] + _datos_tercero(terceros[t])[:8] + ["169", v[0], v[1]]
                           for t, v in f1007.items() if v[0] or v[1]]
                          + [[c] + _datos_tercero(terceros[t])[:8] + ["169", v, 0]
                             for (c, t), v in sorted(f1007_otros.items()) if v]),
        "1008 Cuentas por cobrar": (["Concepto"] + COL_TERCERO + ["Saldo cuentas por cobrar al 31-12"],
                                    [["1315"] + _datos_tercero(terceros[t]) + [v] for t, v in f1008.items() if v]),
        "1009 Cuentas por pagar": (["Concepto"] + COL_TERCERO + ["Saldo cuentas por pagar al 31-12"],
                                   [["2201"] + _datos_tercero(terceros[t]) + [v] for t, v in f1009.items() if v]),
    }
    usados = ({t for (_, t) in f1001} | set(f1005) | set(f1006) | set(f1007) | set(f1008) | set(f1009)
              | {t for (_, t) in f1007_otros})
    incompletos = [(terceros[t], faltantes(terceros[t])) for t in usados if faltantes(terceros[t])]
    if sin_tercero:
        from .formato import pesos
        total = sum((oi.valor for oi in sin_tercero), CERO)
        incompletos.append((Tercero(nit="", nombre=f"{len(sin_tercero)} ingreso(s) sin factura por {pesos(total)} sin "
                                                   "tercero (indíquelo en Bancos → Ingresos sin factura)"),
                            ["tercero"]))
    return hojas, incompletos
