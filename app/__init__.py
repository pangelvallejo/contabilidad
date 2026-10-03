"""Contabilidad Angel Lecompte S.A.S. — aplicación web local."""
import warnings
from datetime import date

from flask import Flask, abort, send_file

from . import config, formato
from .db import Session, init_engine, sincronizar_esquema

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

VERSION = "1.2.0"


def create_app(datos_dir=None, respaldo_automatico=False):
    if datos_dir is not None:
        from pathlib import Path
        config.DATOS_DIR = Path(datos_dir)
        config.DB_PATH = config.DATOS_DIR / "contabilidad.db"
        config.ADJUNTOS_DIR = config.DATOS_DIR / "adjuntos"
    config.DATOS_DIR.mkdir(parents=True, exist_ok=True)
    config.ADJUNTOS_DIR.mkdir(parents=True, exist_ok=True)
    from .sistema import aplicar_restauracion_pendiente, clave_secreta
    restaurado = aplicar_restauracion_pendiente()

    init_engine(f"sqlite:///{config.DB_PATH}")
    sincronizar_esquema()
    from .seed import sembrar
    sembrar(Session())
    Session.remove()

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    app.secret_key = clave_secreta()
    app.config["RESTAURADO"] = restaurado

    app.jinja_env.filters["pesos"] = formato.pesos
    app.jinja_env.filters["numero"] = formato.numero
    app.jinja_env.filters["fecha"] = formato.fecha
    app.jinja_env.filters["fecha_larga"] = formato.fecha_larga
    app.jinja_env.filters["entrada"] = formato.entrada
    app.jinja_env.filters["pct"] = lambda v: f"{float(v) * 100:.1f}".replace(".", ",") + " %"

    @app.teardown_appcontext
    def _cerrar(_exc):
        Session.remove()

    @app.context_processor
    def _contexto():
        from .contab import config as cfg
        s = Session()
        return {"empresa_nombre": cfg(s, "empresa_nombre", ""), "hoy": date.today(), "version": VERSION}

    @app.before_request
    def _control_acceso():
        """Desde el propio computador no pide clave; desde otro equipo de la red, sí (si está activado)."""
        from flask import flash, redirect, render_template, request, session as sesion, url_for
        from .sistema import acceso_red_activo, es_local
        if request.endpoint == "static" or es_local(request.remote_addr or ""):
            return None
        s = Session()
        if not acceso_red_activo(s):
            abort(403)
        if sesion.get("autenticado"):
            return None
        if request.path == "/acceso":
            if request.method == "POST":
                from .contab import config as cfg
                import hmac
                if hmac.compare_digest(request.form.get("clave", ""), cfg(s, "clave_acceso", "")):
                    sesion["autenticado"] = True
                    sesion.permanent = True
                    destino = request.form.get("siguiente") or "/"
                    return redirect(destino if destino.startswith("/") else "/")
                flash("Contraseña incorrecta.", "error")
            return render_template("acceso.html", siguiente=request.args.get("siguiente", "/"))
        return redirect(url_for("acceso", siguiente=request.path))

    @app.route("/acceso", methods=["GET", "POST"])
    def acceso():
        from flask import redirect
        return redirect("/")  # el control real está en before_request; desde el PC local no aplica

    @app.route("/archivo/<path:relativa>")
    def archivo(relativa):
        from .archivos import ruta_absoluta
        try:
            ruta = ruta_absoluta(relativa)
        except ValueError:
            abort(404)
        if not ruta.exists():
            abort(404)
        return send_file(ruta)

    from .routes import (ajustes, bancos, buscar, contabilidad, gastos, impuestos, inicio, planeacion, terceros,
                         ventas)
    for bp in (inicio.bp, ventas.bp, gastos.bp, terceros.bp, impuestos.bp, contabilidad.bp, ajustes.bp, buscar.bp,
               planeacion.bp, bancos.bp):
        app.register_blueprint(bp)

    if respaldo_automatico:  # modo normal (no pruebas): depreciaciones del mes y tareas en segundo plano
        try:
            from .planeacion import causar_depreciaciones
            causar_depreciaciones(Session())
        except Exception as e:  # noqa: BLE001
            print(f"[depreciación] {e}")
        finally:
            Session.remove()
        from sqlalchemy.orm import sessionmaker
        from .db import engine
        from .tareas import iniciar
        iniciar(sessionmaker(bind=engine))
    return app
