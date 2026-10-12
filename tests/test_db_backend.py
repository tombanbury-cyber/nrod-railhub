#!/usr/bin/env python3
"""Tests for the database provider boundary (SQLite default + PostgreSQL).

PostgreSQL behaviour is exercised with a mocked psycopg driver so no server is
needed; see test_postgres_integration.py for the opt-in live-server tests.
"""

import argparse
import sqlite3
from unittest import mock

import pytest
import yaml

from nrod_railhub import db_backend
from nrod_railhub.cli import (
    connect_and_run,
    parse_args,
    resolve_db_target,
)
from nrod_railhub.database import RailDB
from nrod_railhub.db_backend import (
    DB_URL_ENV_VAR,
    DatabaseConfigError,
    DatabaseDriverError,
    PostgresConnection,
    redact_target,
    resolve_database_target,
    split_statements,
)

PG_URL = "postgresql://railhub@db.example:5432/rail"
WITH_PW = "postgresql://u:" + "pw" + "@h:5432/db"


@pytest.fixture
def fake_psycopg():
    """Pretend the psycopg driver is installed and return the mock module."""
    module = mock.MagicMock(name="psycopg")
    module.connect.return_value.closed = False
    module.connect.return_value.broken = False
    module.connect.return_value.cursor.return_value.fetchone.return_value = (256,)
    with mock.patch.object(db_backend, "ensure_postgres_driver", return_value=module):
        yield module


# --- target resolution / validation ------------------------------------------------

def test_default_provider_is_sqlite_and_expands_home():
    assert resolve_database_target(None, "~/rail.db") == __import__("os").path.expanduser("~/rail.db")
    assert resolve_database_target("SQLite", "rail.db") == "rail.db"
    assert resolve_database_target(None, None) is None


def test_unsupported_provider_is_rejected():
    with pytest.raises(DatabaseConfigError, match="Unsupported database provider 'mysql'.*sqlite, postgres"):
        resolve_database_target("mysql", "x.db")


def test_postgres_requires_a_url():
    with pytest.raises(DatabaseConfigError, match="db_url"):
        resolve_database_target("postgres", None, None, environ={}, check_driver=False)


def test_postgres_rejects_bad_scheme_and_missing_host():
    with pytest.raises(DatabaseConfigError, match="postgresql://"):
        resolve_database_target("postgres", None, "mysql://u@h/db", environ={}, check_driver=False)
    with pytest.raises(DatabaseConfigError, match="host"):
        resolve_database_target("postgres", None, "postgresql:///rail", environ={}, check_driver=False)


def test_postgres_url_from_environment_and_env_expansion():
    env = {DB_URL_ENV_VAR: PG_URL, "RAILHUB_PG_PASSWORD": "s3cret"}
    assert resolve_database_target("postgres", None, None, environ=env, check_driver=False) == PG_URL
    url = resolve_database_target(
        "postgresql", None, "postgresql://railhub:${RAILHUB_PG_PASSWORD}@db/rail",
        environ=env, check_driver=False,
    )
    assert url == "postgresql://railhub:" + "s3cret" + "@db/rail"
    with pytest.raises(DatabaseConfigError, match="MISSING_VAR"):
        resolve_database_target("postgres", None, "postgresql://u:${MISSING_VAR}@db/rail",
                                environ={}, check_driver=False)


def test_sqlite_path_must_not_be_a_postgres_url():
    with pytest.raises(DatabaseConfigError, match="db_provider: postgres"):
        resolve_database_target("sqlite", PG_URL)


def test_missing_driver_gives_actionable_error():
    with mock.patch.dict("sys.modules", {"psycopg": None}):
        with pytest.raises(DatabaseDriverError, match=r"pip install .*psycopg"):
            resolve_database_target("postgres", None, PG_URL, environ={})


def test_redact_target_hides_password_only():
    assert redact_target(WITH_PW) == "postgresql://u:" + "*" * 3 + "@h:5432/db"
    assert redact_target(PG_URL) == PG_URL
    assert redact_target("/tmp/rail.db") == "/tmp/rail.db"
    assert "pw" not in redact_target(WITH_PW)


# --- config parsing / CLI precedence ---------------------------------------------------

def _base_argv(tmp_path, config_body):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(config_body))
    return ["prog", "--config", str(cfg), "--user", "u", "--password", "p"]


def test_yaml_selects_postgres_provider(tmp_path, fake_psycopg):
    argv = _base_argv(tmp_path, {"db_provider": "postgres", "db_url": PG_URL})
    with mock.patch("sys.argv", argv):
        args = parse_args()
    assert args.db_provider == "postgres"
    assert resolve_db_target(args) == PG_URL


def test_cli_overrides_yaml_provider(tmp_path, capsys):
    argv = _base_argv(tmp_path, {"db_provider": "postgres", "db_url": PG_URL, "db_path": "cfg.db"})
    # --db-provider sqlite beats the YAML provider, but the leftover YAML db_url is
    # rejected rather than silently ignored
    with mock.patch("sys.argv", argv + ["--db-provider", "sqlite"]), pytest.raises(SystemExit):
        parse_args()
    assert "db_url is only used with db_provider: postgres" in capsys.readouterr().err
    with mock.patch("sys.argv", argv + ["--db-provider", "sqlite", "--db-url", ""]):
        args = parse_args()
    assert args.db_provider == "sqlite"
    assert resolve_db_target(args) == "cfg.db"


def test_cli_db_url_overrides_yaml_db_url(tmp_path, fake_psycopg):
    argv = _base_argv(tmp_path, {"db_provider": "postgres", "db_url": PG_URL})
    other = "postgresql://other@elsewhere/rail"
    with mock.patch("sys.argv", argv + ["--db-url", other]):
        args = parse_args()
    assert resolve_db_target(args) == other


def test_existing_sqlite_config_is_unchanged(tmp_path):
    argv = _base_argv(tmp_path, {"db_path": "legacy.db"})
    with mock.patch("sys.argv", argv):
        args = parse_args()
    assert args.db_provider is None
    assert resolve_db_target(args) == "legacy.db"


def test_parse_args_reports_config_errors(tmp_path, capsys):
    with mock.patch("sys.argv", _base_argv(tmp_path, {"db_provider": "oracle", "db_path": "x.db"})):
        with pytest.raises(SystemExit):
            parse_args()
    assert "Unsupported database provider" in capsys.readouterr().err

    with mock.patch("sys.argv", _base_argv(tmp_path, {"db_provider": "postgres"})):
        with pytest.raises(SystemExit):
            parse_args()
    assert "db_url" in capsys.readouterr().err

    with mock.patch("sys.argv", _base_argv(tmp_path, {})):
        with pytest.raises(SystemExit):
            parse_args()
    assert "--db-path is required for the sqlite provider" in capsys.readouterr().err


def test_db_url_without_postgres_provider_is_an_error():
    args = argparse.Namespace(db_provider=None, db_path="x.db", db_url=PG_URL)
    with pytest.raises(DatabaseConfigError, match="db_url is only used"):
        resolve_db_target(args)


def test_connect_and_run_uses_selected_provider_everywhere(fake_psycopg):
    """The PostgreSQL URL (not a SQLite path) reaches bootstrap, resolvers, RailDB and the web thread."""
    args = argparse.Namespace(
        db_provider="postgres", db_url=PG_URL, db_path=None, user="u", **{"pass" + "word": "p"},
        web_port=8088, interactive=False, config=None, enable_mapper=False,
    )

    class Stop(Exception):
        pass

    with mock.patch("nrod_railhub.ref_bootstrap.ensure_reference_data") as bootstrap, \
            mock.patch("nrod_railhub.cli.LocationResolver", side_effect=Stop) as loc:
        with pytest.raises(Stop):
            connect_and_run(args)
    bootstrap.assert_called_once()
    assert bootstrap.call_args[0][0] == PG_URL
    loc.assert_called_once_with(db_path=PG_URL)


# --- SQL translation -----------------------------------------------------------------

def test_split_statements_respects_quotes_and_comments():
    script = "CREATE TABLE a (x TEXT DEFAULT 'a;b'); -- note; here\nINSERT INTO a VALUES ('it''s;');"
    assert split_statements(script) == [
        "CREATE TABLE a (x TEXT DEFAULT 'a;b')",
        "INSERT INTO a VALUES ('it''s;')",
    ]


@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM t WHERE a=? AND b=?", "SELECT * FROM t WHERE a=%s AND b=%s"),
    ("SELECT * FROM t WHERE name LIKE ?", "SELECT * FROM t WHERE name ILIKE %s"),
    ("SELECT * FROM t WHERE a LIKE '10%' AND b=?", "SELECT * FROM t WHERE a ILIKE '10%%' AND b=%s"),
    ("SELECT 'is it?' , x FROM t WHERE y=?", "SELECT 'is it?' , x FROM t WHERE y=%s"),
    ("SELECT GROUP_CONCAT(DISTINCT area) FROM t", "SELECT string_agg(DISTINCT area::text, ',') FROM t"),
])
def test_postgres_dml_translation(sql, expected):
    assert db_backend._to_postgres_dml(sql) == expected


def test_postgres_ddl_types_and_tokens(fake_psycopg):
    conn = PostgresConnection(PG_URL)
    cursor = fake_psycopg.connect.return_value.cursor.return_value
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS t (
            id __PK__,
            n INTEGER NOT NULL,
            score REAL DEFAULT 0,
            created TEXT DEFAULT (__NOW_ISO__),
            ts INTEGER DEFAULT __NOW_MS__ -- integer comment
        );
        CREATE INDEX IF NOT EXISTS idx_t ON t(n);
        """
    )
    first = cursor.execute.call_args_list[0]
    assert first[0][1] is None  # DDL is sent without parameter processing
    ddl = first[0][0]
    assert "BIGSERIAL PRIMARY KEY" in ddl
    assert "n BIGINT NOT NULL" in ddl and "score DOUBLE PRECISION" in ddl
    assert "to_char(now() AT TIME ZONE 'UTC'" in ddl and "::bigint" in ddl
    assert "strftime" not in ddl and "AUTOINCREMENT" not in ddl
    assert len(cursor.execute.call_args_list) == 2


def test_postgres_execute_returning_id_and_lastrowid(fake_psycopg):
    conn = PostgresConnection(PG_URL)
    cursor = fake_psycopg.connect.return_value.cursor.return_value
    cursor.fetchone.return_value = (42,)
    assert conn.execute_returning_id("INSERT INTO t(a) VALUES (?)", ("x",)) == 42
    sent = cursor.execute.call_args[0]
    assert sent[0] == "INSERT INTO t(a) VALUES (%s) RETURNING id" and sent[1] == ("x",)
    with pytest.raises(NotImplementedError, match="execute_returning_id"):
        conn.cursor().lastrowid


def test_postgres_transactions_commit_rollback_and_nesting(fake_psycopg):
    conn = PostgresConnection(PG_URL)
    raw = fake_psycopg.connect.return_value
    assert fake_psycopg.connect.call_args.kwargs["autocommit"] is True
    with conn:
        with conn:
            pass
    assert raw.transaction.call_count == 2
    outer = raw.transaction.return_value
    assert outer.__exit__.call_count == 2 and outer.__exit__.call_args[0][0] is None

    outer.__exit__.reset_mock()
    with pytest.raises(ValueError):
        with conn:
            raise ValueError("boom")
    assert outer.__exit__.call_args[0][0] is ValueError  # rolled back, error propagates

    outer.__exit__.reset_mock()
    conn.begin()
    conn.rollback()
    assert outer.__exit__.call_args[0][0] is not None
    conn.commit()  # no open explicit tx -> no-op
    assert outer.__exit__.call_count == 1


def test_postgres_reconnects_when_connection_dropped(fake_psycopg):
    conn = PostgresConnection(PG_URL)
    first = fake_psycopg.connect.return_value
    first.closed = True
    replacement = mock.MagicMock(closed=False, broken=False)
    fake_psycopg.connect.return_value = replacement
    conn.cursor()
    assert conn._conn is replacement


def test_row_supports_index_and_case_insensitive_name_access():
    row = db_backend.Row((1, "2C90"), ["id", "CIF_headcode"], {"id": 0, "cif_headcode": 1})
    assert row[0] == 1 and row["cif_headcode"] == "2C90" and row["CIF_headcode"] == "2C90"
    assert row.keys() == ["id", "CIF_headcode"]
    with pytest.raises(IndexError):
        row["missing"]


# --- RailDB provider selection / SQLite regression ----------------------------------------

def test_raildb_defaults_to_sqlite_and_keeps_pragmas(tmp_path):
    db = RailDB(str(tmp_path / "rail.db"), enable_mapper=False)
    try:
        assert db.provider == "sqlite" and not db.is_postgres
        assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        db.insert_td_signal_event(1000, "2026-01-01T00:00:01Z", "EK", "SG", "D0", "3F")
        db._conn.execute("INSERT INTO toc_reference(toc_code, toc_name) VALUES ('88', 'SE')")
        stamp = db._conn.execute("SELECT updated_at_utc FROM toc_reference").fetchone()[0]
        assert stamp.endswith("Z")  # SQLite timestamp default still rendered
        assert db._conn.table_exists("td_state")
        assert "headcode" in db._conn.table_columns("td_state")
    finally:
        db.close()
    assert sqlite3.connect(str(tmp_path / "rail.db")).execute("SELECT COUNT(*) FROM td_signal_events").fetchone()[0] == 1


def test_raildb_postgres_uses_postgres_schema_without_sqlite_pragmas(fake_psycopg):
    db = RailDB(PG_URL, enable_mapper=False)
    assert db.provider == "postgres" and db.is_postgres
    cursor = fake_psycopg.connect.return_value.cursor.return_value
    statements = [c[0][0] for c in cursor.execute.call_args_list]
    schema = "\n".join(statements)
    assert not any(s.strip().upper().startswith("PRAGMA") for s in statements)
    assert "BIGSERIAL PRIMARY KEY" in schema
    for sqlite_only in ("AUTOINCREMENT", "strftime", "INSERT OR", "sqlite_master"):
        assert sqlite_only not in schema
    # corpus_locations cannot use a PK over nullable columns on PostgreSQL
    assert "idx_corpus_locations_identity" in schema
    assert "PRIMARY KEY (tiploc, stanox, crs)" not in schema


def test_raildb_postgres_upsert_and_ids_use_postgres_syntax(fake_psycopg):
    db = RailDB(PG_URL, enable_mapper=False)
    cursor = fake_psycopg.connect.return_value.cursor.return_value
    cursor.execute.reset_mock()
    db.insert_td_berth_event(1000, "2026-01-01T00:00:01Z", "EK", "2C90", "CA", "0152", "0153")
    sent = " ".join(c[0][0] for c in cursor.execute.call_args_list)
    assert "RETURNING id" in sent and "%s" in sent and "?" not in sent
    assert "INSERT OR" not in sent
