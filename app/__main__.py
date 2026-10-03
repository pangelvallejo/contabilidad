"""Arranque: `python -m app` abre el programa en el navegador."""
import logging
import threading
import webbrowser

from . import config, create_app


def main():
    app = create_app(respaldo_automatico=True)
    from .db import Session
    from .sistema import acceso_red_activo, ip_local
    en_red = acceso_red_activo(Session())
    Session.remove()
    url = f"http://127.0.0.1:{config.PUERTO}/"
    print(f"\n  Contabilidad Angel Lecompte S.A.S.\n  Abierto en {url}\n  Datos en {config.DATOS_DIR}")
    if en_red:
        print(f"  Desde otro equipo o celular de la red: http://{ip_local()}:{config.PUERTO}/")
    print("  Cierre esta ventana para salir.\n")
    # Uso local de un solo usuario: se ocultan los mensajes técnicos del servidor.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0" if en_red else "127.0.0.1", port=config.PUERTO, debug=False, use_reloader=False,
            threaded=True)


if __name__ == "__main__":
    main()
