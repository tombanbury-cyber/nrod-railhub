"""YAML-backed storage for berth-sequence visualisation cards.

Cards live under the ``visualisation_cards`` key of the shared ``nrod.yaml``
configuration (or the file named by ``NROD_RAILHUB_CONFIG``). Saving only
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
from pydantic import BaseModel, Field, constr, field_validator

CONFIG_ENV_VAR = "NROD_RAILHUB_CONFIG"
CARDS_KEY = "visualisation_cards"
DEFAULT_CONFIG_NAME = "nrod.yaml"
PROJECT_ROOT = Path(__file__).parent.parent.parent

_CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_lock = threading.RLock()


class CardConfigError(RuntimeError):
    """The shared YAML file cannot be read or safely updated."""


def config_path() -> Path:
    """Return the shared YAML path (env override, else ``nrod.yaml`` in the project root)."""
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


def configured_sqlite_path() -> Optional[str]:
    """SQLite ``db_path`` from the shared YAML, or None when unset/no file.

    Raises CardConfigError if the YAML selects a non-SQLite provider, which the
    visualisation service does not support.
    """
    cfg = load_config()
    provider = str(cfg.get("db_provider") or "sqlite").strip().lower()
    if provider not in ("sqlite", "sqlite3"):
        raise CardConfigError(
            "The visualisation API only supports SQLite; db_provider in "
            f"{config_path()} is {provider!r}"
        )
    db_path = cfg.get("db_path")
    return os.path.expanduser(str(db_path)) if db_path else None


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
    with _lock:
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
    with _lock:
        cards = list_cards()
        remaining = [c for c in cards if c["id"] != card_id]
        if len(remaining) == len(cards):
            return False
        _write_cards(remaining)
    return True
