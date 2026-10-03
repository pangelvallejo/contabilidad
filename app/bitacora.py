"""Historial automático de cambios (antes de cada flush de la sesión)."""
from datetime import date
from decimal import Decimal

from sqlalchemy import event, inspect

from .models import (Asiento, Banco, Bitacora, CategoriaGasto, Config, DocumentoVenta, Gasto, MovimientoBanco,
                     PagoGasto, PagoImpuesto, Recaudo, Tercero, Vencimiento)

VIGILADOS = {
    DocumentoVenta: ("Factura de venta", lambda o: f"{o.tipo} {o.numero}"),
    Recaudo: ("Recaudo", lambda o: f"{o.fecha} {o.valor}"),
    Gasto: ("Gasto", lambda o: f"{o.numero or o.tipo_soporte} {o.fecha} {o.total}"),
    PagoGasto: ("Pago a proveedor", lambda o: f"{o.fecha} {o.valor}"),
    PagoImpuesto: ("Pago de impuesto", lambda o: f"{o.formulario} {o.anio} {o.fecha}"),
    Tercero: ("Tercero", lambda o: f"{o.nombre} ({o.nit})"),
    Banco: ("Banco", lambda o: o.nombre),
    CategoriaGasto: ("Categoría de gasto", lambda o: o.nombre),
    Config: ("Configuración", lambda o: o.clave),
    Vencimiento: ("Vencimiento", lambda o: f"{o.obligacion} {o.periodo or ''}"),
    MovimientoBanco: ("Movimiento bancario", lambda o: f"{o.fecha} {o.valor} {o.descripcion[:40]}"),
}
IGNORAR_CAMPOS = {"creado", "importado", "xml_archivo", "pdf_archivo", "soporte_archivo"}


def _texto(v):
    if isinstance(v, Decimal):
        return f"{v:,.2f}"
    if isinstance(v, date):
        return v.isoformat()
    return "" if v is None else str(v)[:80]


def _cambios(obj):
    partes = []
    for attr in inspect(obj).attrs:
        if attr.key in IGNORAR_CAMPOS or not hasattr(attr, "history"):
            continue
        h = attr.history
        if h.has_changes() and not isinstance(attr.value, list):
            antes = h.deleted[0] if h.deleted else None
            despues = h.added[0] if h.added else None
            if _texto(antes) != _texto(despues):
                partes.append(f"{attr.key}: {_texto(antes) or '—'} → {_texto(despues) or '—'}")
    return "; ".join(partes)


def _registrar(session, _flush_context, _instances):
    if session.info.get("sin_bitacora"):
        return
    nuevos = []
    for obj in list(session.new):
        if type(obj) in VIGILADOS and not isinstance(obj, Config):  # la configuración solo se registra al cambiar
            nombre, desc = VIGILADOS[type(obj)]
            nuevos.append(Bitacora(accion="crear", entidad=nombre, entidad_id=None, descripcion=desc(obj)[:300]))
            obj._bitacora_pendiente = nuevos[-1]
    for obj in list(session.dirty):
        if type(obj) in VIGILADOS and session.is_modified(obj):
            cambios = _cambios(obj)
            if cambios:
                nombre, desc = VIGILADOS[type(obj)]
                nuevos.append(Bitacora(accion="editar", entidad=nombre, entidad_id=getattr(obj, "id", None),
                                       descripcion=desc(obj)[:300], detalle=cambios[:2000]))
    for obj in list(session.deleted):
        if type(obj) in VIGILADOS:
            nombre, desc = VIGILADOS[type(obj)]
            nuevos.append(Bitacora(accion="borrar", entidad=nombre, entidad_id=getattr(obj, "id", None),
                                   descripcion=desc(obj)[:300]))
    # Los asientos manuales también se registran; los automáticos ya quedan por su documento.
    for obj in list(session.new) + list(session.deleted):
        if isinstance(obj, Asiento) and obj.origen is None:
            accion = "crear" if obj in session.new else "borrar"
            nuevos.append(Bitacora(accion=accion, entidad="Asiento manual", entidad_id=getattr(obj, "id", None),
                                   descripcion=f"{obj.fecha} {obj.descripcion}"[:300]))
    for b in nuevos:
        session.add(b)


def _completar_ids(session, _ctx):
    for obj in session.identity_map.values():
        b = getattr(obj, "_bitacora_pendiente", None)
        if b is not None and b.entidad_id is None:
            b.entidad_id = getattr(obj, "id", None)
            del obj._bitacora_pendiente


def activar(session_factory):
    if not event.contains(session_factory, "before_flush", _registrar):
        event.listen(session_factory, "before_flush", _registrar)
        event.listen(session_factory, "after_flush_postexec", _completar_ids)
