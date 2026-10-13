#!/usr/bin/env python3
"""Database provider boundary for nrod_railhub.

Two providers are supported:

* ``sqlite``   - the default; a local file (``db_path``).
* ``postgres`` - a PostgreSQL server addressed by a ``postgresql://`` URL
  (``db_url``).  Requires the optional ``psycopg`` (v3) driver.

Application code asks for a connection with :func:`connect` and gets an object
exposing the small subset of the DB-API/``sqlite3`` surface the application
uses (``execute``, ``executemany``, ``executescript``, ``cursor``,
``with conn:`` transactions, ``row_factory``-style named rows).  Provider
differences are handled here, deliberately, instead of being scattered through
the application:

* Query text uses ``?`` placeholders everywhere; the PostgreSQL connection
  rewrites them to ``%s`` (quote/comment aware) and maps ``LIKE`` to ``ILIKE``
  so searches stay case-insensitive like SQLite.
* Schema text may use the portable tokens ``__PK__`` (generated integer key),
  ``__NOW_ISO__`` and ``__NOW_MS__`` (current UTC time as ISO text / epoch
  milliseconds).  Each provider renders them with its own native syntax.
  PostgreSQL DDL additionally maps ``INTEGER`` to ``BIGINT`` (epoch
  milliseconds overflow 32 bit integers) and ``REAL`` to ``DOUBLE PRECISION``.
* ``executescript`` is emulated on PostgreSQL by executing each statement.
* Transactions: SQLite uses the native ``with conn:`` commit/rollback.  The
  PostgreSQL connection runs in autocommit mode and ``with conn:`` opens an
  explicit transaction, so a failed read can never leave the shared
  connection in an aborted transaction.
* Generated ids: use ``execute_returning_id`` (``lastrowid`` on SQLite,
  ``RETURNING id`` on PostgreSQL).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

PROVIDER_SQLITE = "sqlite"
PROVIDER_POSTGRES = "postgres"
SUPPORTED_PROVIDERS = (PROVIDER_SQLITE, PROVIDER_POSTGRES)
DB_URL_ENV_VAR = "NROD_RAILHUB_DB_URL"
POSTGRES_URL_SCHEMES = ("postgresql://", "postgres://")
POSTGRES_INSTALL_HINT = "pip install -r requirements-postgres.txt  (or: pip install 'psycopg[binary]>=3.1')"

_PROVIDER_ALIASES = {
    "sqlite": PROVIDER_SQLITE,
    "sqlite3": PROVIDER_SQLITE,
    "postgres": PROVIDER_POSTGRES,
    "postgresql": PROVIDER_POSTGRES,
}
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class DatabaseConfigError(ValueError):
    """Invalid or incomplete database provider configuration."""


class DatabaseDriverError(RuntimeError):
    """The driver required by the selected provider is not installed."""


# ---------------------------------------------------------------------------
# Configuration / target resolution
# ---------------------------------------------------------------------------

def _database_errors() -> Tuple[type, ...]:
    errors: Tuple[type, ...] = (sqlite3.Error,)
    try:
        import psycopg  # type: ignore
        errors += (psycopg.Error,)
    except ImportError:
        pass
    return errors


#: Exceptions raised by any supported database driver (for ``except DB_ERRORS``).
DB_ERRORS: Tuple[type, ...] = _database_errors()


def is_postgres_target(target: Optional[str]) -> bool:
    """True if ``target`` (as returned by :func:`resolve_database_target`) is a PostgreSQL URL."""
    return bool(target) and str(target).lower().startswith(POSTGRES_URL_SCHEMES)


def provider_for_target(target: Optional[str]) -> str:
    return PROVIDER_POSTGRES if is_postgres_target(target) else PROVIDER_SQLITE


def redact_target(target: Optional[str]) -> str:
    """Return ``target`` with any password removed so it is safe to log."""
    if not target or not is_postgres_target(target):
        return str(target) if target else ""
    try:
        parsed = urlparse(target)
        if parsed.password is None:
            return target
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{parsed.username or ''}:***@{host}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()
    except ValueError:
        return "postgresql://***"


def normalize_provider(provider: Optional[str]) -> str:
    """Validate a provider name; ``None``/empty means the SQLite default."""
    if provider is None or not str(provider).strip():
        return PROVIDER_SQLITE
    key = str(provider).strip().lower()
    if key not in _PROVIDER_ALIASES:
        raise DatabaseConfigError(
            f"Unsupported database provider {provider!r}; supported providers: "
            f"{', '.join(SUPPORTED_PROVIDERS)}"
        )
    return _PROVIDER_ALIASES[key]


def _expand_env_refs(value: str, environ: Mapping[str, str]) -> str:
    """Expand ``${VAR}`` references so secrets can stay out of the YAML file."""

    def repl(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in environ:
            raise DatabaseConfigError(
                f"db_url references environment variable ${{{name}}} which is not set"
            )
        return environ[name]

    return _ENV_REF_RE.sub(repl, value)


def ensure_postgres_driver() -> Any:
    """Import and return ``psycopg`` or raise an actionable :class:`DatabaseDriverError`."""
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise DatabaseDriverError(
            "The 'postgres' database provider requires the optional psycopg (v3) driver, "
            f"which is not installed. Install it with: {POSTGRES_INSTALL_HINT}"
        ) from exc
    return psycopg


def resolve_database_target(
    provider: Optional[str],
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    check_driver: bool = True,
) -> Optional[str]:
    """Validate the provider settings and return the connection target.

    * ``sqlite``: returns ``db_path`` (``~`` expanded) or ``None`` if unset.
    * ``postgres``: returns the ``postgresql://`` URL, taken from ``db_url`` or
      the ``NROD_RAILHUB_DB_URL`` environment variable; ``${VAR}`` references in
      the URL are expanded from the environment.

    Raises :class:`DatabaseConfigError` / :class:`DatabaseDriverError` with an
    actionable message when the settings are unusable.
    """
    environ = os.environ if environ is None else environ
    resolved = normalize_provider(provider)

    if resolved == PROVIDER_SQLITE:
        if not db_path:
            return None
        path = str(db_path)
        if is_postgres_target(path):
            raise DatabaseConfigError(
                "db_path looks like a PostgreSQL URL; set db_provider: postgres and "
                "use db_url for PostgreSQL connections (db_path is for SQLite files)"
            )
        return os.path.expanduser(path)

    url = (db_url or "").strip() or (environ.get(DB_URL_ENV_VAR) or "").strip()
    if not url:
        raise DatabaseConfigError(
            "db_provider 'postgres' requires a connection URL: set db_url in the config "
            f"file, pass --db-url, or set the {DB_URL_ENV_VAR} environment variable "
            "(e.g. postgresql://USER@HOST:5432/DBNAME)"
        )
    url = _expand_env_refs(url, environ)
    if not is_postgres_target(url):
        raise DatabaseConfigError(
            "db_url must be a PostgreSQL URL starting with postgresql:// or postgres:// "
            f"(got {url.split('://', 1)[0] + '://' if '://' in url else 'a value without a URL scheme'})"
        )
    try:
        parsed = urlparse(url)
        parsed.port  # noqa: B018 - raises ValueError for malformed ports
    except ValueError as exc:
        raise DatabaseConfigError(f"db_url is not a valid PostgreSQL URL: {exc}") from exc
    if not parsed.hostname and "host=" not in (parsed.query or ""):
        raise DatabaseConfigError(
            "db_url must include a host (postgresql://USER@HOST:5432/DBNAME) "
            "or a ?host=/socket/dir query parameter"
        )
    if check_driver:
        ensure_postgres_driver()
    return url


# ---------------------------------------------------------------------------
# SQL rendering helpers
# ---------------------------------------------------------------------------

_SQLITE_TOKENS = {
    "__PK__": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "__NOW_ISO__": "strftime('%Y-%m-%dT%H:%M:%fZ','now')",
    "__NOW_MS__": "(strftime('%s','now') * 1000)",
}
_POSTGRES_TOKENS = {
    "__PK__": "BIGSERIAL PRIMARY KEY",
    "__NOW_ISO__": "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')",
    "__NOW_MS__": "((extract(epoch from now()) * 1000)::bigint)",
}
_TOKEN_RE = re.compile(r"__(?:PK|NOW_ISO|NOW_MS)__")
_DDL_START_RE = re.compile(r"^\s*(?:CREATE|ALTER|DROP)\b", re.IGNORECASE)
_PG_TYPE_MAP = ((re.compile(r"\bINTEGER\b", re.IGNORECASE), "BIGINT"),
                (re.compile(r"\bREAL\b", re.IGNORECASE), "DOUBLE PRECISION"))
_GROUP_CONCAT_RE = re.compile(r"\bGROUP_CONCAT\s*\(\s*DISTINCT\s+([A-Za-z_][\w.]*)\s*\)", re.IGNORECASE)
_LIKE_RE = re.compile(r"\bLIKE\b", re.IGNORECASE)


def _render_tokens(sql: str, tokens: Mapping[str, str]) -> str:
    if "__" not in sql:
        return sql
    return _TOKEN_RE.sub(lambda m: tokens[m.group(0)], sql)


def _map_outside_literals(sql: str, fn, literal_fn=None) -> str:
    """Apply ``fn`` to the parts of ``sql`` outside quoted strings/identifiers and comments.

    ``literal_fn`` (optional) is applied to the quoted/comment parts.
    """
    literal_fn = literal_fn or (lambda text: text)
    out: List[str] = []
    buf: List[str] = []
    i, n = 0, len(sql)

    def flush() -> None:
        if buf:
            out.append(fn("".join(buf)))
            buf.clear()

    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            flush()
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            out.append(literal_fn(sql[i:j + 1]))
            i = j + 1
        elif ch == "-" and sql.startswith("--", i):
            flush()
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(literal_fn(sql[i:j]))
            i = j
        else:
            buf.append(ch)
            i += 1
    flush()
    return "".join(out)


def _to_postgres_dml(sql: str) -> str:
    """Rewrite ``?`` placeholders to ``%s`` (escaping literal ``%``) and map SQLite-isms."""

    def fix(segment: str) -> str:
        segment = segment.replace("%", "%%").replace("?", "%s")
        segment = _LIKE_RE.sub("ILIKE", segment)
        return _GROUP_CONCAT_RE.sub(r"string_agg(DISTINCT \1::text, ',')", segment)

    return _map_outside_literals(sql, fix, lambda text: text.replace("%", "%%"))


def _to_postgres_ddl(sql: str) -> str:
    def fix(segment: str) -> str:
        for pattern, replacement in _PG_TYPE_MAP:
            segment = pattern.sub(replacement, segment)
        return segment

    return _map_outside_literals(sql, fix)


def split_statements(script: str) -> List[str]:
    """Split a multi-statement SQL script on ``;`` (quote and comment aware)."""
    statements: List[str] = []
    current: List[str] = []
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if script[j] == ch:
                    if j + 1 < n and script[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            current.append(script[i:j + 1])
            i = j + 1
        elif script.startswith("--", i):
            j = script.find("\n", i)
            i = n if j == -1 else j
        elif ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
        else:
            current.append(ch)
            i += 1
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


# ---------------------------------------------------------------------------
# SQLite provider
# ---------------------------------------------------------------------------

def _render_sqlite(sql: str) -> str:
    return _render_tokens(sql, _SQLITE_TOKENS)


class _SQLiteCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):  # type: ignore[override]
        return super().execute(_render_sqlite(sql), parameters)

    def executemany(self, sql, seq_of_parameters):  # type: ignore[override]
        return super().executemany(_render_sqlite(sql), seq_of_parameters)

    def executescript(self, sql_script):  # type: ignore[override]
        return super().executescript(_render_sqlite(sql_script))

    def execute_returning_id(self, sql: str, parameters: Sequence[Any] = (), id_column: str = "id") -> int:
        self.execute(sql, parameters)
        return int(self.lastrowid)


class SQLiteConnection(sqlite3.Connection):
    """``sqlite3.Connection`` that renders the portable schema tokens."""

    provider = PROVIDER_SQLITE
    is_postgres = False

    def cursor(self, factory=_SQLiteCursor):  # type: ignore[override]
        return super().cursor(factory)

    def execute(self, sql, parameters=()):  # type: ignore[override]
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):  # type: ignore[override]
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):  # type: ignore[override]
        return self.cursor().executescript(sql_script)

    def execute_returning_id(self, sql: str, parameters: Sequence[Any] = (), id_column: str = "id") -> int:
        return self.cursor().execute_returning_id(sql, parameters, id_column)

    def begin(self) -> None:
        """No-op: the sqlite3 module opens a transaction implicitly before DML; ``commit()`` ends it."""

    def table_exists(self, name: str) -> bool:
        return self.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def table_columns(self, name: str) -> List[str]:
        return [r[1] for r in self.execute(f"PRAGMA table_info({name})").fetchall()]


# ---------------------------------------------------------------------------
# PostgreSQL provider
# ---------------------------------------------------------------------------

class Row(tuple):
    """Result row supporting index access and case-insensitive name access (like ``sqlite3.Row``)."""

    def __new__(cls, values: Iterable[Any], names: Sequence[str], index: Mapping[str, int]):
        row = super().__new__(cls, values)
        row._names = names
        row._index = index
        return row

    def keys(self) -> List[str]:
        return list(self._names)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._index[key.lower()])
            except KeyError:
                raise IndexError(f"No item with that key: {key!r}") from None
        return tuple.__getitem__(self, key)


def _named_row_factory(cursor: Any):
    desc = cursor.description or []
    names = [c.name for c in desc]
    index: dict = {}
    for i, name in enumerate(names):
        index.setdefault(name.lower(), i)
    return lambda values: Row(values, names, index)


class _Rollback(Exception):
    """Internal marker used to roll back an explicit transaction."""


class PostgresCursor:
    """Cursor wrapper that renders SQL for PostgreSQL and exposes the sqlite3-style API."""

    def __init__(self, connection: "PostgresConnection") -> None:
        self.connection = connection
        self._cur = connection._raw_cursor()
        self.arraysize = 1

    def _prepare(self, sql: str) -> Tuple[str, Optional[tuple]]:
        """Return the PostgreSQL text and the params placeholder (``None`` for DDL: no % processing)."""
        sql = _render_tokens(sql, _POSTGRES_TOKENS)
        if _DDL_START_RE.match(sql):
            return _to_postgres_ddl(sql), None
        return _to_postgres_dml(sql), ()

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> "PostgresCursor":
        text, no_params = self._prepare(sql)
        self._cur.execute(text, None if no_params is None else tuple(parameters or ()))
        return self

    def executemany(self, sql: str, seq_of_parameters: Iterable[Sequence[Any]]) -> "PostgresCursor":
        rows = [tuple(p) for p in seq_of_parameters]
        if rows:
            self._cur.executemany(self._prepare(sql)[0], rows)
        return self

    def executescript(self, script: str) -> "PostgresCursor":
        for statement in split_statements(script):
            self._cur.execute(self._prepare(statement)[0], None)
        return self

    def execute_returning_id(self, sql: str, parameters: Sequence[Any] = (), id_column: str = "id") -> int:
        self.execute(f"{sql.rstrip().rstrip(';')} RETURNING {id_column}", parameters)
        row = self._cur.fetchone()
        return int(row[0])

    @property
    def lastrowid(self) -> int:
        raise NotImplementedError(
            "lastrowid is not available on PostgreSQL; use execute_returning_id() instead"
        )

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size: Optional[int] = None):
        return self._cur.fetchmany(size or self.arraysize)

    def close(self) -> None:
        self._cur.close()

    def __iter__(self):
        return iter(self._cur)

    def __enter__(self) -> "PostgresCursor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class PostgresConnection:
    """psycopg (v3) connection exposing the subset of the sqlite3 API the application uses.

    The connection is in autocommit mode; ``with conn:`` starts an explicit
    transaction that is committed on success and rolled back on error.
    """

    provider = PROVIDER_POSTGRES
    is_postgres = True

    def __init__(self, url: str, named_rows: bool = False, connect_timeout: float = 30.0) -> None:
        self._psycopg = ensure_postgres_driver()
        self._url = url
        self._named_rows = named_rows
        self._connect_timeout = int(connect_timeout)
        self._tx_stack: List[Any] = []
        self._explicit_tx: Any = None
        self._reconnect_lock = threading.Lock()
        # Serialises transactions: a thread holds this from begin()/``with`` until the
        # transaction ends, so threads sharing the connection never interleave them.
        self._tx_lock = threading.RLock()
        self._conn = self._open()

    def _open(self):
        kwargs: dict = dict(
            autocommit=True,
            connect_timeout=self._connect_timeout,
            keepalives=1,
            keepalives_idle=60,
        )
        if self._named_rows:
            kwargs["row_factory"] = _named_row_factory
        return self._psycopg.connect(self._url, **kwargs)

    def _raw_cursor(self):
        return self._conn.cursor()

    # sqlite3-compatible surface -------------------------------------------------
    def cursor(self) -> PostgresCursor:
        self._ensure_open()
        return PostgresCursor(self)

    def _ensure_open(self) -> None:
        """Re-open a connection the server dropped (only outside a transaction)."""
        if (self._conn.closed or self._conn.broken) and not self._tx_stack and self._explicit_tx is None:
            with self._reconnect_lock:
                if self._conn.closed or self._conn.broken:
                    self._conn = self._open()

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> PostgresCursor:
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters) -> PostgresCursor:
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, script: str) -> PostgresCursor:
        return self.cursor().executescript(script)

    def execute_returning_id(self, sql: str, parameters: Sequence[Any] = (), id_column: str = "id") -> int:
        return self.cursor().execute_returning_id(sql, parameters, id_column)

    def begin(self) -> None:
        """Open an explicit transaction that lasts until ``commit()`` or ``rollback()``."""
        if self._explicit_tx is None:
            self._tx_lock.acquire()
            try:
                self._ensure_open()
                tx = self._conn.transaction()
                tx.__enter__()
            except BaseException:
                self._tx_lock.release()
                raise
            self._explicit_tx = tx

    def commit(self) -> None:
        """Commit a transaction opened with ``begin()``; no-op in autocommit mode."""
        self._finish_explicit(None)

    def rollback(self) -> None:
        """Roll back a transaction opened with ``begin()``; no-op in autocommit mode."""
        self._finish_explicit(_Rollback)

    def _finish_explicit(self, exc_type) -> None:
        tx, self._explicit_tx = self._explicit_tx, None
        if tx is not None:
            try:
                tx.__exit__(exc_type, exc_type() if exc_type else None, None)
            finally:
                self._tx_lock.release()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PostgresConnection":
        self._tx_lock.acquire()
        try:
            self._ensure_open()
            tx = self._conn.transaction()
            tx.__enter__()
        except BaseException:
            self._tx_lock.release()
            raise
        self._tx_stack.append(tx)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        tx = self._tx_stack.pop()
        try:
            tx.__exit__(exc_type, exc, tb)
        finally:
            self._tx_lock.release()
        return False

    def table_exists(self, name: str) -> bool:
        return self.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ?", (name.lower(),)
        ).fetchone() is not None

    def table_columns(self, name: str) -> List[str]:
        return [r[0] for r in self.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position",
            (name.lower(),),
        ).fetchall()]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def connect(target: str, *, named_rows: bool = False, timeout: float = 30.0):
    """Open a connection for ``target`` (SQLite path or PostgreSQL URL).

    ``named_rows=True`` returns rows addressable by column name
    (``sqlite3.Row`` semantics) instead of plain tuples.
    """
    if is_postgres_target(target):
        return PostgresConnection(target, named_rows=named_rows, connect_timeout=timeout)
    conn = sqlite3.connect(
        target, check_same_thread=False, timeout=timeout, factory=SQLiteConnection
    )
    conn.row_factory = sqlite3.Row if named_rows else None
    return conn
