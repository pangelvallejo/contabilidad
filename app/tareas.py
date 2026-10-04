"""Tareas en segundo plano mientras el programa está abierto: respaldo, carpeta vigilada y correo."""
import threading
import time
from datetime import datetime

from . import correo, respaldo, vigilancia

ESTADO = {"ultima_carpeta": None, "ultimo_correo": None, "ultimo_resumen": None, "errores": []}


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


def ciclo_resumen(session_factory):
    """El primer día de cada mes (o la primera vez que se abra el programa después) envía el resumen del mes anterior."""
    from . import contab
    s = session_factory()
    try:
        if contab.config(s, "resumen_mensual", "no") != "si" or not correo.configuracion(s)["activo"]:
            return
        hoy = datetime.now().date()
        anio, mes = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
        marca = f"{anio}-{mes:02d}"
        if contab.config(s, "ultimo_resumen", "") == marca:
            return
        enviar_resumen(s, anio, mes)
        contab.set_config(s, "ultimo_resumen", marca)
        s.commit()
        ESTADO["ultimo_resumen"] = datetime.now()
    except Exception as e:  # noqa: BLE001
        _registrar_error(f"resumen mensual: {e}")
    finally:
        s.close()


def texto_resumen(session, anio, mes) -> str:
    from . import contab, informes
    from .formato import pesos
    r = informes.resumen_mensual(session, anio, mes)
    nombre = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
              "noviembre", "diciembre"][mes - 1]
    emp = contab.config(session, "empresa_nombre", "")
    return (f"Resumen de {emp} - {nombre} de {anio}\n\n"
            f"Ingresos del mes (sin IVA): {pesos(r.ingresos)}\n"
            f"Gastos del mes: {pesos(r.gastos)}\n"
            f"Resultado del mes: {pesos(r.ingresos - r.gastos)}\n"
            f"Recaudado de clientes: {pesos(r.recaudado)}\n\n"
            f"Cartera por cobrar al cierre: {pesos(r.cartera)} (vencida: {pesos(r.cartera_vencida)})\n"
            f"Cuentas por pagar a proveedores: {pesos(r.por_pagar)}\n"
            f"Caja y bancos: {pesos(r.efectivo)}\n"
            f"Recibo 2593 estimado del bimestre que incluye el mes: {pesos(r.simple_estimado)}\n\n"
            "Generado automáticamente por el programa de contabilidad.")


def enviar_resumen(session, anio, mes):
    from . import contab
    destino = contab.config(session, "resumen_correo", "") or contab.config(session, "empresa_email", "") \
        or correo.configuracion(session)["correo_usuario"]
    emp = contab.config(session, "empresa_nombre", "")
    correo.enviar(session, destino, f"Resumen mensual {anio}-{mes:02d} · {emp}", texto_resumen(session, anio, mes))
    return destino


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
                ciclo_resumen(session_factory)
                ultimo_respaldo = ahora
            time.sleep(minutos_carpeta * 60)

    threading.Thread(target=bucle, daemon=True, name="tareas").start()
