"""Compatibilidad con equipos donde Windows bloquea componentes compilados sin firma.

El "Control de aplicaciones inteligente" de Windows 11 (y las políticas WDAC de empresa) impiden cargar las DLL
(.pyd) que traen algunas librerías de Python: el programa fallaba al arrancar con
"DLL load failed ... An Application Control policy has blocked this file". Esas librerías incluyen la misma
lógica en Python puro, así que basta retirar el binario bloqueado y volver a importarlas.
"""
import importlib
import importlib.util
import sys
from pathlib import Path

# Librerías cuyos módulos compilados son solo aceleradores opcionales (tienen el mismo código en .py):
# paquete -> módulos que hay que probar a importar (los que cargan el binario).
CON_VERSION_PURA = {
    "sqlalchemy": ["sqlalchemy"],
    "markupsafe": ["markupsafe"],
    "fontTools": ["fontTools.misc.bezierTools", "fontTools.pens.momentsPen", "fontTools.cu2qu.cu2qu",
                  "fontTools.qu2cu.qu2cu", "fontTools.varLib.iup", "fontTools.feaLib.lexer"],
}
SENALES_BLOQUEO = ("dll load failed", "application control", "blocked", "bloquead")


def _bloqueo(e: BaseException) -> bool:
    texto = str(e).lower()
    return any(s in texto for s in SENALES_BLOQUEO)


def _carpeta(nombre: str) -> Path | None:
    spec = importlib.util.find_spec(nombre)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def _olvidar(nombre: str):
    for clave in [k for k in sys.modules if k == nombre or k.startswith(nombre + ".")]:
        del sys.modules[clave]
    importlib.invalidate_caches()


def retirar_binarios(nombre: str) -> int:
    """Elimina los módulos compilados (.pyd/.so) de una librería que trae su versión en Python puro."""
    carpeta = _carpeta(nombre)
    if carpeta is None:
        return 0
    quitados = 0
    for binario in list(carpeta.rglob("*.pyd")) + list(carpeta.rglob("*.so")):
        try:
            binario.unlink()
            quitados += 1
        except OSError:
            pass
    return quitados


def asegurar_librerias(avisar=print) -> list[str]:
    """Importa las librerías críticas; si Windows bloquea sus binarios, pasa a la versión en Python puro."""
    reparadas = []
    for nombre, modulos in CON_VERSION_PURA.items():
        try:
            for modulo in modulos:
                importlib.import_module(modulo)
            continue
        except ImportError as e:
            if not _bloqueo(e):
                raise
        _olvidar(nombre)
        quitados = retirar_binarios(nombre)
        _olvidar(nombre)
        for modulo in modulos:
            importlib.import_module(modulo)  # si vuelve a fallar, el error original sube tal cual
        reparadas.append(nombre)
        avisar(f"Aviso: Windows bloqueó los componentes compilados de {nombre}; el programa usará su versión en "
               f"Python puro ({quitados} archivo(s) retirados). Esto solo pasa la primera vez.")
    return reparadas
