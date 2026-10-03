"""Carpeta vigilada: los XML/ZIP que se dejen allí se importan solos.

Los archivos procesados se mueven a `importados/` y los que fallan a `errores/` (con un .txt que explica).
"""
import shutil
from pathlib import Path

from . import contab
from .importacion import importar_archivo


def carpeta(session) -> Path | None:
    ruta = contab.config(session, "carpeta_vigilada")
    return Path(ruta) if ruta else None


def revisar(session):
    """Importa los archivos nuevos de la carpeta. Devuelve lista de (archivo, ok, mensaje)."""
    base = carpeta(session)
    resultados = []
    if base is None or not base.is_dir():
        return resultados
    ok_dir, err_dir = base / "importados", base / "errores"
    for f in sorted(base.iterdir()):
        if not f.is_file() or f.suffix.lower() not in (".xml", ".zip") or f.name.startswith("~"):
            continue
        try:
            contenido = f.read_bytes()
        except OSError:
            continue  # todavía se está copiando
        res = importar_archivo(session, f.name, contenido, origen="carpeta")
        exito = any(r.ok for r in res)
        destino = ok_dir if exito else err_dir
        destino.mkdir(exist_ok=True)
        final = destino / f.name
        n = 1
        while final.exists():
            final = destino / f"{f.stem}_{n}{f.suffix}"
            n += 1
        try:
            shutil.move(str(f), str(final))
            if not exito:
                final.with_suffix(final.suffix + ".txt").write_text(
                    "\n".join(f"{r.archivo}: {r.mensaje}" for r in res), encoding="utf-8")
        except OSError:
            pass
        for r in res:
            resultados.append((f.name, r.ok, r.mensaje))
    return resultados
