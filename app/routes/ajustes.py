"""Configuración: datos de la empresa, bancos, categorías y respaldos."""
import re
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from pathlib import Path

from .. import config, contab, correo, impuestos, respaldo, sistema, vigilancia
from ..tareas import ESTADO
from ..db import Session
from ..models import Banco, CategoriaGasto, Cuenta
from . import check

bp = Blueprint("ajustes", __name__, url_prefix="/configuracion")

CAMPOS_EMPRESA = ["empresa_nombre", "empresa_nit", "empresa_dv", "empresa_direccion", "empresa_ciudad",
                  "empresa_cod_municipio", "empresa_email", "empresa_telefono", "empresa_ciiu", "simple_base",
                  "carpeta_respaldo", "respaldos_a_conservar", "periodo_bloqueado_hasta", "carpeta_vigilada",
                  "correo_servidor", "correo_usuario", "correo_clave", "correo_carpeta", "correo_dias", "correo_filtro",
                  "iva_arrastre_saldo_favor", "acceso_red", "clave_acceso", "actualizaciones_repo", "github_token"]


SECRETOS = {"correo_clave", "github_token", "clave_acceso"}


def _reiniciando(script=None, titulo="Reiniciando el programa…"):
    """Responde una página de espera y reinicia un instante después, cuando la respuesta ya salió."""
    import threading
    threading.Timer(1.5, sistema.reiniciar, args=(script,)).start()
    return render_template("reiniciando.html", titulo=titulo, segundos=25 if script else 10)


@bp.route("/", methods=["GET", "POST"])
def inicio():
    s = Session()
    if request.method == "POST":
        accion = (request.form.getlist("accion") or [""])[-1]  # el botón pulsado va después del campo oculto
        if accion in ("empresa", "probar_correo", "revisar_correo", "revisar_carpeta"):
            # Los botones de prueba también guardan lo escrito: se prueba lo que el usuario ve en pantalla.
            for campo in CAMPOS_EMPRESA:
                if campo in request.form:
                    valor = request.form.get(campo, "").strip()
                    if campo in SECRETOS and not valor:
                        continue  # el campo se muestra vacío por seguridad; vacío = conservar la guardada
                    if campo in ("empresa_nit", "empresa_dv"):
                        valor = re.sub(r"\D", "", valor)
                    if campo == "periodo_bloqueado_hasta" and valor:
                        valor = _fecha_iso(valor)
                        if valor is None:
                            flash("La fecha de bloqueo no es válida; use el selector de fecha.", "error")
                            continue
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
            if accion != "empresa":
                s.commit()
            flash("Datos guardados.", "ok")
        if accion == "banco":
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
            c = (s.get(CategoriaGasto, cid) if cid else None) or CategoriaGasto()
            c.nombre = nombre
            c.cuenta = request.form["cuenta"]
            c.concepto_exogena = request.form.get("concepto_exogena") or "5016"
            c.palabras_clave = request.form.get("palabras_clave") or None
            c.iva_descontable_def = check("iva_descontable_def")
            c.activa = check("activa") if cid else True
            s.add(c)
            flash("Categoría guardada.", "ok")
        elif accion == "probar_correo":
            try:
                flash(correo.probar(s), "ok")
            except Exception as e:  # noqa: BLE001
                flash(f"No se pudo conectar al correo: {correo.explicar_error(e)}", "error")
        elif accion == "revisar_correo":
            res = correo.revisar(s)
            if res.error:
                flash(res.error, "error")
            else:
                flash(f"Correo revisado: {res.revisados} mensaje(s) nuevo(s), {res.importados} documento(s) importado(s).", "ok")
                for asunto, lineas in res.mensajes:
                    flash(f"{asunto}: " + " | ".join(lineas), "info")
        elif accion == "revisar_carpeta":
            res = vigilancia.revisar(s)
            if not res:
                flash("No había archivos nuevos en la carpeta vigilada (o la carpeta no existe).", "info")
            for archivo, ok, msg in res:
                flash(f"{archivo}: {msg}", "ok" if ok else "error")
        elif accion == "restaurar":
            try:
                archivo = request.files.get("archivo_respaldo")
                if archivo is not None and archivo.filename:
                    contenido = archivo.read()
                else:
                    elegido = Path(request.form.get("respaldo_existente", ""))
                    if elegido.name not in {p.name for p in sistema.respaldos_disponibles(s)}:
                        raise ValueError("Seleccione un respaldo.")
                    contenido = (respaldo.carpeta_destino(s) / elegido.name).read_bytes()
                sistema.programar_restauracion(contenido)
                flash("Respaldo listo para restaurar. Reinicie el programa para aplicarlo (botón de abajo).", "ok")
            except Exception as e:  # noqa: BLE001
                flash(f"No se pudo preparar la restauración: {e}", "error")
        elif accion == "reiniciar":
            s.commit()
            return _reiniciando()
        elif accion == "buscar_actualizacion":
            try:
                info = sistema.verificar_actualizacion(s)
                if info["hay_nueva"]:
                    flash(f"Hay una versión nueva: {info['version']} (actual {sistema.version_actual()}). "
                          f"{info['notas'][:400]}", "info")
                    contab.set_config(s, "actualizacion_disponible", info["version"])
                else:
                    flash(f"El programa está al día (versión {sistema.version_actual()}).", "ok")
                    contab.set_config(s, "actualizacion_disponible", "")
            except Exception as e:  # noqa: BLE001
                flash(f"No se pudo consultar: {e}", "error")
        elif accion == "aplicar_actualizacion":
            try:
                info = sistema.verificar_actualizacion(s)
                if not info["hay_nueva"]:
                    raise ValueError("No hay una versión nueva.")
                script = sistema.descargar_actualizacion(s, info)
                respaldo.crear_respaldo(s)
                s.commit()
                return _reiniciando(script, f"Instalando la versión {info['version']}…")
            except Exception as e:  # noqa: BLE001
                flash(f"No se pudo actualizar: {e}", "error")
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
        seccion = request.form.get("seccion") or (accion or "")
        return redirect(url_for("ajustes.inicio") + "#" + seccion)
    valores = {c: contab.config(s, c, "") for c in CAMPOS_EMPRESA + ["ultimo_respaldo"]}
    guardados = {c: bool(valores[c]) for c in SECRETOS}
    for c in SECRETOS:
        valores[c] = ""  # nunca se devuelven al navegador (podría abrirse desde otro equipo de la red)
    anio = date.today().year
    uvts = [(a, impuestos.uvt(a, s), bool(contab.config(s, f"uvt_{a}"))) for a in (anio, anio + 1)]
    return render_template("ajustes.html", v=valores, guardados=guardados, bancos=s.query(Banco).order_by(Banco.id).all(),
                           categorias=s.query(CategoriaGasto).order_by(CategoriaGasto.nombre).all(),
                           cuentas_gasto=s.query(Cuenta).filter(Cuenta.movimiento.is_(True),
                                                                 or_(Cuenta.codigo.like("5%"),
                                                                     Cuenta.codigo.like("15%"))).order_by(Cuenta.codigo).all(),
                           carpeta_respaldo=respaldo.carpeta_destino(s), datos_dir=config.DATOS_DIR, uvts=uvts,
                           estado_tareas=ESTADO, version=sistema.version_actual(), ip_local=sistema.ip_local(),
                           puerto=config.PUERTO, respaldos=sistema.respaldos_disponibles(s),
                           actualizacion=contab.config(s, "actualizacion_disponible", ""),
                           restaurado=current_app.config.get("RESTAURADO"))


def _nueva_subcuenta(s, padre):
    existentes = [c.codigo for c in s.query(Cuenta).filter(Cuenta.codigo.like(f"{padre}__"))]
    n = max((int(c[-2:]) for c in existentes), default=0) + 1
    return f"{padre}{n:02d}"


def _fecha_iso(texto):
    """Acepta 2026-12-31 o 31/12/2026 y devuelve ISO; None si no es una fecha."""
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None
