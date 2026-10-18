"""FastAPI backend for schematic visualization PoC.

This module provides a minimal REST API and WebSocket server for real-time
train visualization on schematic layouts.
"""

import asyncio
import hmac
import html
import json
import os
import sqlite3
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, constr

from nrod_railhub.db_backend import (
    DB_ERRORS,
    DatabaseConfigError,
    DatabaseDriverError,
    connect as db_connect,
    is_postgres_target,
    resolve_database_target,
)

from . import cards as card_store
from . import sequences
from .route_inference import infer_train_chain


def _integrity_errors() -> tuple[type, ...]:
    errors: tuple[type, ...] = (sqlite3.IntegrityError,)
    try:
        import psycopg  # type: ignore
        errors += (psycopg.IntegrityError,)
    except ImportError:
        pass
    return errors


INTEGRITY_ERRORS = _integrity_errors()

# Database path (relative to project root)
DB_PATH = Path(__file__).parent.parent.parent / "railhub.db"

# Static files path
STATIC_PATH = Path(__file__).parent.parent.parent / "web" / "static"

DEFAULT_BERTH_WIDTH = 60
DEFAULT_BERTH_HEIGHT = 30
DEFAULT_BERTH_GAP = 10
DEFAULT_BERTH_START_X = 50
DEFAULT_BERTH_START_Y = 100


class EventCreate(BaseModel):
    """Model for creating new events via POST /event."""
    ts: str
    source: str
    train_id: Optional[str] = None
    event_type: str
    object_id: str
    payload: dict = {}


class LayoutPayload(BaseModel):
    """Model for layout CRUD operations."""
    id: constr(strip_whitespace=True, min_length=1)
    name: constr(strip_whitespace=True, min_length=1)
    description: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class BerthPayload(BaseModel):
    """Model for berth CRUD operations."""
    id: constr(strip_whitespace=True, min_length=1)
    layout_id: constr(strip_whitespace=True, min_length=1)
    name: constr(strip_whitespace=True, min_length=1)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(default=60, gt=0)
    height: int = Field(default=30, gt=0)
    berth_type: Literal["normal", "platform", "siding"] = "normal"


class SignalPayload(BaseModel):
    """Model for signal CRUD operations."""
    id: constr(strip_whitespace=True, min_length=1)
    layout_id: constr(strip_whitespace=True, min_length=1)
    name: constr(strip_whitespace=True, min_length=1)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    signal_type: Literal["auto", "controlled", "shunt"] = "auto"


class BerthImportPayload(BaseModel):
    """Model for importing selected berth chain items into a layout."""
    layout_id: constr(strip_whitespace=True, min_length=1)
    berth_ids: list[str] = Field(default_factory=list)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts events."""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        
        # Clean up dead connections
        for conn in dead_connections:
            self.disconnect(conn)


# Initialize FastAPI app
app = FastAPI(
    title="NROD RailHub Visualization API",
    description="Real-time train schematic visualization",
    version="0.1.0"
)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager
manager = ConnectionManager()


def resolve_target() -> str:
    """Resolve the database target (SQLite path or PostgreSQL URL).

    ``NROD_RAILHUB_DB`` wins; otherwise ``db_provider``/``db_path``/``db_url`` from the
    selected shared YAML are used, falling back to the project ``railhub.db``.
    """
    override = os.environ.get("NROD_RAILHUB_DB", "").strip()
    try:
        if override:
            if is_postgres_target(override):
                return resolve_database_target("postgres", db_url=override)
            return resolve_database_target("sqlite", override)
        return card_store.configured_database_target() or str(DB_PATH)
    except (DatabaseConfigError, DatabaseDriverError, card_store.CardConfigError) as exc:
        raise RuntimeError(str(exc)) from exc


@contextmanager
def get_conn():
    """Get a database connection (SQLite or PostgreSQL) with named-row access."""
    conn = db_connect(resolve_target(), named_rows=True)
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn: Any, table_name: str) -> bool:
    return sequences._exists(conn, table_name)


def _require_visualisation_schema(conn: Any) -> None:
    missing = [name for name in ("layout", "berth", "signal") if not _table_exists(conn, name)]
    if missing:
        raise HTTPException(status_code=503, detail=f"missing visualisation tables: {', '.join(missing)}")

def _json_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
         return {}




def _model_dump(model: BaseModel) -> dict[str, Any]:
    """Return a plain dict across Pydantic v1/v2."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _require_layout_exists(conn: Any, layout_id: str) -> None:
    """Raise a 404 when a referenced layout does not exist."""
    existing = conn.execute("SELECT id FROM layout WHERE id = ?", (layout_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Layout not found")


def _normalise_import_berth_ids(berth_ids: list[str]) -> list[str]:
    """Trim, deduplicate, and validate selected berth IDs."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for berth_id in berth_ids:
        berth_name = berth_id.strip()
        if not berth_name or berth_name in seen:
            continue
        seen.add(berth_name)
        cleaned.append(berth_name)
    if not cleaned:
        raise HTTPException(status_code=422, detail="At least one berth must be selected")
    return cleaned


_ADMIN_CSS = """
    :root{--fg:#1f2937;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb;--danger:#dc2626}
    *{box-sizing:border-box}
    body{font-family:system-ui,Arial,sans-serif;margin:0;background:#f6f8fb;color:var(--fg)}
    main{max-width:1400px;margin:0 auto;padding:24px}
    h1,h2,h3{margin:0 0 12px}
    section,.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 20px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
    .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
    .cards .card{margin:0}
    label{display:flex;flex-direction:column;gap:4px;font-size:14px}
    input,textarea,select,button,.button{font:inherit;padding:8px;border:1px solid #cbd5e1;border-radius:8px}
    button,.button{cursor:pointer;background:var(--accent);color:#fff;border-color:var(--accent);text-decoration:none;display:inline-block}
    button.danger{background:var(--danger);border-color:var(--danger)}
    button.secondary,.button.secondary{background:#fff;color:var(--fg);border-color:#cbd5e1}
    button:disabled{opacity:.5;cursor:not-allowed}
    :focus-visible{outline:3px solid #f59e0b;outline-offset:2px}
    .table-wrap{overflow-x:auto}
    table{width:100%;border-collapse:collapse;margin-top:12px}
    th,td{border-bottom:1px solid var(--line);padding:6px;vertical-align:top;text-align:left}
    th{background:#f8fafc}
    td input,td select{width:100%;min-width:70px}
    td.actions{white-space:nowrap}
    .hint{color:var(--muted);font-size:13px;margin-top:4px}
    .section-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;flex-wrap:wrap}
    .toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;align-items:flex-end}
    .pager{display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap}
    .status-message{margin-top:10px;font-size:14px;color:var(--accent)}
    .status-message.error{color:var(--danger)}
    .sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
    dialog{max-width:min(960px,95vw);width:100%;border:1px solid var(--line);border-radius:12px;padding:16px}
    dialog::backdrop{background:rgba(0,0,0,.4)}
    @media (max-width:600px){main{padding:12px}}
"""

_ADMIN_COMMON_JS = """
function el(tag, props, children) {
  const node = document.createElement(tag);
  Object.entries(props || {}).forEach(([k, v]) => {
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  });
  (children || []).forEach((c) => node.appendChild(c));
  return node;
}

function setStatus(id, message, isError) {
  const status = document.getElementById(id);
  if (!status) return;
  status.textContent = message || "";
  status.className = isError ? "status-message error" : "status-message";
}

async function submitJson(url, method, payload) {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) {
    let detail = text || response.statusText;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed.detail === "string") detail = parsed.detail;
      else if (Array.isArray(parsed.detail)) detail = parsed.detail.map((d) => (d.loc || []).join(".") + ": " + d.msg).join("; ");
    } catch (e) { /* keep raw text */ }
    throw new Error(detail);
  }
  return text ? JSON.parse(text) : {};
}

function textCell(text) {
  return el("td", { text: text || "" });
}

function messageRow(colspan, text) {
  const cell = el("td", { text, colspan: String(colspan) });
  return el("tr", {}, [cell]);
}

/* ---------- headcode chain import dialog ---------- */
let headcodeRows = [];
let currentChain = [];

function trainChainUrl(train) {
  if (train.td_area && train.headcode) {
    return `/td/${encodeURIComponent(train.td_area)}/${encodeURIComponent(train.headcode)}/chain`;
  }
  return `/train/${encodeURIComponent(train.id)}/chain`;
}

function renderHeadcodeResults() {
  const tbody = document.getElementById("headcode-results");
  const query = document.getElementById("headcode-search").value.trim().toLowerCase();
  tbody.replaceChildren();
  const liveKeys = new Set(
    headcodeRows.filter((t) => t.source === "td_state" && t.duplicate_key).map((t) => t.duplicate_key)
  );
  const filtered = headcodeRows.filter((train) => {
    if (train.source === "train" && train.duplicate_key && liveKeys.has(train.duplicate_key)) return false;
    return [train.headcode, train.td_area, train.id, train.description, train.current_berth]
      .some((v) => String(v || "").toLowerCase().includes(query));
  });
  if (!filtered.length) {
    tbody.appendChild(messageRow(6, "No headcodes matched the search."));
    return;
  }
  filtered.slice(0, 100).forEach((train) => {
    const button = el("button", { type: "button", class: "secondary", text: "View chain" });
    button.addEventListener("click", () => loadTrainChain(train));
    tbody.appendChild(el("tr", {}, [
      textCell(train.headcode), textCell(train.td_area), textCell(train.id),
      textCell(train.description), textCell(train.current_berth),
      el("td", {}, [button]),
    ]));
  });
  if (filtered.length > 100) {
    tbody.appendChild(messageRow(6, `Showing first 100 of ${filtered.length} matches; refine the search.`));
  }
}

function renderChainRows(chainData, train) {
  currentChain = Array.isArray(chainData.chain) ? chainData.chain : [];
  const tbody = document.getElementById("chain-results");
  tbody.replaceChildren();
  const label = [train.headcode || chainData.headcode || train.id, train.td_area || chainData.td_area]
    .filter(Boolean).join(" • ");
  document.getElementById("chain-summary").textContent = label ? `Chain for ${label}` : "Selected berth chain";
  if (!currentChain.length) {
    tbody.appendChild(messageRow(5, "No berth chain is available for the selected headcode."));
    return;
  }
  currentChain.forEach((item, index) => {
    const checkbox = el("input", {
      type: "checkbox", class: "chain-import-checkbox",
      "aria-label": `Import berth ${item.berth_id || index + 1}`,
    });
    checkbox.checked = Boolean(item.berth_id);
    checkbox.dataset.berthId = item.berth_id || "";
    tbody.appendChild(el("tr", {}, [
      el("td", {}, [checkbox]), textCell(item.berth_id),
      textCell(item.enter_time || item.last_time_iso), textCell(item.source), textCell(item.reason),
    ]));
  });
}

async function loadHeadcodes() {
  setStatus("chain-import-status", "");
  const tbody = document.getElementById("headcode-results");
  tbody.replaceChildren(messageRow(6, "Loading headcodes…"));
  try {
    const response = await fetch("/trains");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    headcodeRows = await response.json();
    renderHeadcodeResults();
  } catch (error) {
    console.error(error);
    tbody.replaceChildren(messageRow(6, "Failed to load headcodes."));
  }
}

async function loadTrainChain(train) {
  setStatus("chain-import-status", "Loading berth chain…");
  try {
    const response = await fetch(trainChainUrl(train));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderChainRows(await response.json(), train);
    setStatus("chain-import-status", `Loaded ${currentChain.length} berth item(s).`);
  } catch (error) {
    console.error(error);
    setStatus("chain-import-status", "Failed to load the berth chain.", true);
  }
}

function setAllChainSelections(selected) {
  document.querySelectorAll(".chain-import-checkbox").forEach((cb) => { cb.checked = selected; });
}

async function importSelectedChainBerths() {
  const layoutId = document.getElementById("chain-import-layout_id").value;
  const berthIds = Array.from(document.querySelectorAll(".chain-import-checkbox"))
    .filter((cb) => cb.checked && cb.dataset.berthId).map((cb) => cb.dataset.berthId);
  if (!layoutId) return setStatus("chain-import-status", "Choose a layout before importing berths.", true);
  if (!berthIds.length) return setStatus("chain-import-status", "Select at least one berth to import.", true);
  try {
    const result = await submitJson("/api/berths/import-chain", "POST", { layout_id: layoutId, berth_ids: berthIds });
    const imported = Array.isArray(result.imported) ? result.imported.length : 0;
    const skipped = Array.isArray(result.skipped_existing) ? result.skipped_existing.length : 0;
    setStatus("chain-import-status", `Imported ${imported} berth(s) into ${layoutId}${skipped ? `; skipped ${skipped} existing.` : "."}`);
    document.dispatchEvent(new CustomEvent("berths-imported", { detail: { layoutId } }));
  } catch (error) {
    console.error(error);
    setStatus("chain-import-status", `Failed to import selected berths: ${error.message}`, true);
  }
}

async function populateImportLayouts(defaultLayout) {
  const select = document.getElementById("chain-import-layout_id");
  const response = await fetch("/api/layouts");
  const layouts = response.ok ? await response.json() : [];
  select.replaceChildren();
  layouts.forEach((layout) => {
    const option = el("option", { value: layout.id, text: `${layout.id} — ${layout.name}` });
    if (layout.id === defaultLayout) option.selected = true;
    select.appendChild(option);
  });
}

async function openImportDialog(defaultLayout) {
  const dialog = document.getElementById("import-dialog");
  await populateImportLayouts(defaultLayout);
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  loadHeadcodes();
  document.getElementById("headcode-search").focus();
}

function closeImportDialog() {
  const dialog = document.getElementById("import-dialog");
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}
"""

_ADMIN_IMPORT_DIALOG = """
<dialog id="import-dialog" aria-labelledby="import-title">
  <div class="section-head">
    <div>
      <h2 id="import-title">Import berths from headcode chain</h2>
      <div class="hint">Search headcodes, inspect the berth chain, then choose exactly which berths to import into a layout.</div>
    </div>
    <button type="button" class="secondary" onclick="closeImportDialog()">Close</button>
  </div>
  <div class="toolbar">
    <label>Search headcodes
      <input id="headcode-search" placeholder="2C90, EK, BRTH_1…" oninput="renderHeadcodeResults()" />
    </label>
    <label>Import into layout
      <select id="chain-import-layout_id"></select>
    </label>
    <button type="button" class="secondary" onclick="loadHeadcodes()">Refresh headcodes</button>
  </div>
  <h3>Matching headcodes</h3>
  <div class="table-wrap"><table>
    <thead><tr><th>Headcode</th><th>TD area</th><th>Train ID</th><th>Description</th><th>Current berth</th><th>Action</th></tr></thead>
    <tbody id="headcode-results"></tbody>
  </table></div>
  <h3 style="margin-top:16px">Selected berth chain</h3>
  <div class="hint" id="chain-summary">Select a headcode to view its berth chain.</div>
  <div class="toolbar">
    <button type="button" class="secondary" onclick="setAllChainSelections(true)">Select all</button>
    <button type="button" class="secondary" onclick="setAllChainSelections(false)">Clear all</button>
    <button type="button" onclick="importSelectedChainBerths()">Import selected berths</button>
  </div>
  <div class="table-wrap"><table>
    <thead><tr><th>Import</th><th>Berth</th><th>Entered</th><th>Source</th><th>Reason</th></tr></thead>
    <tbody id="chain-results"><tr><td colspan="5">No berth chain loaded.</td></tr></tbody>
  </table></div>
  <div id="chain-import-status" class="status-message" role="status" aria-live="polite"></div>
</dialog>
"""

_ADMIN_LIST_JS = """
async function deleteLayout(layoutId) {
  if (!confirm(`Delete layout ${layoutId} and all of its berths and signals? This cannot be undone.`)) return;
  try {
    await submitJson(`/api/layouts/${encodeURIComponent(layoutId)}`, "DELETE");
    window.location.reload();
  } catch (error) {
    setStatus("admin-status", `Failed to delete layout ${layoutId}: ${error.message}`, true);
  }
}
document.querySelectorAll("[data-delete-layout]").forEach((btn) => {
  btn.addEventListener("click", () => deleteLayout(btn.dataset.deleteLayout));
});
document.getElementById("open-import").addEventListener("click", () => openImportDialog(""));
"""


def _render_admin_page(layout_rows: list[Any], counts: Optional[dict[str, dict[str, int]]] = None) -> str:
    """Render the layout landing/list page."""
    counts = counts or {}

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    cards = []
    for row in layout_rows:
        lid = row["id"]
        c = counts.get(lid, {})
        cards.append(
            f"""<article class="card" aria-label="Layout {esc(lid)}">
  <h2>{esc(row['name'])}</h2>
  <div class="hint">ID: {esc(lid)}</div>
  <p>{esc(row['description']) or '<span class="hint">No description</span>'}</p>
  <div class="hint">{int(c.get('berths', 0))} berth(s) · {int(c.get('signals', 0))} signal(s)</div>
  <div class="toolbar">
    <a class="button" href="/admin/layouts/{esc(urllib.parse.quote(str(lid), safe=''))}">Edit</a>
    <button type="button" class="danger" data-delete-layout="{esc(lid)}">Delete</button>
  </div>
</article>"""
        )
    body = "".join(cards) or '<p class="hint">No layouts yet. Create one to get started.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Visualisation admin</title>
  <style>{_ADMIN_CSS}</style>
</head>
<body>
<main>
  <div class="section-head">
    <div>
      <h1>Visualisation admin</h1>
      <p class="hint">Manage layouts. Open a layout to edit its settings, berths and signals.</p>
    </div>
    <div class="toolbar">
      <a class="button" href="/admin/new-layout">Create layout</a>
      <button type="button" class="secondary" id="open-import">Import berths from headcode chain</button>
    </div>
  </div>
  <div id="admin-status" class="status-message" role="alert"></div>
  <h2 class="sr-only">Layouts</h2>
  <div class="cards">{body}</div>
</main>
{_ADMIN_IMPORT_DIALOG}
<script>
{_ADMIN_COMMON_JS}
{_ADMIN_LIST_JS}
</script>
</body>
</html>
"""


_ADMIN_LAYOUT_JS = """
const body = document.body;
let layoutId = body.dataset.layoutId || "";
const isNew = body.dataset.mode === "new";

const TABLES = {
  berth: {
    api: "/api/berths", status: "berth-status", body: "berth-rows", cols: 9,
    fields: [["id","text"],["name","text"],["x","number"],["y","number"],["width","number"],["height","number"],["berth_type",["normal","platform","siding"]]],
  },
  signal: {
    api: "/api/signals", status: "signal-status", body: "signal-rows", cols: 7,
    fields: [["id","text"],["name","text"],["x","number"],["y","number"],["signal_type",["auto","controlled","shunt"]]],
  },
};
const tableState = { berth: { page: 1 }, signal: { page: 1 } };

function readLayoutForm() {
  const raw = document.getElementById("layout-data").value.trim() || "{}";
  let data;
  try { data = JSON.parse(raw); } catch (e) { throw new Error("Layout data is not valid JSON."); }
  return {
    id: document.getElementById("layout-id").value.trim(),
    name: document.getElementById("layout-name").value.trim(),
    description: document.getElementById("layout-description").value.trim() || null,
    data,
  };
}

async function saveLayout(event) {
  event.preventDefault();
  try {
    const payload = readLayoutForm();
    if (isNew) await submitJson("/api/layouts", "POST", payload);
    else await submitJson(`/api/layouts/${encodeURIComponent(layoutId)}`, "PUT", payload);
    window.location.href = `/admin/layouts/${encodeURIComponent(payload.id)}`;
  } catch (error) {
    setStatus("layout-status", `Failed to save layout: ${error.message}`, true);
  }
}

async function loadLayout() {
  try {
    const response = await fetch(`/api/layouts/${encodeURIComponent(layoutId)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const layout = await response.json();
    document.getElementById("layout-id").value = layout.id;
    document.getElementById("layout-name").value = layout.name;
    document.getElementById("layout-description").value = layout.description || "";
    document.getElementById("layout-data").value = JSON.stringify(layout.data || {}, null, 2);
  } catch (error) {
    setStatus("layout-status", `Failed to load layout: ${error.message}`, true);
  }
}

async function deleteLayout() {
  if (!confirm(`Delete layout ${layoutId} and all of its berths and signals? This cannot be undone.`)) return;
  try {
    await submitJson(`/api/layouts/${encodeURIComponent(layoutId)}`, "DELETE");
    window.location.href = "/admin";
  } catch (error) {
    setStatus("layout-status", `Failed to delete layout: ${error.message}`, true);
  }
}

function fieldInput(name, kind, value, label) {
  let input;
  if (Array.isArray(kind)) {
    input = el("select", { "aria-label": label });
    kind.forEach((opt) => input.appendChild(el("option", { value: opt, text: opt })));
  } else {
    input = el("input", { type: kind, "aria-label": label });
  }
  input.name = name;
  if (value !== undefined && value !== null) input.value = value;
  return input;
}

function collect(kind, container) {
  const payload = { layout_id: layoutId };
  TABLES[kind].fields.forEach(([name, type]) => {
    const input = container.querySelector(`[name="${name}"]`);
    payload[name] = type === "number" ? Number(input.value) : input.value.trim();
  });
  return payload;
}

async function reloadAfter(kind, action, okMessage) {
  const cfg = TABLES[kind];
  try {
    await action();
    setStatus(cfg.status, okMessage);
    await loadTable(kind);
  } catch (error) {
    setStatus(cfg.status, `Failed: ${error.message}`, true);
  }
}

function renderRow(kind, item) {
  const cfg = TABLES[kind];
  const tr = el("tr");
  tr.dataset.originalId = item.id;
  cfg.fields.forEach(([name, type]) => {
    tr.appendChild(el("td", {}, [fieldInput(name, type, item[name], `${kind} ${item.id} ${name}`)]));
  });
  const save = el("button", { type: "button", text: "Save" });
  save.addEventListener("click", () => reloadAfter(kind,
    () => submitJson(`${cfg.api}/${encodeURIComponent(item.id)}`, "PUT", collect(kind, tr)), `Saved ${kind} ${item.id}.`));
  const del = el("button", { type: "button", class: "danger", text: "Delete" });
  del.addEventListener("click", () => {
    if (!confirm(`Delete ${kind} ${item.id}?`)) return;
    reloadAfter(kind, () => submitJson(`${cfg.api}/${encodeURIComponent(item.id)}`, "DELETE"), `Deleted ${kind} ${item.id}.`);
  });
  tr.appendChild(el("td", { class: "actions" }, [save, document.createTextNode(" "), del]));
  return tr;
}

async function loadTable(kind) {
  const cfg = TABLES[kind];
  const tbody = document.getElementById(cfg.body);
  const q = document.getElementById(`${kind}-q`).value.trim();
  const type = document.getElementById(`${kind}-type`).value;
  const size = document.getElementById(`${kind}-page-size`).value;
  const params = new URLSearchParams({ page: tableState[kind].page, page_size: size });
  if (q) params.set("q", q);
  if (type) params.set("type", type);
  tbody.setAttribute("aria-busy", "true");
  tbody.replaceChildren(messageRow(cfg.fields.length + 1, "Loading…"));
  try {
    const response = await fetch(`/api/layouts/${encodeURIComponent(layoutId)}/${kind}s?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    tableState[kind].page = data.page;
    tbody.replaceChildren();
    if (!data.items.length) {
      tbody.appendChild(messageRow(cfg.fields.length + 1, q || type ? `No ${kind}s match the filters.` : `No ${kind}s in this layout yet.`));
    }
    data.items.forEach((item) => tbody.appendChild(renderRow(kind, item)));
    document.getElementById(`${kind}-page-info`).textContent =
      `Page ${data.page} of ${data.pages} · ${data.total} ${kind}(s)`;
    document.getElementById(`${kind}-prev`).disabled = data.page <= 1;
    document.getElementById(`${kind}-next`).disabled = data.page >= data.pages;
  } catch (error) {
    console.error(error);
    tbody.replaceChildren(messageRow(cfg.fields.length + 1, "Failed to load data."));
    setStatus(cfg.status, `Failed to load ${kind}s: ${error.message}`, true);
  } finally {
    tbody.removeAttribute("aria-busy");
  }
}

function setupTable(kind) {
  const cfg = TABLES[kind];
  const reset = () => { tableState[kind].page = 1; loadTable(kind); };
  let timer = null;
  document.getElementById(`${kind}-q`).addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(reset, 250); });
  document.getElementById(`${kind}-type`).addEventListener("change", reset);
  document.getElementById(`${kind}-page-size`).addEventListener("change", reset);
  document.getElementById(`${kind}-prev`).addEventListener("click", () => { tableState[kind].page -= 1; loadTable(kind); });
  document.getElementById(`${kind}-next`).addEventListener("click", () => { tableState[kind].page += 1; loadTable(kind); });
  const form = document.getElementById(`${kind}-create`);
  cfg.fields.forEach(([name, type]) => {
    const defaults = { x: 0, y: 0, width: 60, height: 30, berth_type: "normal", signal_type: "auto" };
    const input = fieldInput(name, type, defaults[name], name);
    const label = el("label", { text: name.replace("_", " ") });
    label.appendChild(input);
    form.querySelector(".fields").appendChild(label);
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    reloadAfter(kind, () => submitJson(cfg.api, "POST", collect(kind, form)), `Created ${kind}.`);
  });
  loadTable(kind);
}

document.getElementById("layout-form").addEventListener("submit", saveLayout);
if (!isNew) {
  loadLayout();
  document.getElementById("delete-layout").addEventListener("click", deleteLayout);
  setupTable("berth");
  setupTable("signal");
  document.getElementById("open-import").addEventListener("click", () => openImportDialog(layoutId));
  document.addEventListener("berths-imported", () => loadTable("berth"));
}
"""


def _render_layout_page(layout_id: Optional[str]) -> str:
    """Render the layout create/edit page. ``layout_id`` is None when creating."""
    is_new = layout_id is None
    esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731

    def table_section(kind: str, title: str, headers: list[str], types: list[str]) -> str:
        head = "".join(f"<th scope='col'>{esc(h)}</th>" for h in headers + ["Actions"])
        type_opts = "".join(f"<option value='{t}'>{t}</option>" for t in types)
        return f"""
  <section aria-labelledby="{kind}-heading">
    <h2 id="{kind}-heading">{title}</h2>
    <details>
      <summary>Add {kind}</summary>
      <form id="{kind}-create" class="toolbar">
        <div class="fields grid" style="flex:1"></div>
        <button type="submit">Create {kind}</button>
      </form>
    </details>
    <div class="toolbar">
      <label>Search <input id="{kind}-q" type="search" placeholder="ID or name" /></label>
      <label>Type <select id="{kind}-type"><option value="">All types</option>{type_opts}</select></label>
      <label>Per page <select id="{kind}-page-size"><option>10</option><option selected>25</option><option>50</option><option>100</option></select></label>
    </div>
    <div id="{kind}-status" class="status-message" role="status" aria-live="polite"></div>
    <div class="table-wrap"><table>
      <caption class="sr-only">{title}</caption>
      <thead><tr>{head}</tr></thead>
      <tbody id="{kind}-rows"></tbody>
    </table></div>
    <div class="pager">
      <button type="button" class="secondary" id="{kind}-prev">Previous</button>
      <span id="{kind}-page-info" aria-live="polite"></span>
      <button type="button" class="secondary" id="{kind}-next">Next</button>
    </div>
  </section>"""

    related = ""
    actions = ""
    if not is_new:
        related = table_section(
            "berth", "Berths", ["ID", "Name", "X", "Y", "Width", "Height", "Type"], ["normal", "platform", "siding"]
        ) + table_section("signal", "Signals", ["ID", "Name", "X", "Y", "Type"], ["auto", "controlled", "shunt"])
        actions = (
            '<button type="button" class="secondary" id="open-import">Import berths from headcode chain</button>'
            '<button type="button" class="danger" id="delete-layout">Delete layout</button>'
        )
    mode = "new" if is_new else "edit"
    lid_attr = "" if is_new else f' data-layout-id="{esc(layout_id)}"'
    title = "Create layout" if is_new else f"Edit layout {esc(layout_id)}"
    dialog = "" if is_new else _ADMIN_IMPORT_DIALOG
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{title} – Visualisation admin</title>
  <style>{_ADMIN_CSS}</style>
</head>
<body data-mode="{mode}"{lid_attr}>
<main>
  <p><a href="/admin">← All layouts</a></p>
  <h1>{title}</h1>
  <section aria-labelledby="settings-heading">
    <h2 id="settings-heading">Layout settings</h2>
    <form id="layout-form">
      <div class="grid">
        <label>ID <input id="layout-id" required /></label>
        <label>Name <input id="layout-name" required /></label>
        <label>Description <input id="layout-description" /></label>
        <label>Metadata (JSON) <textarea id="layout-data" rows="5">{{}}</textarea></label>
      </div>
      <div class="toolbar">
        <button type="submit">{'Create layout' if is_new else 'Save layout'}</button>
        {actions}
      </div>
      <div id="layout-status" class="status-message" role="alert"></div>
    </form>
  </section>
  {related}
</main>
{dialog}
<script>
{_ADMIN_COMMON_JS}
{_ADMIN_LAYOUT_JS}
</script>
</body>
</html>
"""



def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _format_event_row(row: Any, source: str) -> dict[str, Any]:
    data = _row_dict(row)
    data["source"] = source
    data["berth_id"] = data.get("to_berth") or data.get("from_berth") or data.get("object_id")
    data["train_ref"] = data.get("headcode") or data.get("train_id")
    data["display_ts"] = data.get("ts_iso") or data.get("ts")
    return data


def _fetch_td_snapshot(
    conn: Any,
    td_area: str | None = None,
    headcode: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Read current schematic state from TD tables, falling back to PoC events."""
    snapshot: dict[str, Any] = {
        "source": "empty",
        "td_area": td_area,
        "headcode": headcode,
        "updated_at": None,
        "trains": [],
        "events": [],
    }

    try:
        if _table_exists(conn, "td_state"):
            clauses = []
            params: list[Any] = []
            if td_area:
                clauses.append("td_area = ?")
                params.append(td_area)
            if headcode:
                clauses.append("headcode = ?")
                params.append(headcode)

            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth,
                       stanox, location_name, platform, uid
                FROM td_state
                {where}
                ORDER BY last_time_ms DESC, td_area, headcode
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            if rows:
                snapshot["source"] = "td_state"
                snapshot["trains"] = [
                    {
                        "marker_id": f"{row['td_area']}:{row['headcode']}",
                        "td_area": row["td_area"],
                        "headcode": row["headcode"],
                        "uid": row["uid"],
                        "current_berth": row["to_berth"] or row["from_berth"],
                        "from_berth": row["from_berth"],
                        "to_berth": row["to_berth"],
                        "last_time_ms": row["last_time_ms"],
                        "last_time_iso": row["last_time_iso"],
                        "stanox": row["stanox"],
                        "location_name": row["location_name"],
                        "platform": row["platform"],
                    }
                    for row in rows
                    if (row["to_berth"] or row["from_berth"])
                ]
                snapshot["updated_at"] = rows[0]["last_time_iso"] if rows else None

        if _table_exists(conn, "td_berth_events"):
            clauses = []
            params = []
            if td_area:
                clauses.append("td_area = ?")
                params.append(td_area)
            if headcode:
                clauses.append("headcode = ?")
                params.append(headcode)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT id, ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr
                FROM td_berth_events
                {where}
                ORDER BY ts_ms DESC, id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            if rows:
                snapshot["events"] = [
                    _format_event_row(row, "td_berth_events")
                    for row in rows
                ]
                if snapshot["source"] == "empty":
                    latest_by_key: dict[tuple[str | None, str | None], Any] = {}
                    for row in rows:
                        key = (row["td_area"], row["headcode"])
                        if key not in latest_by_key:
                            latest_by_key[key] = row
                    snapshot["source"] = "td_berth_events"
                    snapshot["trains"] = [
                        {
                            "marker_id": f"{row['td_area']}:{row['headcode']}",
                            "td_area": row["td_area"],
                            "headcode": row["headcode"],
                            "current_berth": row["to_berth"] or row["from_berth"],
                            "from_berth": row["from_berth"],
                            "to_berth": row["to_berth"],
                            "last_time_ms": row["ts_ms"],
                            "last_time_iso": row["ts_iso"],
                            "description": row["descr"],
                        }
                        for row in latest_by_key.values()
                        if row["headcode"] and (row["to_berth"] or row["from_berth"])
                    ]
                    snapshot["updated_at"] = rows[0]["ts_iso"] if rows else None

        if not snapshot["trains"] and _table_exists(conn, "event"):
            clauses = []
            params = []
            if headcode:
                clauses.append("COALESCE(t.headcode, e.train_id) = ?")
                params.append(headcode)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT e.id, e.ts, e.source, e.train_id, e.event_type, e.object_id, e.payload, t.headcode
                FROM event e
                LEFT JOIN train t ON t.id = e.train_id
                {where}
                ORDER BY e.ts DESC, e.id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            if rows:
                snapshot["source"] = "event"
                snapshot["events"] = [
                    _format_event_row(row, "event")
                    for row in rows
                ]
                active_by_train: dict[str, dict[str, Any]] = {}
                for row in reversed(rows):
                    train_key = row["train_id"] or row["headcode"] or row["object_id"]
                    if not train_key:
                        continue
                    event_type = row["event_type"]
                    if event_type == "berth_enter":
                        active_by_train[train_key] = {
                            "marker_id": train_key,
                            "td_area": td_area or "",
                            "headcode": row["headcode"] or row["train_id"] or "",
                            "train_id": row["train_id"],
                            "current_berth": row["object_id"],
                            "from_berth": None,
                            "to_berth": row["object_id"],
                            "last_time_iso": row["ts"],
                            "last_time_ms": None,
                        }
                    elif event_type == "berth_exit":
                        current = active_by_train.get(train_key)
                        if current and current["current_berth"] == row["object_id"]:
                            active_by_train.pop(train_key, None)
                snapshot["trains"] = list(active_by_train.values())
                snapshot["updated_at"] = rows[0]["ts"] if rows else None

        snapshot["train_count"] = len(snapshot["trains"])
        snapshot["event_count"] = len(snapshot["events"])
        return snapshot
    except DB_ERRORS as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


def _fetch_td_chain(
    conn: Any,
    td_area: str,
    headcode: str,
) -> dict[str, Any]:
    """Return a detailed TD movement chain for a single headcode."""
    try:
        if not _table_exists(conn, "td_berth_events"):
            return {"td_area": td_area, "headcode": headcode, "chain": []}

        rows = conn.execute(
            """
            SELECT ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr
            FROM td_berth_events
            WHERE td_area = ? AND headcode = ?
            ORDER BY ts_ms ASC, id ASC
            """,
            (td_area, headcode),
        ).fetchall()
        chain = []
        for row in rows:
            berth_id = row["to_berth"] or row["from_berth"]
            if not berth_id:
                continue
            chain.append(
                {
                    "berth_id": berth_id,
                    "enter_time": row["ts_iso"],
                    "exit_time": None,
                    "inferred": False,
                    "confidence": 1.0,
                    "source": "td_berth_events",
                    "reason": f"TD {row['msg_type']} event from {row['from_berth'] or 'unknown'} to {row['to_berth'] or 'unknown'}",
                    "td_area": row["td_area"],
                    "headcode": row["headcode"],
                    "from_berth": row["from_berth"],
                    "to_berth": row["to_berth"],
                    "msg_type": row["msg_type"],
                    "ts_ms": row["ts_ms"],
                }
            )

        if not chain and _table_exists(conn, "td_state"):
            row = conn.execute(
                """
                SELECT td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth,
                       stanox, location_name, platform
                FROM td_state
                WHERE td_area = ? AND headcode = ?
                ORDER BY last_time_ms DESC
                LIMIT 1
                """,
                (td_area, headcode),
            ).fetchone()
            if row and (row["to_berth"] or row["from_berth"]):
                chain.append(
                    {
                        "berth_id": row["to_berth"] or row["from_berth"],
                        "enter_time": row["last_time_iso"],
                        "exit_time": None,
                        "inferred": False,
                        "confidence": 1.0,
                        "source": "td_state",
                        "reason": "Current TD state snapshot",
                        "td_area": row["td_area"],
                        "headcode": row["headcode"],
                        "from_berth": row["from_berth"],
                        "to_berth": row["to_berth"],
                        "last_time_ms": row["last_time_ms"],
                    }
                )

        return {"td_area": td_area, "headcode": headcode, "chain": chain}
    except DB_ERRORS as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _paged_children(
    table: str,
    type_column: str,
    allowed_types: tuple[str, ...],
    layout_id: str,
    q: Optional[str],
    type_: Optional[str],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Return a filtered, paged slice of berth/signal rows for a layout."""
    if type_ and type_ not in allowed_types:
        raise HTTPException(status_code=422, detail="Invalid type filter")
    where = "layout_id = ?"
    params: list[Any] = [layout_id]
    if q and q.strip():
        like = f"%{_escape_like(q.strip().lower())}%"
        where += " AND (LOWER(id) LIKE ? ESCAPE '!' OR LOWER(name) LIKE ? ESCAPE '!')"
        params.extend([like, like])
    if type_:
        where += f" AND {type_column} = ?"
        params.append(type_)
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        _require_layout_exists(conn, layout_id)
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"])
        pages = max(1, -(-total // page_size))
        page = min(page, pages)
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY name, id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "items": [_row_dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }


@app.get("/admin", response_class=HTMLResponse)
async def admin():
    """Render the layout landing page with CRUD controls."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        layout_rows = conn.execute("SELECT * FROM layout ORDER BY id").fetchall()
        counts: dict[str, dict[str, int]] = {}
        for table, key in (("berth", "berths"), ("signal", "signals")):
            for row in conn.execute(f"SELECT layout_id, COUNT(*) AS n FROM {table} GROUP BY layout_id").fetchall():
                counts.setdefault(row["layout_id"], {})[key] = int(row["n"])
        return _render_admin_page(layout_rows, counts)


@app.get("/admin/new-layout", response_class=HTMLResponse)
async def admin_new_layout():
    """Render the create-layout page."""
    return _render_layout_page(None)


@app.get("/admin/layouts/{layout_id}", response_class=HTMLResponse)
async def admin_edit_layout(layout_id: str):
    """Render the layout edit page with paged berth and signal tables."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        _require_layout_exists(conn, layout_id)
    return _render_layout_page(layout_id)


@app.get("/api/layouts")
async def list_layouts():
    """List all layouts."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        rows = conn.execute("SELECT * FROM layout ORDER BY id").fetchall()
        return [
            {"id": r["id"], "name": r["name"], "description": r["description"], "data": _json_or_empty(r["data"])}
            for r in rows
        ]


@app.get("/api/layouts/{layout_id}")
async def read_layout(layout_id: str):
    """Return a single layout."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        r = conn.execute("SELECT * FROM layout WHERE id = ?", (layout_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Layout not found")
        return {"id": r["id"], "name": r["name"], "description": r["description"], "data": _json_or_empty(r["data"])}


@app.get("/api/layouts/{layout_id}/berths")
async def list_layout_berths(
    layout_id: str,
    q: Optional[str] = None,
    type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """List a layout's berths with search, type filter and paging."""
    return _paged_children("berth", "berth_type", ("normal", "platform", "siding"), layout_id, q, type, page, page_size)


@app.get("/api/layouts/{layout_id}/signals")
async def list_layout_signals(
    layout_id: str,
    q: Optional[str] = None,
    type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """List a layout's signals with search, type filter and paging."""
    return _paged_children("signal", "signal_type", ("auto", "controlled", "shunt"), layout_id, q, type, page, page_size)


@app.post("/api/layouts")
async def create_layout(payload: LayoutPayload):
    """Create a layout row."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO layout (id, name, description, data)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        payload.id,
                        payload.name,
                        payload.description,
                        json.dumps(payload.data),
                    ),
                )
        except INTEGRITY_ERRORS as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        rowid = None if getattr(conn, "is_postgres", False) else cursor.lastrowid
        return {"status": "created", "rowid": rowid, "layout": _model_dump(payload)}


@app.put("/api/layouts/{layout_id}")
async def update_layout(layout_id: str, payload: LayoutPayload):
    """Update a layout row and keep child berth/signal rows in sync when renaming."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        try:
            with conn:
                existing = conn.execute("SELECT id FROM layout WHERE id = ?", (layout_id,)).fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Layout not found")
                if payload.id != layout_id:
                    conn.execute("UPDATE berth SET layout_id = ? WHERE layout_id = ?", (payload.id, layout_id))
                    conn.execute("UPDATE signal SET layout_id = ? WHERE layout_id = ?", (payload.id, layout_id))
                conn.execute(
                    """
                    UPDATE layout
                    SET id = ?, name = ?, description = ?, data = ?
                    WHERE id = ?
                    """,
                    (payload.id, payload.name, payload.description, json.dumps(payload.data), layout_id),
                )
        except INTEGRITY_ERRORS as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "updated", "id": payload.id, "layout": _model_dump(payload)}


@app.delete("/api/layouts/{layout_id}")
async def delete_layout(layout_id: str):
    """Delete a layout and its berths/signals."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        with conn:
            existing = conn.execute("SELECT id FROM layout WHERE id = ?", (layout_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Layout not found")
            conn.execute("DELETE FROM berth WHERE layout_id = ?", (layout_id,))
            conn.execute("DELETE FROM signal WHERE layout_id = ?", (layout_id,))
            conn.execute("DELETE FROM layout WHERE id = ?", (layout_id,))
        return {"status": "deleted", "id": layout_id}


@app.post("/api/berths")
async def create_berth(payload: BerthPayload):
    """Create a berth row."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        try:
            with conn:
                _require_layout_exists(conn, payload.layout_id)
                conn.execute(
                    """
                    INSERT INTO berth (id, layout_id, name, x, y, width, height, berth_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload.id,
                        payload.layout_id,
                        payload.name,
                        payload.x,
                        payload.y,
                        payload.width,
                        payload.height,
                        payload.berth_type,
                    ),
                )
        except INTEGRITY_ERRORS as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "created", "id": payload.id, "berth": _model_dump(payload)}


@app.post("/api/berths/import-chain")
async def import_chain_berths(payload: BerthImportPayload):
    """Import selected berth IDs from a displayed chain into a layout."""
    berth_names = _normalise_import_berth_ids(payload.berth_ids)
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        with conn:
            _require_layout_exists(conn, payload.layout_id)
            existing_rows = conn.execute(
                """
                SELECT id, name, x, y, width
                FROM berth
                WHERE layout_id = ?
                ORDER BY x ASC, y ASC, id ASC
                """,
                (payload.layout_id,),
            ).fetchall()
            existing_names = {row["name"] for row in existing_rows}
            existing_ids = {row["id"] for row in existing_rows}

            if existing_rows:
                anchor = max(
                    existing_rows,
                    key=lambda row: (
                        (row["x"] or 0) + (row["width"] or DEFAULT_BERTH_WIDTH),
                        row["y"] or 0,
                    ),
                )
                next_x = (
                    int(anchor["x"] or 0)
                    + int(anchor["width"] or DEFAULT_BERTH_WIDTH)
                    + DEFAULT_BERTH_GAP
                )
                next_y = int(anchor["y"] or 0)
            else:
                next_x = DEFAULT_BERTH_START_X
                next_y = DEFAULT_BERTH_START_Y

            rows_to_insert: list[tuple[str, str, str, int, int, int, int, str]] = []
            imported: list[dict[str, Any]] = []
            skipped_existing: list[str] = []
            for berth_name in berth_names:
                berth_id = f"{payload.layout_id}:{berth_name}"
                if berth_name in existing_names or berth_id in existing_ids:
                    skipped_existing.append(berth_name)
                    continue
                rows_to_insert.append(
                    (
                        berth_id,
                        payload.layout_id,
                        berth_name,
                        next_x,
                        next_y,
                        DEFAULT_BERTH_WIDTH,
                        DEFAULT_BERTH_HEIGHT,
                        "normal",
                    )
                )
                imported.append(
                    {"id": berth_id, "name": berth_name, "x": next_x, "y": next_y}
                )
                existing_names.add(berth_name)
                existing_ids.add(berth_id)
                next_x += DEFAULT_BERTH_WIDTH + DEFAULT_BERTH_GAP

            if rows_to_insert:
                conn.executemany(
                    """
                    INSERT INTO berth (id, layout_id, name, x, y, width, height, berth_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )

        return {
            "status": "imported",
            "layout_id": payload.layout_id,
            "imported": imported,
            "skipped_existing": skipped_existing,
        }


@app.put("/api/berths/{berth_id}")
async def update_berth(berth_id: str, payload: BerthPayload):
    """Update a berth row."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        try:
            with conn:
                existing = conn.execute("SELECT id FROM berth WHERE id = ?", (berth_id,)).fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Berth not found")
                _require_layout_exists(conn, payload.layout_id)
                conn.execute(
                    """
                    UPDATE berth
                    SET id = ?, layout_id = ?, name = ?, x = ?, y = ?, width = ?, height = ?, berth_type = ?
                    WHERE id = ?
                    """,
                    (
                        payload.id,
                        payload.layout_id,
                        payload.name,
                        payload.x,
                        payload.y,
                        payload.width,
                        payload.height,
                        payload.berth_type,
                        berth_id,
                    ),
                )
        except INTEGRITY_ERRORS as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "updated", "id": payload.id, "berth": _model_dump(payload)}


@app.delete("/api/berths/{berth_id}")
async def delete_berth(berth_id: str):
    """Delete a berth row."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        with conn:
            existing = conn.execute("SELECT id FROM berth WHERE id = ?", (berth_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Berth not found")
            conn.execute("DELETE FROM berth WHERE id = ?", (berth_id,))
        return {"status": "deleted", "id": berth_id}


@app.post("/api/signals")
async def create_signal(payload: SignalPayload):
    """Create a signal row."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        try:
            with conn:
                _require_layout_exists(conn, payload.layout_id)
                conn.execute(
                    """
                    INSERT INTO signal (id, layout_id, name, x, y, signal_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload.id,
                        payload.layout_id,
                        payload.name,
                        payload.x,
                        payload.y,
                        payload.signal_type,
                    ),
                )
        except INTEGRITY_ERRORS as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "created", "id": payload.id, "signal": _model_dump(payload)}


@app.put("/api/signals/{signal_id}")
async def update_signal(signal_id: str, payload: SignalPayload):
    """Update a signal row."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        try:
            with conn:
                existing = conn.execute("SELECT id FROM signal WHERE id = ?", (signal_id,)).fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Signal not found")
                _require_layout_exists(conn, payload.layout_id)
                conn.execute(
                    """
                    UPDATE signal
                    SET id = ?, layout_id = ?, name = ?, x = ?, y = ?, signal_type = ?
                    WHERE id = ?
                    """,
                    (
                        payload.id,
                        payload.layout_id,
                        payload.name,
                        payload.x,
                        payload.y,
                        payload.signal_type,
                        signal_id,
                    ),
                )
        except INTEGRITY_ERRORS as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "updated", "id": payload.id, "signal": _model_dump(payload)}


@app.delete("/api/signals/{signal_id}")
async def delete_signal(signal_id: str):
    """Delete a signal row."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        with conn:
            existing = conn.execute("SELECT id FROM signal WHERE id = ?", (signal_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Signal not found")
            conn.execute("DELETE FROM signal WHERE id = ?", (signal_id,))
        return {"status": "deleted", "id": signal_id}


WRITE_TOKEN_ENV_VAR = "NROD_RAILHUB_VIS_TOKEN"


def require_write_token(request: Request) -> None:
    """If NROD_RAILHUB_VIS_TOKEN is set, card writes need a matching bearer token header."""
    token = os.environ.get(WRITE_TOKEN_ENV_VAR, "")
    if not token:
        return
    supplied = request.headers.get("authorization", "")
    expected = "Bearer " + token
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def _card_http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=500, detail=f"card configuration unavailable: {exc}")


@app.get("/cards", response_class=HTMLResponse)
async def cards_page():
    """Card setup and live view UI."""
    return HTMLResponse((STATIC_PATH / "cards.html").read_text(encoding="utf-8"))


@app.get("/api/cards")
async def api_list_cards():
    try:
        return {"cards": card_store.list_cards(), "config_file": str(card_store.config_path())}
    except card_store.CardConfigError as exc:
        raise _card_http_error(exc) from exc


@app.get("/api/cards/suggest")
async def api_suggest_sequences(from_station: str, to_station: str):
    """Rank candidate berth sequences between two stations (nothing is saved)."""
    with get_conn() as conn:
        try:
            return sequences.suggest_sequences(conn, from_station, to_station)
        except DB_ERRORS as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/api/cards/chain")
async def api_chain_sequence(td_area: str, headcode: str):
    """Seed a manually edited sequence from one train's observed TD berth events."""
    with get_conn() as conn:
        try:
            return sequences.chain_sequence(conn, td_area, headcode)
        except DB_ERRORS as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/api/cards/{card_id}")
async def api_get_card(card_id: str):
    try:
        card = card_store.get_card(card_id)
    except card_store.CardConfigError as exc:
        raise _card_http_error(exc) from exc
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    return card


@app.post("/api/cards", status_code=201, dependencies=[Depends(require_write_token)])
async def api_create_card(payload: card_store.CardPayload):
    try:
        return card_store.save_card(payload, create=True)
    except KeyError:
        raise HTTPException(status_code=409, detail="card already exists")
    except (card_store.CardConfigError, OSError) as exc:
        raise _card_http_error(exc) from exc


@app.put("/api/cards/{card_id}", dependencies=[Depends(require_write_token)])
async def api_update_card(card_id: str, payload: card_store.CardPayload):
    if payload.id != card_id:
        raise HTTPException(status_code=400, detail="card id in body does not match URL")
    try:
        return card_store.save_card(payload, create=False)
    except KeyError:
        raise HTTPException(status_code=404, detail="card not found")
    except (card_store.CardConfigError, OSError) as exc:
        raise _card_http_error(exc) from exc


@app.delete("/api/cards/{card_id}", dependencies=[Depends(require_write_token)])
async def api_delete_card(card_id: str):
    try:
        deleted = card_store.delete_card(card_id)
    except (card_store.CardConfigError, OSError) as exc:
        raise _card_http_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="card not found")
    return {"status": "deleted", "id": card_id}


@app.get("/api/cards/{card_id}/state")
async def api_card_state(card_id: str, max_age_s: int = 1800, since_revision: str | None = None):
    """Live occupancy for a card, read from persisted TD tables (poll this)."""
    try:
        card = card_store.get_card(card_id)
    except card_store.CardConfigError as exc:
        raise _card_http_error(exc) from exc
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    with get_conn() as conn:
        try:
            return sequences.card_live_state(conn, card, max_age_s=max(0, max_age_s), since_revision=since_revision)
        except DB_ERRORS as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "NROD RailHub Visualization API",
        "endpoints": {
            "layout": "/layout/{layout_id}",
            "berths": "/berths/{layout_id}",
            "trains": "/trains",
            "admin": "/admin",
            "cards": "/cards",
            "train_chain": "/train/{train_id}/chain",
            "state": "/state",
            "td_chain": "/td/{td_area}/{headcode}/chain",
            "event": "POST /event",
            "websocket": "/ws"
        }
    }


@app.get("/layout/{layout_id}")
async def get_layout(layout_id: str):
    """Get layout by ID."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM layout WHERE id = ?",
            (layout_id,)
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Layout not found")
        
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "data": _json_or_empty(row["data"]),
            "created_at": row["created_at"]
        }


@app.get("/berths/{layout_id}")
async def get_berths(layout_id: str):
    """Get all berths for a layout."""
    with get_conn() as conn:
        # First check if layout exists
        layout = conn.execute(
            "SELECT id FROM layout WHERE id = ?",
            (layout_id,)
        ).fetchone()
        
        if not layout:
            raise HTTPException(status_code=404, detail="Layout not found")
        
        rows = conn.execute(
            "SELECT * FROM berth WHERE layout_id = ? ORDER BY name",
            (layout_id,)
        ).fetchall()
        
        return [
            {
                "id": row["id"],
                "layout_id": row["layout_id"],
                "name": row["name"],
                "x": row["x"],
                "y": row["y"],
                "width": row["width"],
                "height": row["height"],
                "berth_type": row["berth_type"]
            }
            for row in rows
        ]


@app.get("/signals/{layout_id}")
async def get_signals(layout_id: str):
    """Get all signals for a layout."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signal WHERE layout_id = ? ORDER BY name",
            (layout_id,)
        ).fetchall()
        
        return [
            {
                "id": row["id"],
                "layout_id": row["layout_id"],
                "name": row["name"],
                "x": row["x"],
                "y": row["y"],
                "signal_type": row["signal_type"]
            }
            for row in rows
        ]


@app.get("/trains")
async def get_trains():
    """Get all trains."""
    with get_conn() as conn:
        try:
            trains: list[dict[str, Any]] = []

            if _table_exists(conn, "td_state"):
                rows = conn.execute(
                    """
                    SELECT td_area, headcode, last_time_iso, from_berth, to_berth, location_name, platform
                    FROM td_state
                    ORDER BY last_time_ms DESC, td_area, headcode
                    """
                ).fetchall()
                for row in rows:
                    if not row["headcode"]:
                        continue
                    trains.append(
                        {
                            "id": f"{row['td_area']}:{row['headcode']}",
                            "headcode": row["headcode"],
                            "description": row["location_name"] or row["to_berth"] or row["from_berth"],
                            "toc": row["platform"],
                            "created_at": row["last_time_iso"],
                            "td_area": row["td_area"],
                            "current_berth": row["to_berth"] or row["from_berth"],
                            "source": "td_state",
                            "duplicate_key": row["headcode"],
                        }
                    )

            if _table_exists(conn, "train"):
                rows = conn.execute(
                    "SELECT * FROM train ORDER BY created_at DESC"
                ).fetchall()
                trains.extend(
                    [
                        {
                            "id": row["id"],
                            "headcode": row["headcode"],
                            "description": row["description"],
                            "toc": row["toc"],
                            "created_at": row["created_at"],
                            "source": "train",
                            "duplicate_key": row["headcode"] or None,
                        }
                        for row in rows
                    ]
                )

            return trains
        except DB_ERRORS as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/train/{train_id}/chain")
async def get_train_chain(train_id: str, td_area: str | None = None, headcode: str | None = None):
    """Build train journey chain from events.
    
    Returns a list of berth occupancy periods ordered by time.
    Each chain item contains direct observations plus inference metadata.
    """
    with get_conn() as conn:
        try:
            result = infer_train_chain(conn, train_id)
            if result.get("chain"):
                return result
        except DB_ERRORS:
            result = None

        td_headcode = headcode or train_id
        if td_area:
            return _fetch_td_chain(conn, td_area=td_area, headcode=td_headcode)

        row = conn.execute(
            "SELECT td_area FROM td_state WHERE headcode = ? ORDER BY last_time_ms DESC LIMIT 1",
            (td_headcode,),
        ).fetchone() if _table_exists(conn, "td_state") else None
        if row:
            return _fetch_td_chain(conn, td_area=row["td_area"], headcode=td_headcode)

        if result is not None:
            return result
        return {"train_id": train_id, "chain": []}


@app.get("/state")
async def get_state(td_area: str | None = None, headcode: str | None = None, area: str | None = None, hc: str | None = None):
    """Get the current schematic snapshot from TD or PoC tables."""
    td_area = td_area or area
    headcode = headcode or hc
    with get_conn() as conn:
        try:
            return _fetch_td_snapshot(conn, td_area=td_area, headcode=headcode)
        except DB_ERRORS as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/td/{td_area}/{headcode}/chain")
async def get_td_chain(td_area: str, headcode: str):
    """Build a TD-backed berth chain for a headcode within one TD area."""
    with get_conn() as conn:
        try:
            return _fetch_td_chain(conn, td_area=td_area, headcode=headcode)
        except DB_ERRORS as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.post("/event")
async def create_event(event: EventCreate):
    """Create a new event and broadcast to WebSocket clients."""
    try:
        with get_conn() as conn:
            event_id = conn.execute_returning_id(
                """
                INSERT INTO event (ts, source, train_id, event_type, object_id, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.ts,
                    event.source,
                    event.train_id,
                    event.event_type,
                    event.object_id,
                    json.dumps(event.payload)
                )
            )
            conn.commit()
        
        # Broadcast to WebSocket clients
        broadcast_data = {
            "type": "event",
            "data": {
                "id": event_id,
                "ts": event.ts,
                "source": event.source,
                "train_id": event.train_id,
                "event_type": event.event_type,
                "object_id": event.object_id,
                "payload": event.payload
            }
        }
        await manager.broadcast(broadcast_data)
        
        return {"id": event_id, "status": "created"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time event updates."""
    await manager.connect(websocket)
    try:
        # Keep connection alive and handle ping/pong
        while True:
            # Wait for any message from client (ping/pong)
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0
                )
                # Echo back as keepalive
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_json({"type": "keepalive"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# Mount static files last to avoid conflicts
if STATIC_PATH.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


def _parse_args(argv: Optional[list[str]] = None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.visualisation.app",
        description="NROD RailHub visualisation API",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="shared nrod.yaml used for database settings, host/port and cards "
        f"(must exist; default: ${card_store.CONFIG_ENV_VAR}, else nrod.yaml in the project root)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    import uvicorn

    args = _parse_args(argv)
    if args.config:
        if not Path(args.config).expanduser().is_file():
            raise SystemExit(f"Config file not found: {args.config}")
        card_store.set_config_path(args.config)
    try:
        settings = card_store.visualisation_settings()
    except card_store.CardConfigError as exc:
        raise SystemExit(f"Invalid visualisation configuration: {exc}")
    uvicorn.run(app, host=settings["host"], port=settings["port"])


if __name__ == "__main__":
    main()
