import pytest
from fastapi.testclient import TestClient

from server.main import Settings, create_app


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(database=str(tmp_path / "test.db")))


@pytest.fixture
def api(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "fake home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    return home
