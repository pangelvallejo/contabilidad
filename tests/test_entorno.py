"""Simula el bloqueo de DLL de Windows (Control de aplicaciones) y comprueba que el programa arranca igual."""
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

GUION = r'''
import importlib.machinery, importlib.util, sys, shutil
from pathlib import Path
tmp = Path(sys.argv[1]); raiz = sys.argv[2]
import sqlalchemy, markupsafe, fontTools, PIL
for m in (sqlalchemy, markupsafe, fontTools, PIL):
    shutil.copytree(Path(m.__file__).parent, tmp / "site" / m.__name__)
for k in [k for k in sys.modules if k.split(".")[0] in ("sqlalchemy", "markupsafe", "fontTools", "PIL")]:
    del sys.modules[k]
sys.path.insert(0, str(tmp / "site")); sys.path.insert(0, raiz)

class Bloqueador(importlib.abc.MetaPathFinder):
    """Como Windows: todo módulo compilado dentro de la copia falla al cargarse."""
    def find_spec(self, nombre, ruta, destino=None):
        spec = importlib.machinery.PathFinder.find_spec(nombre, ruta, destino)
        if spec and spec.origin and str(spec.origin).startswith(str(tmp)) and isinstance(spec.loader, importlib.machinery.ExtensionFileLoader):
            class Loader(importlib.abc.Loader):
                def create_module(self, spec):
                    raise ImportError(f"DLL load failed while importing {nombre.rsplit('.', 1)[-1]}: An Application Control policy has blocked this file.")
                def exec_module(self, module): pass
            spec.loader = Loader()
        return spec
import importlib.abc
sys.meta_path.insert(0, Bloqueador())
try:
    import sqlalchemy
    raise SystemExit("la simulación no bloqueó nada")
except ImportError as e:
    assert "Application Control" in str(e), e
for k in [k for k in sys.modules if k.split(".")[0] == "sqlalchemy"]:
    del sys.modules[k]
import app  # noqa: F401  su arranque debe reparar la librería
import sqlalchemy
assert str(Path(sqlalchemy.__file__)).startswith(str(tmp)), sqlalchemy.__file__
for paq in ("sqlalchemy", "fontTools"):
    assert not list((tmp / "site" / paq).rglob("*.so")) and not list((tmp / "site" / paq).rglob("*.pyd")), paq
import fpdf  # sin Pillow (sin versión pura) fpdf2 avisa pero funciona para PDF sin imágenes
try:
    import PIL.Image
    raise SystemExit("Pillow debía quedar bloqueado en la simulación")
except ImportError:
    pass
from app import create_app
aplicacion = create_app(tmp / "datos")
c = aplicacion.test_client()
assert c.get("/").status_code == 200
assert c.get("/contabilidad/balance?pdf=1").status_code == 200  # el PDF también sale
print("OK puro")
'''


def test_arranca_aunque_windows_bloquee_las_dll(tmp_path):
    guion = tmp_path / "guion.py"
    guion.write_text(GUION, encoding="utf-8")
    r = subprocess.run([sys.executable, str(guion), str(tmp_path), str(RAIZ)], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and "OK puro" in r.stdout, r.stdout + r.stderr
    assert "Windows bloqueó los componentes compilados de sqlalchemy" in r.stdout
    shutil.rmtree(tmp_path / "site", ignore_errors=True)
