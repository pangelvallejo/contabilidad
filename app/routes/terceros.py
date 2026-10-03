"""Clientes y proveedores."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from ..db import Session
from ..exogena import faltantes
from ..models import Tercero
from . import check, destino_seguro

bp = Blueprint("terceros", __name__)


@bp.route("/terceros")
def lista():
    s = Session()
    q = s.query(Tercero)
    tipo = request.args.get("tipo")
    if tipo == "clientes":
        q = q.filter(Tercero.es_cliente.is_(True))
    elif tipo == "proveedores":
        q = q.filter(Tercero.es_proveedor.is_(True))
    buscar = request.args.get("q", "").strip()
    if buscar:
        q = q.filter(or_(Tercero.nombre.ilike(f"%{buscar}%"), Tercero.nit.like(f"%{buscar}%")))
    terceros = q.order_by(Tercero.nombre).all()
    return render_template("terceros/lista.html", terceros=terceros, tipo=tipo, buscar=buscar, faltantes=faltantes)


@bp.route("/terceros/nuevo", methods=["GET", "POST"])
@bp.route("/terceros/<int:id>", methods=["GET", "POST"])
def editar(id=None):
    s = Session()
    t = s.get(Tercero, id) if id else Tercero(es_cliente=request.args.get("tipo") == "cliente",
                                              es_proveedor=request.args.get("tipo") == "proveedor",
                                              tipo_doc="31", plazo_dias=30, pais="169")
    if id and t is None:
        abort(404)
    if request.method == "POST":
        for campo in ("tipo_doc", "nit", "dv", "nombre", "primer_apellido", "segundo_apellido", "primer_nombre",
                      "otros_nombres", "email", "telefono", "direccion", "ciudad", "cod_municipio", "pais", "notas"):
            setattr(t, campo, (request.form.get(campo) or "").strip() or None)
        t.pais = t.pais or "169"
        t.tipo_doc = t.tipo_doc or "31"
        t.es_cliente = check("es_cliente")
        t.es_proveedor = check("es_proveedor")
        t.aplica_reteiva = check("aplica_reteiva")
        t.plazo_dias = int(request.form.get("plazo_dias") or 0)
        if not t.nit or not t.nombre:
            flash("NIT/cédula y nombre son obligatorios.", "error")
            return render_template("terceros/form.html", t=t)
        with s.no_autoflush:
            otro = s.query(Tercero).filter(Tercero.nit == t.nit, Tercero.id != (t.id or 0)).first()
        if otro:
            flash(f"Ya existe un tercero con ese número: {otro.nombre}.", "error")
            s.rollback()
            return render_template("terceros/form.html", t=t)
        s.add(t)
        s.commit()
        flash("Tercero guardado.", "ok")
        return redirect(destino_seguro(request.args.get("volver"), url_for("terceros.lista")))
    return render_template("terceros/form.html", t=t)
