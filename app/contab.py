"""Generación de asientos contables a partir de los documentos.

Cada documento (factura, recaudo, gasto, pago) es dueño de sus asientos, identificados por
`origen`. Al crear o editar un documento se regeneran; al borrarlo, se eliminan.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from .models import CERO, Asiento, Config, Movimiento

# Cuentas usadas por los asientos automáticos
CTA_CLIENTES = "130505"
CTA_INGRESOS = "415595"
CTA_DEVOLUCIONES = "417505"
CTA_IVA_GENERADO = "240805"
CTA_IVA_DESCONTABLE = "240810"
CTA_IVA_PAGADO_2593 = "240815"
CTA_RETEIVA = "135517"
CTA_ANTICIPO_SIMPLE = "135595"
CTA_SIMPLE_POR_PAGAR = "240405"
CTA_GASTO_SIMPLE = "540505"
CTA_PROVEEDORES = "220505"
CTA_ANTICIPOS_CLIENTES = "280505"
CTA_SOCIOS = "235505"
CTA_CAJA = "110505"
CTA_GASTOS_BANCARIOS = "530505"  # GMF y cargos que el banco descuenta de los rendimientos

TIPOS_ASIENTO = {
    "FV": "Factura de venta",
    "NC": "Nota crédito venta",
    "ND": "Nota débito venta",
    "RI": "Retención de IVA",
    "RC": "Recibo de caja",
    "FC": "Gasto / compra",
    "CE": "Comprobante de egreso",
    "IM": "Pago de impuestos",
    "AJ": "Ajuste / manual",
    "CI": "Cierre de ejercicio",
}


class ErrorContable(Exception):
    pass


def d(valor) -> Decimal:
    if valor is None or valor == "":
        return CERO
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, str):
        valor = valor.replace("$", "").replace(" ", "").strip() or "0"
        # Formato colombiano 1.234.567,89 -> 1234567.89; "1.234.567" o "250.000" -> miles
        if "," in valor:
            valor = valor.replace(".", "").replace(",", ".")
        elif valor.count(".") > 1 or (valor.count(".") == 1 and len(valor.split(".")[1]) == 3):
            valor = valor.replace(".", "")
    res = Decimal(str(valor))
    if not res.is_finite():
        raise ValueError(f"'{valor}' no es un número.")
    return res


def redondear(valor, unidad="0.01") -> Decimal:
    return d(valor).quantize(Decimal(unidad), rounding=ROUND_HALF_UP)


def config(session, clave, defecto=None):
    c = session.get(Config, clave)
    return c.valor if c and c.valor not in (None, "") else defecto


def set_config(session, clave, valor):
    c = session.get(Config, clave)
    if c is None:
        c = Config(clave=clave)
        session.add(c)
    c.valor = valor


def periodo_bloqueado_hasta(session):
    """Fecha hasta la cual la contabilidad está cerrada (se fija al pagar el 2593 o al cerrar el año)."""
    valor = config(session, "periodo_bloqueado_hasta")
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def verificar_periodo(session, fecha, forzar=False):
    tope = periodo_bloqueado_hasta(session)
    if not forzar and tope and fecha and fecha <= tope:
        raise ErrorContable(
            f"El periodo hasta el {tope:%d/%m/%Y} está bloqueado porque ya se declaró. "
            "Para corregirlo cambie la fecha de bloqueo en Configuración.")


def _siguiente_numero(session, tipo):
    return (session.query(func.max(Asiento.numero)).filter(Asiento.tipo == tipo).scalar() or 0) + 1


def guardar_asiento(session, *, origen, tipo, fecha, descripcion, tercero_id, lineas, asiento=None, forzar=False):
    """Crea o reemplaza el asiento (origen, tipo). `lineas`: (cuenta, débito, crédito, tercero_id, detalle).

    Las líneas con valor cero se omiten; si no queda ninguna, el asiento se elimina.
    Rechaza fechas dentro de un periodo bloqueado, salvo `forzar` (cierres y causaciones).
    """
    lineas = [(c, redondear(db), redondear(cr), t, det) for c, db, cr, t, det in lineas
              if redondear(db) != 0 or redondear(cr) != 0]
    if asiento is None and origen is not None:
        asiento = session.query(Asiento).filter_by(origen=origen, tipo=tipo).one_or_none()
    if asiento is not None and asiento.fecha == fecha and asiento.tercero_id == tercero_id:
        actuales = [(l.cuenta, l.debito, l.credito, l.tercero_id, l.descripcion or None) for l in asiento.lineas]
        if actuales == [(c, db, cr, t, det or None) for c, db, cr, t, det in lineas]:
            asiento.descripcion = descripcion[:250]
            return asiento  # nada cambió en la contabilidad: no cuenta como modificación del periodo
    verificar_periodo(session, fecha, forzar)
    if asiento is not None:
        verificar_periodo(session, asiento.fecha, forzar)
    if not lineas:
        if asiento is not None:
            session.delete(asiento)
        return None
    total_d = sum(l[1] for l in lineas)
    total_c = sum(l[2] for l in lineas)
    if total_d != total_c:
        raise ErrorContable(f"El asiento {tipo} '{descripcion}' no cuadra: débitos {total_d} ≠ créditos {total_c}")
    if asiento is None:
        asiento = Asiento(tipo=tipo, numero=_siguiente_numero(session, tipo), origen=origen)
        session.add(asiento)
    asiento.fecha = fecha
    asiento.descripcion = descripcion[:250]
    asiento.tercero_id = tercero_id
    asiento.lineas.clear()
    session.flush()
    for cuenta, debito, credito, tercero, detalle in lineas:
        asiento.lineas.append(Movimiento(cuenta=cuenta, debito=debito, credito=credito,
                                         tercero_id=tercero, descripcion=(detalle or None)))
    session.flush()
    return asiento


def borrar_asientos(session, origen, forzar=False):
    for a in session.query(Asiento).filter_by(origen=origen).all():
        verificar_periodo(session, a.fecha, forzar)
        session.delete(a)
    session.flush()


# ------------------------------------------------------------------ ventas

def contabilizar_venta(session, doc):
    origen = f"venta:{doc.id}"
    t = doc.cliente_id
    ingreso = doc.total - doc.iva
    desc = f"{doc.tipo} {doc.numero} {doc.cliente.nombre if doc.cliente else ''}".strip()
    if doc.anulada:
        borrar_asientos(session, origen)
        return
    if doc.tipo == "NC":
        lineas = [
            (CTA_DEVOLUCIONES, ingreso, 0, t, desc),
            (CTA_IVA_GENERADO, doc.iva, 0, t, desc),
            (CTA_CLIENTES, 0, doc.total, t, desc),
        ]
    else:
        lineas = [
            (CTA_CLIENTES, doc.total, 0, t, desc),
            (CTA_INGRESOS, 0, ingreso, t, desc),
            (CTA_IVA_GENERADO, 0, doc.iva, t, desc),
        ]
    guardar_asiento(session, origen=origen, tipo=doc.tipo, fecha=doc.fecha, descripcion=desc,
                    tercero_id=t, lineas=lineas)

    rete = doc.reteiva_valor if (doc.reteiva_aplica and doc.tipo != "NC") else CERO
    guardar_asiento(session, origen=origen, tipo="RI", fecha=doc.reteiva_fecha or doc.fecha,
                    descripcion=f"ReteIVA {doc.numero}", tercero_id=t,
                    lineas=[(CTA_RETEIVA, rete, 0, t, None), (CTA_CLIENTES, 0, rete, t, None)])


def contabilizar_recaudo(session, rec):
    t = rec.cliente_id
    desc = f"Recaudo {rec.cliente.nombre if rec.cliente else ''} {rec.referencia or ''}".strip()
    lineas = [(rec.banco.cuenta, rec.valor, 0, t, desc)]
    for ap in rec.aplicaciones:
        lineas.append((CTA_CLIENTES, 0, ap.valor, t, f"Abono {ap.documento.numero}"))
    if rec.sin_aplicar > 0:
        lineas.append((CTA_ANTICIPOS_CLIENTES, 0, rec.sin_aplicar, t, "Saldo sin aplicar"))
    guardar_asiento(session, origen=f"recaudo:{rec.id}", tipo="RC", fecha=rec.fecha, descripcion=desc,
                    tercero_id=t, lineas=lineas)


def contabilizar_otro_ingreso(session, oi):
    """Dr banco (neto) y Dr gastos bancarios (descuentos) contra Cr la cuenta de ingreso elegida (valor bruto)."""
    desc = f"{oi.concepto} {oi.referencia or ''}".strip()
    lineas = [(oi.banco.cuenta, oi.neto, 0, oi.tercero_id, desc),
              (CTA_GASTOS_BANCARIOS, oi.descuentos, 0, oi.tercero_id, "Descuentos del banco (GMF, cargos)"),
              (oi.cuenta, 0, oi.valor, oi.tercero_id, desc)]
    guardar_asiento(session, origen=f"otroingreso:{oi.id}", tipo="RC", fecha=oi.fecha, descripcion=desc,
                    tercero_id=oi.tercero_id, lineas=lineas)


# ------------------------------------------------------------------ gastos

def contabilizar_gasto(session, g):
    origen = f"gasto:{g.id}"
    t = g.proveedor_id
    nombre = g.proveedor.nombre if g.proveedor else ""
    desc = f"{g.numero or g.tipo_soporte} {nombre} {g.descripcion or ''}".strip()
    iva_desc = g.iva_desc_valor
    costo = g.total - iva_desc
    contra = CTA_PROVEEDORES if g.forma_pago == "credito" else (g.cuenta_pago or CTA_CAJA)
    if g.tipo_soporte == "NC":  # nota crédito de proveedor: reversa gasto e IVA descontable
        lineas = [(contra, g.total, 0, t, desc), (g.categoria.cuenta, 0, costo, t, desc),
                  (CTA_IVA_DESCONTABLE, 0, iva_desc, t, desc)]
    else:
        lineas = [(g.categoria.cuenta, costo, 0, t, desc), (CTA_IVA_DESCONTABLE, iva_desc, 0, t, desc),
                  (contra, 0, g.total, t, desc)]
    guardar_asiento(session, origen=origen, tipo="FC", fecha=g.fecha, descripcion=desc, tercero_id=t,
                    lineas=lineas)


def contabilizar_pago_gasto(session, p):
    g = p.gasto
    desc = f"Pago {g.numero or ''} {g.proveedor.nombre if g.proveedor else ''}".strip()
    guardar_asiento(session, origen=f"pagogasto:{p.id}", tipo="CE", fecha=p.fecha, descripcion=desc,
                    tercero_id=g.proveedor_id,
                    lineas=[(CTA_PROVEEDORES, p.valor, 0, g.proveedor_id, desc),
                            (p.cuenta_pago, 0, p.valor, g.proveedor_id, desc)])


# ------------------------------------------------------------------ impuestos

def contabilizar_pago_impuesto(session, p):
    periodo = f"bimestre {p.bimestre} de {p.anio}" if p.bimestre else str(p.anio)
    desc = f"Formulario {p.formulario} {periodo}"
    if p.formulario == "2593":
        # El anticipo del bimestre 6 se paga en enero: ya es un pago del impuesto causado al cierre (pasivo),
        # no un anticipo del año en curso.
        cta_simple = CTA_SIMPLE_POR_PAGAR if p.fecha.year > p.anio else CTA_ANTICIPO_SIMPLE
        lineas = [(cta_simple, p.valor_simple, 0, None, "Anticipo SIMPLE"),
                  (CTA_IVA_PAGADO_2593, p.valor_iva, 0, None, "IVA bimestral"),
                  (p.banco.cuenta, 0, p.total, None, desc)]
        if p.bimestre:
            # La reteIVA que practicaron los clientes en el bimestre se aplica contra el IVA generado.
            from .impuestos import resumen_iva_bimestre
            rete = resumen_iva_bimestre(session, p.anio, p.bimestre).reteiva
            lineas += [(CTA_IVA_GENERADO, rete, 0, None, "ReteIVA aplicada"), (CTA_RETEIVA, 0, rete, None, "ReteIVA aplicada")]
    else:  # saldo de declaración anual: 260 cancela SIMPLE por pagar; 300 cancela IVA
        cta = CTA_SIMPLE_POR_PAGAR if p.formulario == "260" else CTA_IVA_PAGADO_2593
        lineas = [(cta, p.total, 0, None, desc), (p.banco.cuenta, 0, p.total, None, desc)]
    guardar_asiento(session, origen=f"impuesto:{p.id}", tipo="IM", fecha=p.fecha, descripcion=desc,
                    tercero_id=None, lineas=lineas)
