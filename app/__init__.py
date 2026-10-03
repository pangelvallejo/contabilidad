"""Contabilidad Angel Lecompte S.A.S. — aplicación web local."""
import warnings
from datetime import date

from flask import Flask, abort, send_file

from . import config, formato
from .db import Session, init_engine, sincronizar_esquema

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")

VERSION = "1.0.0"


def create_app(datos_dir=None, respaldo_automatico=False):
    if datos_dir is not None:
        from pathlib import Path
        config.DATOS_DIR = Path(datos_dir)
        config.DB_PATH = config.DATOS_DIR / "contabilidad.db"
        config.ADJUNTOS_DIR = config.DATOS_DIR / "adjuntos"
    config.DATOS_DIR.mkdir(parents=True, exist_ok=True)
    config.ADJUNTOS_DIR.mkdir(parents=True, exist_ok=True)

    init_engine(f"sqlite:///{config.DB_PATH}")
    sincronizar_esquema()
    from .seed import sembrar
    sembrar(Session())
    Session.remove()

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    app.secret_key = "contabilidad-local"  # solo se usa para mensajes flash; la app escucha en 127.0.0.1

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

    from .routes import contabilidad, gastos, inicio, impuestos, terceros, ajustes, ventas
    for bp in (inicio.bp, ventas.bp, gastos.bp, terceros.bp, impuestos.bp, contabilidad.bp, ajustes.bp):
        app.register_blueprint(bp)

    if respaldo_automatico:
        from sqlalchemy.orm import sessionmaker
        from .db import engine
        from .respaldo import iniciar_respaldo_automatico
        iniciar_respaldo_automatico(sessionmaker(bind=engine))
    return app
