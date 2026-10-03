"""Crea el paquete portable para Windows: el programa con Python incluido, sin instalar nada.

Uso (en Windows, con Python instalado solo para construir):  python herramientas/crear_paquete.py
Resultado: dist/Contabilidad-<versión>-windows.zip  →  se descomprime y se abre Contabilidad.bat.
Este mismo script lo ejecuta GitHub Actions al publicar una versión (ver .github/workflows/paquete.yml).
"""
import io
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.7"
URL_PYTHON = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
URL_GET_PIP = "https://bootstrap.pypa.io/get-pip.py"
INCLUIR = ["app", "requirements.txt", "README.md", "iniciar.bat", "crear_acceso_directo.ps1"]


def version():
    texto = (RAIZ / "app" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'VERSION = "([^"]+)"', texto).group(1)


def main():
    dist = RAIZ / "dist"
    carpeta = dist / "Contabilidad"
    if carpeta.exists():
        shutil.rmtree(carpeta)
    carpeta.mkdir(parents=True)
    for nombre in INCLUIR:
        origen = RAIZ / nombre
        if origen.is_dir():
            shutil.copytree(origen, carpeta / nombre, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy(origen, carpeta / nombre)

    print("Descargando Python embebido…")
    py_dir = carpeta / "python"
    with urllib.request.urlopen(URL_PYTHON) as r:
        zipfile.ZipFile(io.BytesIO(r.read())).extractall(py_dir)
    # Habilitar site-packages en la distribución embebida
    pth = next(py_dir.glob("python3*._pth"))
    # Habilita site-packages y agrega la carpeta del programa (..) para que `python -m app` encuentre `app/`:
    # el Python embebido fija safe_path y no incluye el directorio actual.
    pth.write_text(pth.read_text().replace("#import site", "import site").rstrip() + "\n..\n", encoding="utf-8")
    print("Instalando pip y dependencias…")
    with urllib.request.urlopen(URL_GET_PIP) as r:
        (py_dir / "get-pip.py").write_bytes(r.read())
    python = py_dir / "python.exe"
    subprocess.check_call([str(python), str(py_dir / "get-pip.py"), "--no-warn-script-location", "-q"])
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", "--no-warn-script-location",
                           "-r", str(carpeta / "requirements.txt")])
    (py_dir / "get-pip.py").unlink()
    # Los aceleradores compilados de estas librerías no están firmados y el "Control de aplicaciones inteligente"
    # de Windows 11 los bloquea; se retiran y queda la versión en Python puro (misma funcionalidad).
    subprocess.check_call([str(python), "-B", "-c",
                           "from app.entorno import retirar_binarios, CON_VERSION_PURA; "
                           "print('binarios retirados:', [(n, retirar_binarios(n)) for n in CON_VERSION_PURA])"],
                          cwd=str(carpeta))
    # Prueba de humo: el paquete debe poder importar el programa con su propio Python
    subprocess.check_call([str(python), "-B", "-c", "import app, flask, sqlalchemy, openpyxl, fpdf; print('paquete OK', app.VERSION)"],
                          cwd=str(carpeta))
    for cache in carpeta.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    # write_bytes: en Windows, write_text convertiría "\n" en "\r\n" y el .bat quedaría con "\r\r\n".
    (carpeta / "Contabilidad.bat").write_bytes(
        b"@echo off\r\n"
        b"title Contabilidad Angel Lecompte\r\n"
        b'cd /d "%~dp0"\r\n'
        b"python\\python.exe -m app\r\n"
        b"pause\r\n")
    salida = dist / f"Contabilidad-{version()}-windows.zip"
    if salida.exists():
        salida.unlink()
    shutil.make_archive(str(salida.with_suffix("")), "zip", dist, "Contabilidad")
    print(f"Paquete creado: {salida}")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Este paquete se construye en Windows (o en GitHub Actions con windows-latest).")
    main()
