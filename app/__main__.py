"""Arranque: `python -m app` abre el programa en el navegador."""
import logging
import threading
import webbrowser

from . import config, create_app


def main():
    app = create_app(respaldo_automatico=True)
    url = f"http://127.0.0.1:{config.PUERTO}/"
    print(f"\n  Contabilidad Angel Lecompte S.A.S.\n  Abierto en {url}\n  Datos en {config.DATOS_DIR}\n"
          "  Cierre esta ventana para salir.\n")
    # Uso local de un solo usuario: se ocultan los mensajes técnicos del servidor.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=config.PUERTO, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
