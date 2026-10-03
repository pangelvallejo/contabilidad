"""Actualizaciones, restauración de respaldos, reinicio y acceso desde la red local."""
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from . import config, contab

RAIZ = Path(__file__).resolve().parents[1]  # carpeta del programa (donde están iniciar.bat y app/)
REPO_DEFECTO = "pangelvallejo/contabilidad"
ARCHIVO_RESTAURAR = "restaurar.zip"


# ------------------------------------------------------------------ versión y actualizaciones

def version_actual():
    from . import VERSION
    return VERSION


def _tupla(v):
    return tuple(int(x) for x in str(v).lstrip("vV").split(".") if x.isdigit())


def es_mas_nueva(remota, local):
    return _tupla(remota) > _tupla(local)


def _repo(session):
    return contab.config(session, "actualizaciones_repo", REPO_DEFECTO)


def _peticion(url, session=None, aceptar="application/vnd.github+json"):
    cab = {"Accept": aceptar, "User-Agent": "contabilidad-angel-lecompte"}
    token = contab.config(session, "github_token") if session is not None else None
    if token:
        cab["Authorization"] = f"Bearer {token}"
    return Request(url, headers=cab)


def verificar_actualizacion(session):
    """Consulta el último lanzamiento en GitHub. Devuelve dict con version, hay_nueva, url_zip, notas."""
    url = f"https://api.github.com/repos/{_repo(session)}/releases/latest"
    with urlopen(_peticion(url, session), timeout=20) as r:
        datos = json.loads(r.read().decode("utf-8"))
    version = datos.get("tag_name", "")
    # Preferir el paquete portable (lleva Python); si no hay, el código fuente del lanzamiento.
    activo = next((a for a in datos.get("assets", []) if a["name"].lower().endswith(".zip")), None)
    url_zip = activo["browser_download_url"] if activo else datos.get("zipball_url")
    return {"version": version, "hay_nueva": es_mas_nueva(version, version_actual()), "url_zip": url_zip,
            "notas": datos.get("body") or "", "portable": activo is not None, "nombre": activo["name"] if activo else ""}


def descargar_actualizacion(session, info):
    """Descarga el ZIP a la carpeta de datos y deja listo el script que lo aplica al reiniciar."""
    destino = config.DATOS_DIR / "actualizacion"
    if destino.exists():
        import shutil
        shutil.rmtree(destino, ignore_errors=True)
    destino.mkdir(parents=True)
    with urlopen(_peticion(info["url_zip"], session, aceptar="application/octet-stream"), timeout=300) as r:
        contenido = r.read()
    return preparar_actualizacion(contenido, destino)


def preparar_actualizacion(contenido_zip: bytes, destino: Path) -> Path:
    """Extrae el ZIP y localiza la carpeta que contiene `app/`. Devuelve la ruta del script de aplicación."""
    with zipfile.ZipFile(io.BytesIO(contenido_zip)) as z:
        z.extractall(destino)
    origen = next((p.parent for p in destino.rglob("app/__init__.py")), None)
    if origen is None:
        raise ValueError("El paquete descargado no contiene el programa (carpeta app/).")
    script = destino / "aplicar_actualizacion.bat"
    # Copia todo menos los datos del usuario y el entorno de Python ya instalado; luego reinicia.
    script.write_text(
        "@echo off\r\n"
        "timeout /t 3 /nobreak >nul\r\n"
        f'robocopy "{origen}" "{RAIZ}" /E /XD .venv python __pycache__ .git /XF contabilidad.db /NFL /NDL /NJH /NJS >nul\r\n'
        f'cd /d "{RAIZ}"\r\n'
        'if exist Contabilidad.bat (start "" Contabilidad.bat) else (start "" iniciar.bat)\r\n',
        encoding="utf-8")
    return script


# ------------------------------------------------------------------ reinicio

def reiniciar(script: Path | None = None):
    """Lanza el script indicado (o el iniciador) en una ventana nueva y cierra este proceso."""
    if sys.platform == "win32":
        objetivo = script or next((RAIZ / n for n in ("Contabilidad.bat", "iniciar.bat") if (RAIZ / n).exists()), None)
        if objetivo is None:
            raise RuntimeError("No se encontró el iniciador del programa.")
        subprocess.Popen(["cmd", "/c", "start", "", str(objetivo)], cwd=str(objetivo.parent),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    else:
        if script is not None:
            subprocess.Popen(["sh", "-c", f"sleep 3; cd '{RAIZ}'; cp -r '{script.parent}'/*/app . 2>/dev/null; "
                                         f"{sys.executable} -m app"], start_new_session=True)
        else:
            subprocess.Popen([sys.executable, "-m", "app"], cwd=str(RAIZ), start_new_session=True)
    os._exit(0)


# ------------------------------------------------------------------ restauración de respaldos

def respaldos_disponibles(session):
    from .respaldo import carpeta_destino
    carpeta = carpeta_destino(session)
    if not carpeta.exists():
        return []
    return sorted(carpeta.glob("respaldo_*.zip"), reverse=True)[:30]


def programar_restauracion(contenido_zip: bytes):
    """Valida el ZIP y lo deja en la carpeta de datos; se aplica al siguiente arranque (con la base cerrada)."""
    with zipfile.ZipFile(io.BytesIO(contenido_zip)) as z:
        if "contabilidad.db" not in z.namelist():
            raise ValueError("El archivo no es un respaldo del programa (no contiene contabilidad.db).")
    (config.DATOS_DIR / ARCHIVO_RESTAURAR).write_bytes(contenido_zip)


def aplicar_restauracion_pendiente():
    """Se llama antes de abrir la base: si hay un respaldo programado, conserva la base actual y lo restaura."""
    pendiente = config.DATOS_DIR / ARCHIVO_RESTAURAR
    if not pendiente.exists():
        return None
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    if config.DB_PATH.exists():
        config.DB_PATH.rename(config.DATOS_DIR / f"contabilidad_antes_de_restaurar_{marca}.db")
    for sufijo in ("-wal", "-shm"):
        extra = Path(str(config.DB_PATH) + sufijo)
        if extra.exists():
            extra.unlink()
    with zipfile.ZipFile(pendiente) as z:
        z.extract("contabilidad.db", config.DATOS_DIR)
        for n in z.namelist():
            if n.startswith("adjuntos/") and not n.endswith("/"):
                z.extract(n, config.DATOS_DIR)
    pendiente.unlink()
    return marca


# ------------------------------------------------------------------ acceso desde la red local

def clave_secreta():
    """Clave de sesión persistente (necesaria para que el inicio de sesión sobreviva a reinicios)."""
    archivo = config.DATOS_DIR / "clave.secreta"
    if not archivo.exists():
        archivo.write_text(secrets.token_hex(32))
    return archivo.read_text().strip()


def acceso_red_activo(session):
    return contab.config(session, "acceso_red", "no") == "si" and bool(contab.config(session, "clave_acceso"))


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def es_local(direccion: str) -> bool:
    return direccion in ("127.0.0.1", "::1", "localhost")
