"""Copias de seguridad automáticas (por defecto en la carpeta de OneDrive)."""
import os
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from . import config

_lock = threading.Lock()


def carpeta_por_defecto() -> Path:
    for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
        if os.environ.get(var):
            return Path(os.environ[var]) / "Respaldos Contabilidad Angel Lecompte"
    onedrive = Path.home() / "OneDrive"
    if onedrive.exists():
        return onedrive / "Respaldos Contabilidad Angel Lecompte"
    return config.DATOS_DIR / "respaldos"


def carpeta_destino(session) -> Path:
    from .contab import config as cfg
    return Path(cfg(session, "carpeta_respaldo") or carpeta_por_defecto())


def crear_respaldo(session) -> Path:
    from .contab import config as cfg, set_config
    with _lock:
        destino = carpeta_destino(session)
        try:
            conservar = max(1, int(cfg(session, "respaldos_a_conservar", "30")))
        except ValueError:
            conservar = 30
        destino.mkdir(parents=True, exist_ok=True)
        nombre = destino / f"respaldo_{datetime.now():%Y%m%d_%H%M%S}.zip"
        with tempfile.TemporaryDirectory() as tmp:
            copia = Path(tmp) / "contabilidad.db"
            origen = sqlite3.connect(config.DB_PATH)
            dest = sqlite3.connect(copia)
            with dest:
                origen.backup(dest)
            dest.close()
            origen.close()
            with zipfile.ZipFile(nombre, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(copia, "contabilidad.db")
                if config.ADJUNTOS_DIR.exists():
                    for f in config.ADJUNTOS_DIR.rglob("*"):
                        if f.is_file():
                            z.write(f, Path("adjuntos") / f.relative_to(config.ADJUNTOS_DIR))
        for viejo in sorted(destino.glob("respaldo_*.zip"))[:-conservar]:
            viejo.unlink(missing_ok=True)
        set_config(session, "ultimo_respaldo", datetime.now().isoformat(timespec="seconds"))
        session.commit()
        return nombre


def respaldo_pendiente(session, horas=24) -> bool:
    from .contab import config as cfg
    ultimo = cfg(session, "ultimo_respaldo")
    if not ultimo:
        return True
    return datetime.now() - datetime.fromisoformat(ultimo) > timedelta(hours=horas)


def iniciar_respaldo_automatico(session_factory):
    """Hilo en segundo plano: revisa cada hora y respalda si pasaron 24 horas."""
    def ciclo():
        while True:
            s = session_factory()
            try:
                if respaldo_pendiente(s):
                    crear_respaldo(s)
            except Exception as e:  # noqa: BLE001 — un fallo de respaldo no debe tumbar la app
                print(f"[respaldo] No se pudo crear el respaldo: {e}")
            finally:
                s.close()
            time.sleep(3600)

    threading.Thread(target=ciclo, daemon=True, name="respaldo").start()
