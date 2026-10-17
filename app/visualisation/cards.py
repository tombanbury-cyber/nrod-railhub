"""YAML-backed storage for berth-sequence visualisation cards.

Cards live under the ``visualisation_cards`` key of the shared ``nrod.yaml``
configuration (or the file named by ``--config`` / ``NROD_RAILHUB_CONFIG``). Saving only
replaces that key; every other setting is written back unchanged.  Note that
PyYAML does not retain comments, so comments in the file are lost on save.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

try:  # POSIX only; falls back to the in-process lock elsewhere
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]
from pydantic import BaseModel, Field, constr, field_validator

CONFIG_ENV_VAR = "NROD_RAILHUB_CONFIG"
CARDS_KEY = "visualisation_cards"
SETTINGS_KEY = "visualisation"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_CONFIG_NAME = "nrod.yaml"
PROJECT_ROOT = Path(__file__).parent.parent.parent

_CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_lock = threading.RLock()


class CardConfigError(RuntimeError):
    """The shared YAML file cannot be read or safely updated."""


_config_override: Optional[Path] = None


def set_config_path(path: Optional[str | os.PathLike]) -> None:
    """Select the shared YAML file for this process (e.g. from ``--config``); ``None`` clears it."""
    global _config_override
    text = os.fspath(path).strip() if path is not None else ""
    _config_override = Path(text).expanduser() if text else None


def config_path() -> Path:
    """Return the shared YAML path.

    Precedence: ``set_config_path()`` (``--config``), ``NROD_RAILHUB_CONFIG``, then
    ``nrod.yaml`` in the project root.
    """
    if _config_override is not None:
        return _config_override
    override = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return PROJECT_ROOT / DEFAULT_CONFIG_NAME


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load the whole YAML mapping; a missing file yields ``{}``."""
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise CardConfigError(f"{path} is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise CardConfigError(f"{path} must contain a YAML mapping")
    return data


def configured_database_target() -> Optional[str]:
    """Database target (SQLite path or PostgreSQL URL) from the shared YAML.

    Reads ``db_provider``, ``db_path`` and ``db_url`` and validates them with
    ``resolve_database_target`` like the main RailHub CLI. Returns None for SQLite
    when no ``db_path`` is set. Raises CardConfigError for invalid settings; the
    message never contains the connection URL.
    """
    from nrod_railhub.db_backend import (
        DatabaseConfigError,
        DatabaseDriverError,
        normalize_provider,
        resolve_database_target,
    )

    cfg = load_config()
    try:
        provider = normalize_provider(cfg.get("db_provider"))
        db_url = cfg.get("db_url")
        if db_url and provider != "postgres":
            raise DatabaseConfigError("db_url is only used with db_provider: postgres")
        return resolve_database_target(provider, cfg.get("db_path"), db_url)
    except (DatabaseConfigError, DatabaseDriverError) as exc:
        raise CardConfigError(f"{config_path()}: {exc}") from exc


def configured_sqlite_path() -> Optional[str]:
    """Backward-compatible alias of :func:`configured_database_target`."""
    return configured_database_target()


def visualisation_settings() -> dict[str, Any]:
    """Validated ``visualisation:`` section (``host``, ``port``) of the shared YAML."""
    defaults: dict[str, Any] = {"host": DEFAULT_HOST, "port": DEFAULT_PORT}
    section = load_config().get(SETTINGS_KEY)
    if section is None:
        return defaults
    if not isinstance(section, dict):
        raise CardConfigError(f"{SETTINGS_KEY!r} in {config_path()} must be a mapping")
    unknown = sorted(set(section) - set(defaults))
    if unknown:
        raise CardConfigError(
            f"unknown {SETTINGS_KEY} setting(s) in {config_path()}: {', '.join(map(str, unknown))} "
            "(supported: host, port)"
        )
    settings = {**defaults, **{k: v for k, v in section.items() if v is not None}}
    host = settings["host"]
    if not isinstance(host, str) or not host.strip():
        raise CardConfigError(f"{SETTINGS_KEY}.host in {config_path()} must be a non-empty string")
    port = settings["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise CardConfigError(f"{SETTINGS_KEY}.port in {config_path()} must be an integer 1-65535")
    settings["host"] = host.strip()
    return settings


class SequenceItem(BaseModel):
    berth: constr(strip_whitespace=True, min_length=1)
    td_area: constr(strip_whitespace=True, min_length=1)
    provenance: Literal["observed", "inferred"] = "observed"
    source: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class DisplayOptions(BaseModel):
    stations: bool = True
    berth_labels: bool = True
    occupancy: bool = True
    signals: bool = False


class SequenceSource(BaseModel):
    method: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    evidence: dict[str, Any] = Field(default_factory=dict)


class CardPayload(BaseModel):
    id: str
    name: constr(strip_whitespace=True, min_length=1)
    from_station: Optional[str] = None
    to_station: Optional[str] = None
    sequence: list[SequenceItem] = Field(default_factory=list)
    sequence_source: SequenceSource = Field(default_factory=SequenceSource)
    display: DisplayOptions = Field(default_factory=DisplayOptions)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        value = value.strip()
        if not _CARD_ID_RE.match(value):
            raise ValueError("id must be 1-64 letters, digits, '_' or '-'")
        return value


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)


class _FileLock:
    """Advisory cross-process lock on a sidecar ``<config>.lock`` file."""

    def __init__(self, path: Path) -> None:
        self._path = path.with_name(path.name + ".lock")
        self._fh = None

    def __enter__(self):
        if fcntl is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "a")
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def _write_cards(cards: list[dict[str, Any]]) -> None:
    path = config_path()
    cfg = load_config(path)  # raises (and leaves file untouched) if unreadable
    cfg[CARDS_KEY] = cards
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, sort_keys=False, default_flow_style=False)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)  # keep restrictive perms (file holds credentials)
        else:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def list_cards() -> list[dict[str, Any]]:
    cards = load_config().get(CARDS_KEY) or []
    if not isinstance(cards, list):
        raise CardConfigError(f"{CARDS_KEY} must be a list")
    return [c for c in cards if isinstance(c, dict) and c.get("id")]


def get_card(card_id: str) -> Optional[dict[str, Any]]:
    return next((c for c in list_cards() if c["id"] == card_id), None)


def save_card(payload: CardPayload, *, create: bool) -> dict[str, Any]:
    """Create (rejecting duplicates) or replace a card; returns the stored dict."""
    card = _dump(payload)
    with _lock, _FileLock(config_path()):
        cards = list_cards()
        index = next((i for i, c in enumerate(cards) if c["id"] == card["id"]), None)
        if create and index is not None:
            raise KeyError("exists")
        if not create and index is None:
            raise KeyError("missing")
        if index is None:
            cards.append(card)
        else:
            cards[index] = card
        _write_cards(cards)
    return card


def delete_card(card_id: str) -> bool:
    with _lock, _FileLock(config_path()):
        cards = list_cards()
        remaining = [c for c in cards if c["id"] != card_id]
        if len(remaining) == len(cards):
            return False
        _write_cards(remaining)
    return True
