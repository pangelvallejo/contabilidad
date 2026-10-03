"""Configuración: datos de la empresa, bancos, categorías y respaldos."""
import re
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from .. import config, contab, impuestos, respaldo
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
                    valor = request.form.get(campo, "").strip()
                    if campo in ("empresa_nit", "empresa_dv"):
                        valor = re.sub(r"\D", "", valor)
                    contab.set_config(s, campo, valor)
            if request.form.get("uvt_anio") and request.form.get("uvt_valor", "").strip():
                try:
                    contab.set_config(s, f"uvt_{int(request.form['uvt_anio'])}",
                                      str(contab.d(request.form["uvt_valor"])))
                except Exception:  # noqa: BLE001
                    flash("El valor de la UVT no es un número válido.", "error")
            if "respaldos_a_conservar" in request.form:
                try:
                    contab.set_config(s, "respaldos_a_conservar", str(max(1, int(request.form["respaldos_a_conservar"]))))
                except ValueError:
                    contab.set_config(s, "respaldos_a_conservar", "30")
            flash("Datos guardados.", "ok")
        elif accion == "banco":
            bid = request.form.get("id", type=int)
            b = s.get(Banco, bid) if bid else None
            if not request.form.get("nombre", "").strip():
                flash("El banco necesita un nombre.", "error")
                return redirect(url_for("ajustes.inicio") + "#banco")
            if b is None:
                codigo = _nueva_subcuenta(s, "112005")
                s.add(Cuenta(codigo=codigo, nombre=request.form["nombre"], naturaleza="D", movimiento=True))
                s.flush()  # la cuenta debe existir antes del banco (clave foránea)
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
            nombre = request.form.get("nombre", "").strip()
            repetida = s.query(CategoriaGasto).filter(CategoriaGasto.nombre == nombre,
                                                      CategoriaGasto.id != (cid or 0)).first()
            if not nombre or repetida or s.get(Cuenta, request.form.get("cuenta", "")) is None:
                flash("La categoría necesita un nombre único y una cuenta válida.", "error")
                return redirect(url_for("ajustes.inicio") + "#categoria")
            c = s.get(CategoriaGasto, cid) if cid else CategoriaGasto()
            c.nombre = nombre
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
        try:
            s.commit()
        except Exception as e:  # noqa: BLE001
            s.rollback()
            flash(f"No se pudo guardar: {e}", "error")
        return redirect(url_for("ajustes.inicio") + "#" + (accion or ""))
    valores = {c: contab.config(s, c, "") for c in CAMPOS_EMPRESA + ["ultimo_respaldo"]}
    anio = date.today().year
    uvts = [(a, impuestos.uvt(a, s), bool(contab.config(s, f"uvt_{a}"))) for a in (anio, anio + 1)]
    return render_template("ajustes.html", v=valores, bancos=s.query(Banco).order_by(Banco.id).all(),
                           categorias=s.query(CategoriaGasto).order_by(CategoriaGasto.nombre).all(),
                           cuentas_gasto=s.query(Cuenta).filter(Cuenta.movimiento.is_(True),
                                                                 or_(Cuenta.codigo.like("5%"),
                                                                     Cuenta.codigo.like("15%"))).order_by(Cuenta.codigo).all(),
                           carpeta_respaldo=respaldo.carpeta_destino(s), datos_dir=config.DATOS_DIR, uvts=uvts)


def _nueva_subcuenta(s, padre):
    existentes = [c.codigo for c in s.query(Cuenta).filter(Cuenta.codigo.like(f"{padre}__"))]
    n = max((int(c[-2:]) for c in existentes), default=0) + 1
    return f"{padre}{n:02d}"
