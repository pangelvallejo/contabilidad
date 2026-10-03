"""Configuración: datos de la empresa, bancos, categorías y respaldos."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from .. import config, contab, respaldo
from ..db import Session
from ..models import Banco, CategoriaGasto, Cuenta
from . import check

bp = Blueprint("ajustes", __name__, url_prefix="/configuracion")

CAMPOS_EMPRESA = ["empresa_nombre", "empresa_nit", "empresa_dv", "empresa_direccion", "empresa_ciudad",
                  "empresa_cod_municipio", "empresa_email", "empresa_telefono", "empresa_ciiu", "simple_base",
                  "carpeta_respaldo", "respaldos_a_conservar"]


@bp.route("/", methods=["GET", "POST"])
def inicio():
    s = Session()
    if request.method == "POST":
        accion = request.form.get("accion")
        if accion == "empresa":
            for campo in CAMPOS_EMPRESA:
                if campo in request.form:
                    contab.set_config(s, campo, request.form.get(campo, "").strip())
            flash("Datos guardados.", "ok")
        elif accion == "banco":
            bid = request.form.get("id", type=int)
            b = s.get(Banco, bid) if bid else None
            if b is None:
                codigo = _nueva_subcuenta(s, "112005")
                s.add(Cuenta(codigo=codigo, nombre=request.form["nombre"], naturaleza="D", movimiento=True))
                b = Banco(cuenta=codigo)
                s.add(b)
            b.nombre = request.form["nombre"]
            b.numero = request.form.get("numero") or None
            b.tipo = request.form.get("tipo") or None
            b.activo = check("activo") if bid else True
            s.get(Cuenta, b.cuenta).nombre = b.nombre
            flash("Banco guardado.", "ok")
        elif accion == "categoria":
            cid = request.form.get("id", type=int)
            c = s.get(CategoriaGasto, cid) if cid else CategoriaGasto()
            c.nombre = request.form["nombre"]
            c.cuenta = request.form["cuenta"]
            c.concepto_exogena = request.form.get("concepto_exogena") or "5016"
            c.palabras_clave = request.form.get("palabras_clave") or None
            c.iva_descontable_def = check("iva_descontable_def")
            c.activa = check("activa") if cid else True
            s.add(c)
            flash("Categoría guardada.", "ok")
        elif accion == "respaldo":
            try:
                ruta = respaldo.crear_respaldo(s)
                flash(f"Respaldo creado en {ruta}", "ok")
            except Exception as e:  # noqa: BLE001
                flash(f"No se pudo crear el respaldo: {e}", "error")
        s.commit()
        return redirect(url_for("ajustes.inicio") + "#" + (accion or ""))
    valores = {c: contab.config(s, c, "") for c in CAMPOS_EMPRESA + ["ultimo_respaldo"]}
    return render_template("ajustes.html", v=valores, bancos=s.query(Banco).order_by(Banco.id).all(),
                           categorias=s.query(CategoriaGasto).order_by(CategoriaGasto.nombre).all(),
                           cuentas_gasto=s.query(Cuenta).filter(Cuenta.movimiento.is_(True),
                                                                 or_(Cuenta.codigo.like("5%"),
                                                                     Cuenta.codigo.like("15%"))).order_by(Cuenta.codigo).all(),
                           carpeta_respaldo=respaldo.carpeta_destino(s), datos_dir=config.DATOS_DIR)


def _nueva_subcuenta(s, padre):
    existentes = [c.codigo for c in s.query(Cuenta).filter(Cuenta.codigo.like(f"{padre}__"))]
    n = max((int(c[-2:]) for c in existentes), default=0) + 1
    return f"{padre}{n:02d}"
