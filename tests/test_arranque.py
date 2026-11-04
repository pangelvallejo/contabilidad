"""Arranque como aplicación: detección de instancia abierta, registro sin consola, cierre desde el menú."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import __main__ as arranque
from app import config, sistema


def _servidor(respuesta: bytes, codigo=200):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(respuesta)

        def log_message(self, *a):  # silencio
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_ya_abierto_distingue_el_programa_de_otros_servicios():
    propio = _servidor(json.dumps({"programa": "contabilidad", "version": "x"}).encode())
    ajeno = _servidor(b"<html>otro servicio</html>")
    try:
        assert arranque.ya_abierto(f"http://127.0.0.1:{propio.server_address[1]}/")
        assert not arranque.ya_abierto(f"http://127.0.0.1:{ajeno.server_address[1]}/")
    finally:
        propio.shutdown()
        ajeno.shutdown()
    libre = _servidor(b"")
    puerto = libre.server_address[1]
    libre.shutdown()
    libre.server_close()
    assert not arranque.ya_abierto(f"http://127.0.0.1:{puerto}/")  # nadie escucha


def test_salud_y_cierre_desde_el_menu(cliente_web, app, monkeypatch):
    r = cliente_web.get("/salud")
    assert r.status_code == 200 and r.get_json()["programa"] == "contabilidad"
    html = cliente_web.get("/").get_data(as_text=True)
    assert "Cerrar el programa" in html  # desde el propio computador
    apagados = []
    monkeypatch.setattr(sistema, "apagar", lambda: apagados.append(True))
    # Desde la red no se puede apagar el programa
    r = cliente_web.post("/salir-programa", environ_base={"REMOTE_ADDR": "192.168.1.40"})
    assert r.status_code in (302, 403)
    assert cliente_web.get("/salir-programa").status_code == 405
    r = cliente_web.post("/salir-programa")
    assert r.status_code == 200 and "se cerró" in r.get_data(as_text=True) and "refresh" not in r.get_data(as_text=True)
    import time
    time.sleep(1.2)
    assert apagados == [True]


def test_registro_sin_consola(app, monkeypatch):
    import sys
    viejo_out, viejo_err = sys.stdout, sys.stderr
    try:
        ruta = arranque._registro_sin_consola()
        print("hola desde el programa")
        sys.stdout.flush()
    finally:
        sys.stdout, sys.stderr = viejo_out, viejo_err
    assert ruta == config.DATOS_DIR / "programa.log" and "hola desde el programa" in ruta.read_text(encoding="utf-8")
