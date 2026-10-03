"""Búsqueda global e historial de cambios."""
from flask import Blueprint, render_template, request
from sqlalchemy import or_

from ..contab import d
from ..db import Session
from ..models import Bitacora, DocumentoVenta, Gasto, Recaudo, Tercero
from . import paginar

bp = Blueprint("buscar", __name__)


@bp.route("/buscar")
def buscar():
    s = Session()
    q = request.args.get("q", "").strip()
    res = {"ventas": [], "gastos": [], "terceros": [], "recaudos": []}
    if q:
        like = f"%{q}%"
        valor = None
        try:
            valor = d(q)
        except Exception:  # noqa: BLE001
            pass
        f_ventas = [DocumentoVenta.numero.ilike(like), DocumentoVenta.cufe.ilike(like),
                    DocumentoVenta.notas.ilike(like), Tercero.nombre.ilike(like), Tercero.nit.like(like)]
        f_gastos = [Gasto.numero.ilike(like), Gasto.descripcion.ilike(like), Gasto.cufe.ilike(like),
                    Tercero.nombre.ilike(like), Tercero.nit.like(like)]
        f_rec = [Recaudo.referencia.ilike(like), Tercero.nombre.ilike(like)]
        if valor and valor > 0:
            f_ventas += [DocumentoVenta.total == valor, DocumentoVenta.ingreso == valor]
            f_gastos += [Gasto.total == valor, Gasto.subtotal == valor]
            f_rec += [Recaudo.valor == valor]
        res["ventas"] = (s.query(DocumentoVenta).join(Tercero, DocumentoVenta.cliente_id == Tercero.id)
                         .filter(or_(*f_ventas)).order_by(DocumentoVenta.fecha.desc()).limit(50).all())
        res["gastos"] = (s.query(Gasto).outerjoin(Tercero, Gasto.proveedor_id == Tercero.id)
                         .filter(or_(*f_gastos)).order_by(Gasto.fecha.desc()).limit(50).all())
        res["recaudos"] = (s.query(Recaudo).join(Tercero, Recaudo.cliente_id == Tercero.id)
                           .filter(or_(*f_rec)).order_by(Recaudo.fecha.desc()).limit(50).all())
        res["terceros"] = (s.query(Tercero).filter(or_(Tercero.nombre.ilike(like), Tercero.nit.like(like),
                                                        Tercero.email.ilike(like)))
                           .order_by(Tercero.nombre).limit(50).all())
    return render_template("buscar.html", q=q, **res)


@bp.route("/historial")
def historial():
    s = Session()
    q = s.query(Bitacora).order_by(Bitacora.fecha.desc(), Bitacora.id.desc())
    entidad = request.args.get("entidad")
    if entidad:
        q = q.filter(Bitacora.entidad == entidad)
    filas, pagina, paginas = paginar(q)
    entidades = [e[0] for e in s.query(Bitacora.entidad).distinct().order_by(Bitacora.entidad)]
    return render_template("historial.html", filas=filas, pagina=pagina, paginas=paginas, entidad=entidad,
                           entidades=entidades)
