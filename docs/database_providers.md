# Database providers (SQLite and PostgreSQL)

`nrod_railhub` stores TD/TRUST/VSTP/CIF state, reference data and mapper output
in one database. **SQLite is the default** and needs no configuration beyond
`db_path`. PostgreSQL is available as an optional provider for longer-term,
multi-process or server deployments.

## Selecting a provider

| Setting (YAML) | CLI | Meaning |
|---|---|---|
| `db_provider` | `--db-provider` | `sqlite` (default) or `postgres` (`postgresql` accepted) |
| `db_path` | `--db-path` | SQLite file path (provider `sqlite` only) |
| `db_url` | `--db-url` | PostgreSQL URL (provider `postgres` only) |

As with every other option, command-line arguments override the YAML file.
If `db_provider` is omitted the provider is `sqlite`, so existing configs and
command lines keep working unchanged.

### SQLite (default)

```yaml
db_provider: sqlite        # optional - this is the default
db_path: /var/lib/nrod-railhub/rail.db
```

### PostgreSQL

```bash
pip install -r requirements-postgres.txt     # installs psycopg (v3)
```

```yaml
db_provider: postgres
# Keep the password out of the file: ${VAR} references are expanded from the environment
db_url: postgresql://railhub:${RAILHUB_DB_PASSWORD}@db.example:5432/railhub
web_port: 8088
```

Alternatively leave `db_url` out and export `NROD_RAILHUB_DB_URL`
(`postgresql://USER@HOST:5432/DBNAME``). Prefer the environment variable
or the YAML file over `--db-url`, whose value is visible in the process list.
Passwords are never written to the log (`redact_target`).

The database must already exist and the user needs `CREATE` rights on its
schema; tables and indexes are created automatically on first start. Use libpq
URL parameters (`?sslmode=require`, `?options=-csearch_path%3Dmyschema`) for
TLS or a non-default schema.

## What uses the selected provider

* `RailDB` primary persistence (TD, TRUST, VSTP, CIF, TOC, mapper, topology, retention)
* The web dashboard, including `/signal-mappings`, the corridor explorer and
  the TD Decode Lab
* Reference-data bootstrap (`ref_bootstrap`) and `import_scripts/nrod_ref_import.py`
  (`--db postgresql://...`), and `LocationResolver`/`SmartResolver` persistence

Every provider-specific difference (parameter markers, generated IDs, upserts,
timestamp defaults, transactions, schema introspection) is handled in
`nrod_railhub/db_backend.py`; SQL in the application uses portable syntax plus
the `__PK__`, `__NOW_ISO__` and `__NOW_MS__` schema tokens, which each provider
renders natively. SQLite-only features (`PRAGMA`, `executescript`, `lastrowid`,
`INSERT OR ...`) are not used on the PostgreSQL path.

## Limitations

* **Switching providers does not move data.** There is no SQLite-to-PostgreSQL
  migration; a new PostgreSQL database starts empty (reference data is
  re-imported automatically at startup when credentials are supplied).
* Still SQLite-only (they fail with a clear message instead of misbehaving):
  `td_listener.py` (rejects `db_provider: postgres` in the config file), and the helper scripts under `scripts/`, `sql/` and
  `import_scripts/convert_nwr_shp_to_sqlite.py`.
* The `/config` web page edits the YAML file but does not expose `db_url`; edit
  the file (or environment) directly to change database settings.
* PostgreSQL `LIKE` filters are mapped to case-insensitive `ILIKE` to match
  SQLite's behaviour.

## Testing

`pytest -q` needs no PostgreSQL server: the PostgreSQL path is covered with a
mocked driver in `tests/test_db_backend.py`. To run the live-server tests in
`tests/test_postgres_integration.py` (each test uses and drops its own schema):

```bash
NROD_RAILHUB_TEST_POSTGRES_URL=postgresql://postgres@localhost/railhub_test \
  pytest -q tests/test_postgres_integration.py
```
