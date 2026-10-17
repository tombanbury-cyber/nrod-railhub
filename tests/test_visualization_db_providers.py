"""Provider/target resolution and config selection for the visualisation service."""

import sqlite3

import pytest

from app.visualisation import app as vis_app
from app.visualisation import cards
from nrod_railhub import db_backend

PG_URL = "postgresql://railuser@db.example:5432/rail"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("NROD_RAILHUB_DB", raising=False)
    monkeypatch.delenv("NROD_RAILHUB_CONFIG", raising=False)
    monkeypatch.delenv("NROD_RAILHUB_DB_URL", raising=False)
    monkeypatch.setattr(db_backend, "ensure_postgres_driver", lambda: object())
    cards.set_config_path(None)
    yield
    cards.set_config_path(None)


def test_set_config_path_beats_env(tmp_path, monkeypatch):
    env_cfg = tmp_path / "env.yaml"
    cli_cfg = tmp_path / "cli.yaml"
    monkeypatch.setenv("NROD_RAILHUB_CONFIG", str(env_cfg))
    assert cards.config_path() == env_cfg
    cards.set_config_path(str(cli_cfg))
    assert cards.config_path() == cli_cfg


def test_selected_config_used_for_db_settings_and_host(tmp_path):
    cfg = tmp_path / "x.yaml"
    cfg.write_text(f"db_provider: postgres\ndb_url: {PG_URL}\nvisualisation:\n  port: 9100\n")
    cards.set_config_path(cfg)
    assert vis_app.resolve_target() == PG_URL
    assert cards.visualisation_settings()["port"] == 9100


def test_yaml_sqlite_path(tmp_path):
    cfg = tmp_path / "x.yaml"
    cfg.write_text(f"db_path: {tmp_path}/a.db\n")
    cards.set_config_path(cfg)
    assert vis_app.resolve_target() == f"{tmp_path}/a.db"


def test_default_fallback_is_project_db(tmp_path):
    cards.set_config_path(tmp_path / "missing.yaml")
    assert vis_app.resolve_target() == str(vis_app.DB_PATH)


def test_env_override_accepts_postgres_url_and_sqlite_path(tmp_path, monkeypatch):
    cfg = tmp_path / "x.yaml"
    cfg.write_text("db_path: /ignored.db\n")
    cards.set_config_path(cfg)
    monkeypatch.setenv("NROD_RAILHUB_DB", PG_URL)
    assert vis_app.resolve_target() == PG_URL
    monkeypatch.setenv("NROD_RAILHUB_DB", str(tmp_path / "o.db"))
    assert vis_app.resolve_target() == str(tmp_path / "o.db")


def test_postgres_without_url_errors_without_leaking_secrets(tmp_path):
    cfg = tmp_path / "x.yaml"
    cfg.write_text("db_provider: postgres\n")
    cards.set_config_path(cfg)
    with pytest.raises(RuntimeError, match="requires a connection URL"):
        vis_app.resolve_target()


def test_get_conn_uses_named_rows_and_closes(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    seen = {}
    real = db_backend.connect

    def spy(target, **kw):
        seen["kw"] = kw
        conn = real(target, **kw)
        seen["conn"] = conn
        return conn

    monkeypatch.setattr(vis_app, "db_connect", spy)
    monkeypatch.setenv("NROD_RAILHUB_DB", str(db))
    with vis_app.get_conn() as conn:
        conn.execute("CREATE TABLE layout (id TEXT)")
        assert conn.table_exists("layout")
        assert vis_app._table_exists(conn, "layout")
        assert not vis_app._table_exists(conn, "nope")
    assert seen["kw"] == {"named_rows": True}
    with pytest.raises(sqlite3.ProgrammingError):
        seen["conn"].execute("SELECT 1")


def test_get_conn_dispatches_postgres_target(monkeypatch):
    calls = []

    class FakePg:
        is_postgres = True

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(vis_app, "db_connect", lambda t, **kw: calls.append((t, kw)) or FakePg())
    monkeypatch.setenv("NROD_RAILHUB_DB", PG_URL)
    with vis_app.get_conn():
        pass
    assert calls == [(PG_URL, {"named_rows": True}), "closed"]


def test_exists_on_postgres_uses_table_exists_only():
    class FakePg:
        is_postgres = True

        def table_exists(self, name):
            return name == "td_state"

        def execute(self, *a, **k):
            raise AssertionError("sqlite_master must not be queried")

    from app.visualisation import sequences
    assert sequences._exists(FakePg(), "td_state")
    assert not sequences._exists(FakePg(), "other")


def test_main_applies_config_before_settings(tmp_path, monkeypatch):
    cfg = tmp_path / "x.yaml"
    cfg.write_text("visualisation:\n  host: 0.0.0.0\n  port: 9200\n")
    ran = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: ran.update(host=host, port=port))
    vis_app.main(["--config", str(cfg)])
    assert ran == {"host": "0.0.0.0", "port": 9200}
    with pytest.raises(SystemExit):
        vis_app.main(["--config", str(tmp_path / "nope.yaml")])


def test_db_error_returns_503_on_any_driver_error(monkeypatch):
    from fastapi.testclient import TestClient

    def boom(*a, **k):
        raise sqlite3.OperationalError("down")

    monkeypatch.setattr(vis_app, "_fetch_td_snapshot", boom)
    r = TestClient(vis_app.app, raise_server_exceptions=False).get("/state")
    assert r.status_code == 503
