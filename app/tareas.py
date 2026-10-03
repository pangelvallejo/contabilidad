"""Tareas en segundo plano mientras el programa está abierto: respaldo, carpeta vigilada y correo."""
import threading
import time
from datetime import datetime

from . import correo, respaldo, vigilancia

ESTADO = {"ultima_carpeta": None, "ultimo_correo": None, "errores": []}


def _registrar_error(texto):
    ESTADO["errores"] = (ESTADO["errores"] + [f"{datetime.now():%d/%m %H:%M} {texto}"])[-10:]


def ciclo_carpeta(session_factory):
    s = session_factory()
    try:
        res = vigilancia.revisar(s)
        if res:
            ESTADO["ultima_carpeta"] = (datetime.now(), res)
    except Exception as e:  # noqa: BLE001
        _registrar_error(f"carpeta vigilada: {e}")
    finally:
        s.close()


def ciclo_correo(session_factory):
    s = session_factory()
    try:
        if correo.configuracion(s)["activo"]:
            res = correo.revisar(s)
            ESTADO["ultimo_correo"] = (datetime.now(), res)
            if res.error:
                _registrar_error(res.error)
    except Exception as e:  # noqa: BLE001
        _registrar_error(f"correo: {e}")
    finally:
        s.close()


def ciclo_respaldo(session_factory):
    s = session_factory()
    try:
        if respaldo.respaldo_pendiente(s):
            respaldo.crear_respaldo(s)
    except Exception as e:  # noqa: BLE001
        _registrar_error(f"respaldo: {e}")
    finally:
        s.close()


def iniciar(session_factory, minutos_carpeta=2, minutos_correo=15):
    def bucle():
        ultimo_correo = 0.0
        ultimo_respaldo = 0.0
        while True:
            ahora = time.time()
            ciclo_carpeta(session_factory)
            if ahora - ultimo_correo >= minutos_correo * 60:
                ciclo_correo(session_factory)
                ultimo_correo = ahora
            if ahora - ultimo_respaldo >= 3600:
                ciclo_respaldo(session_factory)
                ultimo_respaldo = ahora
            time.sleep(minutos_carpeta * 60)

    threading.Thread(target=bucle, daemon=True, name="tareas").start()
