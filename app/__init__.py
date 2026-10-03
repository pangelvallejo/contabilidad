"""Contabilidad Angel Lecompte S.A.S. — aplicación web local."""
import warnings
from datetime import date

from flask import Flask, abort, send_file

from . import config, formato
from .entorno import asegurar_librerias

asegurar_librerias()  # antes de importar SQLAlchemy: si Windows bloquea sus DLL, usar la versión en Python puro

from .db import Session, init_engine, sincronizar_esquema  # noqa: E402

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")
# Si Windows bloquea Pillow, fpdf2 avisa que no podrá insertar imágenes: el programa no las usa.
warnings.filterwarnings("ignore", message="Pillow could not be imported.*")

VERSION = "1.2.2"


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
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

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
        from .sistema import acceso_red_activo, es_local, misma_origen
        if request.method == "POST" and not misma_origen(request):
            abort(403)
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
                    from .routes import destino_seguro
                    return redirect(destino_seguro(request.form.get("siguiente"), "/"))
                flash("Contraseña incorrecta.", "error")
            return render_template("acceso.html", siguiente=request.args.get("siguiente", "/"))
        return redirect(url_for("acceso", siguiente=request.path))

    @app.route("/acceso", methods=["GET", "POST"])
    def acceso():
        from flask import redirect
        return redirect("/")  # el control real está en before_request; desde el PC local no aplica

    @app.route("/salir")
    def salir():
        from flask import redirect, session as sesion
        sesion.clear()
        return redirect("/")

    @app.route("/favicon.ico")
    def favicon():
        from flask import redirect, url_for
        return redirect(url_for("static", filename="icono.svg"))

    @app.route("/archivo/<path:relativa>")
    def archivo(relativa):
        from .archivos import ruta_absoluta
        try:
            ruta = ruta_absoluta(relativa)
        except ValueError:
            abort(404)
        if not ruta.exists():
            abort(404)
        # XML y ZIP se descargan en vez de mostrarse: un XML recibido de un tercero no debe ejecutarse en el navegador.
        return send_file(ruta, as_attachment=ruta.suffix.lower() in (".xml", ".zip", ".html", ".htm", ".svg"))

    from .routes import (ajustes, bancos, buscar, contabilidad, gastos, impuestos, inicio, planeacion, terceros,
                         ventas)
    for bp in (inicio.bp, ventas.bp, gastos.bp, terceros.bp, impuestos.bp, contabilidad.bp, ajustes.bp, buscar.bp,
               planeacion.bp, bancos.bp):
        app.register_blueprint(bp)

    if respaldo_automatico:  # modo normal (no pruebas): depreciaciones del mes y tareas en segundo plano
        try:
            from .planeacion import causar_depreciaciones
            causar_depreciaciones(Session(), forzar=True)  # automática, como la causación del impuesto
        except Exception as e:  # noqa: BLE001
            print(f"[depreciación] {e}")
        finally:
            Session.remove()
        from sqlalchemy.orm import sessionmaker
        from .bitacora import activar
        from .db import engine
        from .tareas import iniciar
        fabrica = sessionmaker(bind=engine)
        activar(fabrica)  # lo que importan el correo y la carpeta vigilada también queda en el historial
        iniciar(fabrica)
    return app
