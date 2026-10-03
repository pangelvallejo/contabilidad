import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture()
def app(tmp_path):
    from app import create_app
    from app.db import Session
    aplicacion = create_app(tmp_path / "datos")
    aplicacion.config["TESTING"] = True
    yield aplicacion
    Session.remove()


@pytest.fixture()
def cliente_web(app):
    return app.test_client()


@pytest.fixture()
def s(app):
    from app.db import Session
    return Session()
