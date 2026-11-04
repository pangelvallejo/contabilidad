"""Arranque: `python -m app` abre el programa en el navegador.

Con `pythonw.exe` (acceso directo del escritorio) no hay ventana negra: los mensajes van a programa.log en la
carpeta de datos y un error de arranque se muestra en un cuadro de diálogo. Si el programa ya está abierto, un
segundo doble clic solo abre el navegador.
"""
import json
import logging
import sys
import threading
import webbrowser
from urllib.request import urlopen

from . import config, create_app

SIN_CONSOLA = sys.stdout is None or sys.stderr is None


def ya_abierto(url: str) -> bool:
    """¿Hay una instancia del programa respondiendo en esa dirección?"""
    try:
        with urlopen(url.rstrip("/") + "/salud", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("programa") == "contabilidad"
    except Exception:  # noqa: BLE001 — nada escucha, u otro programa ocupa el puerto
        return False


def _registro_sin_consola():
    """Sin consola, stdout y stderr van a programa.log (se recorta cuando pasa de 1 MB)."""
    config.DATOS_DIR.mkdir(parents=True, exist_ok=True)
    ruta = config.DATOS_DIR / "programa.log"
    try:
        if ruta.exists() and ruta.stat().st_size > 1_000_000:
            ruta.write_text(ruta.read_text(encoding="utf-8", errors="replace")[-200_000:], encoding="utf-8")
    except OSError:
        pass
    archivo = open(ruta, "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115 — vive todo el proceso
    sys.stdout = sys.stderr = archivo
    return ruta


def avisar(titulo: str, texto: str):
    """Mensaje para el usuario: cuadro de diálogo en Windows sin consola; si no, por consola."""
    if sys.platform == "win32" and SIN_CONSOLA:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x10)  # MB_ICONERROR
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"\n  {titulo}: {texto}\n")


def main():
    registro = _registro_sin_consola() if SIN_CONSOLA else None
    url = f"http://127.0.0.1:{config.PUERTO}/"
    if ya_abierto(url):
        webbrowser.open(url)  # ya está corriendo: solo se abre el navegador
        return
    try:
        app = create_app(respaldo_automatico=True)
    except Exception as e:  # noqa: BLE001 — base dañada, carpeta sin permiso de escritura, disco lleno
        avisar("No se pudo abrir la contabilidad",
               f"{e}\n\nDatos en {config.DATOS_DIR}. Si la base está dañada, cambie el nombre de contabilidad.db y copie "
               "en su lugar el contabilidad.db de un respaldo (ZIP de la carpeta de respaldos)."
               + (f"\n\nDetalle en {registro}" if registro else ""))
        if not SIN_CONSOLA:
            try:
                input("  Pulse Entrar para salir.")
            except EOFError:
                pass
        raise SystemExit(1) from e
    from .db import Session
    from .sistema import acceso_red_activo, ip_local
    en_red = acceso_red_activo(Session())
    Session.remove()
    print(f"\n  Contabilidad Angel Lecompte S.A.S.\n  Abierto en {url}\n  Datos en {config.DATOS_DIR}")
    if en_red:
        print(f"  Desde otro equipo o celular de la red: http://{ip_local()}:{config.PUERTO}/")
    print("  Para salir use Otros → Cerrar el programa (o cierre esta ventana).\n")
    # Uso local de un solo usuario: se ocultan los mensajes técnicos del servidor.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        app.run(host="0.0.0.0" if en_red else "127.0.0.1", port=config.PUERTO, debug=False, use_reloader=False,
                threaded=True)
    except OSError as e:
        avisar("No se pudo abrir la contabilidad",
               f"El puerto {config.PUERTO} está ocupado por otro programa ({e}). Cierre ese programa o cambie el puerto "
               "con la variable CONTA_PUERTO.")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
