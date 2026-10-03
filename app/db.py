"""Conexión SQLite y sincronización ligera del esquema."""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


engine = None
Session = scoped_session(sessionmaker(expire_on_commit=False))


def init_engine(url: str):
    global engine
    # timeout: espera hasta 30 s si otra conexión (hilo de tareas) tiene la base bloqueada.
    engine = create_engine(url, future=True, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        # SQLAlchemy controla las transacciones (ver _begin): el driver no debe abrirlas por su cuenta.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin(conn):
        # BEGIN IMMEDIATE toma el bloqueo de escritura desde el inicio: evita "database is locked" cuando una
        # transacción empieza leyendo y luego escribe mientras otro hilo ya escribió (BUSY_SNAPSHOT en WAL).
        conn.exec_driver_sql("BEGIN IMMEDIATE")

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
    # Primero se inspecciona (conexión de lectura) y después se altera: con BEGIN IMMEDIATE dos conexiones
    # abiertas a la vez se bloquearían entre sí.
    insp = inspect(engine)
    faltantes = []
    for tabla in Base.metadata.sorted_tables:
        existentes = {c["name"] for c in insp.get_columns(tabla.name)}
        faltantes += [(tabla, col) for col in tabla.columns if col.name not in existentes]
    if not faltantes:
        return
    with engine.begin() as conn:
        for tabla, col in faltantes:
            tipo = col.type.compile(engine.dialect)
            default = ""
            if col.default is not None and getattr(col.default, "is_scalar", False):
                valor = col.default.arg
                if isinstance(valor, bool):
                    valor = int(valor)
                default = f" DEFAULT {valor!r}" if isinstance(valor, str) else f" DEFAULT {valor}"
            conn.execute(text(f'ALTER TABLE "{tabla.name}" ADD COLUMN "{col.name}" {tipo}{default}'))
