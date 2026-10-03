"""Almacenamiento de soportes (XML, PDF, imágenes, certificados)."""
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import config

EXTENSIONES_PERMITIDAS = {".xml", ".pdf", ".png", ".jpg", ".jpeg", ".heic", ".webp", ".zip"}


def _ascii(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._-]+", "_", texto).strip("._")


def nombre_seguro(nombre: str) -> str:
    """Nombre de archivo ASCII sin rutas; conserva la extensión aunque el nombre tenga otros alfabetos."""
    p = Path(nombre.replace("\\", "/"))
    ext = p.suffix.lower() if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", p.suffix) else ""
    base = _ascii(p.stem) or "archivo"
    return (base[-110:] + ext) or "archivo"


def guardar(carpeta: str, nombre: str, contenido: bytes) -> str:
    """Guarda el archivo y devuelve la ruta relativa a la carpeta de adjuntos."""
    ext = Path(nombre).suffix.lower()
    if ext not in EXTENSIONES_PERMITIDAS:
        raise ValueError(f"Tipo de archivo no permitido: {ext or 'sin extensión'}")
    destino = config.ADJUNTOS_DIR / carpeta / datetime.now().strftime("%Y-%m")
    destino.mkdir(parents=True, exist_ok=True)
    final = destino / f"{datetime.now().strftime('%d%H%M%S%f')}_{nombre_seguro(nombre)}"
    final.write_bytes(contenido)
    return final.relative_to(config.ADJUNTOS_DIR).as_posix()


def ruta_absoluta(relativa: str) -> Path:
    ruta = (config.ADJUNTOS_DIR / relativa).resolve()
    if config.ADJUNTOS_DIR.resolve() not in ruta.parents:
        raise ValueError("Ruta de archivo no válida")
    return ruta


def guardar_upload(carpeta, archivo) -> str | None:
    """Guarda un FileStorage de Flask si viene con contenido."""
    if archivo is None or not archivo.filename:
        return None
    return guardar(carpeta, archivo.filename, archivo.read())
