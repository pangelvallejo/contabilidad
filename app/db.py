"""Conexión SQLite y sincronización ligera del esquema."""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


engine = None
Session = scoped_session(sessionmaker(expire_on_commit=False))


def init_engine(url: str):
    global engine
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    Session.remove()
    Session.configure(bind=engine)
    from .bitacora import activar
    activar(Session.session_factory)
    return engine


def sincronizar_esquema():
    """Crea tablas nuevas y agrega columnas nuevas a tablas existentes.

    Permite actualizar el programa sin perder datos cuando una versión agrega campos.
    """
    from . import models  # noqa: F401  (registra los modelos)

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for tabla in Base.metadata.sorted_tables:
            existentes = {c["name"] for c in insp.get_columns(tabla.name)}
            for col in tabla.columns:
                if col.name in existentes:
                    continue
                tipo = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    valor = col.default.arg
                    if isinstance(valor, bool):
                        valor = int(valor)
                    default = f" DEFAULT {valor!r}" if isinstance(valor, str) else f" DEFAULT {valor}"
                conn.execute(text(f'ALTER TABLE "{tabla.name}" ADD COLUMN "{col.name}" {tipo}{default}'))
