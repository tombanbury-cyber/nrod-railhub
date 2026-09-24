"""FastAPI backend for schematic visualization PoC.

This module provides a minimal REST API and WebSocket server for real-time
train visualization on schematic layouts.
"""

import asyncio
import html
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, constr

from .route_inference import infer_train_chain


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


@contextmanager
def get_conn():
    """Get database connection with row factory."""
    db_path = Path(os.environ.get("NROD_RAILHUB_DB", str(DB_PATH)))
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _require_visualisation_schema(conn: sqlite3.Connection) -> None:
    missing = [name for name in ("layout", "berth", "signal") if not _table_exists(conn, name)]
    if missing:
        raise HTTPException(status_code=503, detail=f"missing visualisation tables: {', '.join(missing)}")


def _json_or_empty(value: Optional[str]) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _model_dump(model: BaseModel) -> dict[str, Any]:
    """Return a plain dict across Pydantic v1/v2."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _require_layout_exists(conn: sqlite3.Connection, layout_id: str) -> None:
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


def _render_admin_page(layout_rows: list[sqlite3.Row], berth_rows: list[sqlite3.Row], signal_rows: list[sqlite3.Row]) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    def json_text(value: Any) -> str:
        return esc(json.dumps(value if value is not None else {}, indent=2, sort_keys=True))

    def layout_options(selected: Optional[str] = None) -> str:
        return "".join(
            f"<option value='{esc(row['id'])}'{' selected' if row['id'] == selected else ''}>{esc(row['id'])} — {esc(row['name'])}</option>"
            for row in layout_rows
        )

    def layout_rows_html() -> str:
        rows = []
        for idx, row in enumerate(layout_rows):
            row_key = f"layout-row-{idx}"
            rows.append(
                """
                <tr>
                  <td><input id="{row_key}-id" value="{id}" /></td>
                  <td><input id="{row_key}-name" value="{name}" /></td>
                  <td><input id="{row_key}-description" value="{description}" /></td>
                  <td><textarea id="{row_key}-data" rows="3">{data}</textarea></td>
                  <td class="actions">
                    <input id="{row_key}-original" type="hidden" value="{id}" />
                    <button type="button" onclick="saveLayout('{row_key}')">Save</button>
                    <button type="button" onclick="deleteLayout('{row_key}')">Delete</button>
                  </td>
                </tr>
                """.format(
                    row_key=row_key,
                    id=esc(row["id"]),
                    name=esc(row["name"]),
                    description=esc(row["description"]),
                    data=json_text(_json_or_empty(row["data"])),
                )
            )
        return "".join(rows)

    def berth_rows_html() -> str:
        rows = []
        for idx, row in enumerate(berth_rows):
            row_key = f"berth-row-{idx}"
            rows.append(
                """
                <tr>
                  <td><input id="{row_key}-id" value="{id}" /></td>
                  <td><select id="{row_key}-layout_id">{layout_opts}</select></td>
                  <td><input id="{row_key}-name" value="{name}" /></td>
                  <td><input id="{row_key}-x" type="number" value="{x}" /></td>
                  <td><input id="{row_key}-y" type="number" value="{y}" /></td>
                  <td><input id="{row_key}-width" type="number" value="{width}" /></td>
                  <td><input id="{row_key}-height" type="number" value="{height}" /></td>
                  <td><input id="{row_key}-berth_type" value="{berth_type}" /></td>
                  <td class="actions">
                    <input id="{row_key}-original" type="hidden" value="{id}" />
                    <button type="button" onclick="saveBerth('{row_key}')">Save</button>
                    <button type="button" onclick="deleteBerth('{row_key}')">Delete</button>
                  </td>
                </tr>
                """.format(
                    row_key=row_key,
                    id=esc(row["id"]),
                    layout_opts=layout_options(row["layout_id"]),
                    name=esc(row["name"]),
                    x=esc(row["x"]),
                    y=esc(row["y"]),
                    width=esc(row["width"]),
                    height=esc(row["height"]),
                    berth_type=esc(row["berth_type"]),
                )
            )
        return "".join(rows)

    def signal_rows_html() -> str:
        rows = []
        for idx, row in enumerate(signal_rows):
            row_key = f"signal-row-{idx}"
            rows.append(
                """
                <tr>
                  <td><input id="{row_key}-id" value="{id}" /></td>
                  <td><select id="{row_key}-layout_id">{layout_opts}</select></td>
                  <td><input id="{row_key}-name" value="{name}" /></td>
                  <td><input id="{row_key}-x" type="number" value="{x}" /></td>
                  <td><input id="{row_key}-y" type="number" value="{y}" /></td>
                  <td><input id="{row_key}-signal_type" value="{signal_type}" /></td>
                  <td class="actions">
                    <input id="{row_key}-original" type="hidden" value="{id}" />
                    <button type="button" onclick="saveSignal('{row_key}')">Save</button>
                    <button type="button" onclick="deleteSignal('{row_key}')">Delete</button>
                  </td>
                </tr>
                """.format(
                    row_key=row_key,
                    id=esc(row["id"]),
                    layout_opts=layout_options(row["layout_id"]),
                    name=esc(row["name"]),
                    x=esc(row["x"]),
                    y=esc(row["y"]),
                    signal_type=esc(row["signal_type"]),
                )
            )
        return "".join(rows)

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Visualisation admin</title>
  <style>
    body{{font-family:system-ui,Arial,sans-serif;margin:0;background:#f6f8fb;color:#1f2937}}
    main{{max-width:1400px;margin:0 auto;padding:24px}}
    h1,h2,h3{{margin:0 0 12px}}
    section{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:0 0 20px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}}
    label{{display:flex;flex-direction:column;gap:4px;font-size:14px}}
    input,textarea,select,button{{font:inherit;padding:8px;border:1px solid #cbd5e1;border-radius:8px}}
    textarea{{min-height:92px}}
    button{{cursor:pointer;background:#2563eb;color:#fff;border-color:#2563eb}}
    button.delete{{background:#dc2626;border-color:#dc2626}}
    button.secondary{{background:#fff;color:#1f2937}}
    table{{width:100%;border-collapse:collapse;margin-top:12px}}
    th,td{{border-bottom:1px solid #e5e7eb;padding:8px;vertical-align:top;text-align:left}}
    th{{background:#f8fafc}}
    td.actions{{white-space:nowrap}}
    .hint{{color:#6b7280;font-size:13px;margin-top:4px}}
    .section-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;flex-wrap:wrap}}
    .layout-editor, .berth-editor, .signal-editor{{margin-top:14px}}
    .search-grid{{margin-top:14px}}
    .search-results, .chain-view{{margin-top:14px}}
    .toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}
    .status-message{{margin-top:10px;font-size:14px;color:#2563eb}}
    .status-message.error{{color:#dc2626}}
  </style>
</head>
<body>
<main>
  <h1>Visualisation admin</h1>
  <p class="hint">Edit layout, berth, and signal rows in the SQLite database.</p>
  <section>
    <div class="section-head">
      <div>
        <h2>Headcode chain import</h2>
        <div class="hint">Search headcodes, inspect the berth chain, then choose exactly which berths to import into a layout.</div>
      </div>
      <button type="button" class="secondary" onclick="loadHeadcodes()">Refresh headcodes</button>
    </div>
    <div class="search-grid grid">
      <label>Search headcodes
        <input id="headcode-search" placeholder="2C90, EK, BRTH_1…" oninput="renderHeadcodeResults()" />
      </label>
      <label>Import into layout
        <select id="chain-import-layout_id">{layout_options()}</select>
      </label>
    </div>
    <div class="search-results">
      <h3>Matching headcodes</h3>
      <table>
        <thead><tr><th>Headcode</th><th>TD area</th><th>Train ID</th><th>Description</th><th>Current berth</th><th>Action</th></tr></thead>
        <tbody id="headcode-results"><tr><td colspan="6">Loading headcodes…</td></tr></tbody>
      </table>
    </div>
    <div class="chain-view">
      <div class="section-head">
        <div>
          <h3>Selected berth chain</h3>
          <div class="hint" id="chain-summary">Select a headcode to view its berth chain.</div>
        </div>
      </div>
      <div class="toolbar">
        <button type="button" class="secondary" onclick="setAllChainSelections(true)">Select all</button>
        <button type="button" class="secondary" onclick="setAllChainSelections(false)">Clear all</button>
        <button type="button" onclick="importSelectedChainBerths()">Import selected berths</button>
      </div>
      <table>
        <thead><tr><th>Import</th><th>Berth</th><th>Entered</th><th>Source</th><th>Reason</th></tr></thead>
        <tbody id="chain-results"><tr><td colspan="5">No berth chain loaded.</td></tr></tbody>
      </table>
      <div id="chain-import-status" class="status-message"></div>
    </div>
  </section>

  <section>
    <div class="section-head">
      <div>
        <h2>Layout editor</h2>
        <div class="hint">Create a layout, then edit its ID/name/description/JSON metadata inline.</div>
      </div>
    </div>
    <div class="layout-editor grid">
      <label>ID <input id="new-layout-id" /></label>
      <label>Name <input id="new-layout-name" /></label>
      <label>Description <input id="new-layout-description" /></label>
      <label>Data <textarea id="new-layout-data">{{}}</textarea></label>
    </div>
    <p><button type="button" onclick="createLayout()">Create layout</button></p>
    <table>
      <thead><tr><th>ID</th><th>Name</th><th>Description</th><th>Data</th><th>Actions</th></tr></thead>
      <tbody>{layout_rows_html()}</tbody>
    </table>
  </section>

  <section>
    <div class="section-head">
      <div>
        <h2>Berth editor</h2>
        <div class="hint">Berths belong to a layout and can be moved or renamed inline.</div>
      </div>
    </div>
    <div class="berth-editor grid">
      <label>ID <input id="new-berth-id" /></label>
      <label>Layout <select id="new-berth-layout_id">{layout_options()}</select></label>
      <label>Name <input id="new-berth-name" /></label>
      <label>X <input id="new-berth-x" type="number" value="0" /></label>
      <label>Y <input id="new-berth-y" type="number" value="0" /></label>
      <label>Width <input id="new-berth-width" type="number" value="{DEFAULT_BERTH_WIDTH}" /></label>
      <label>Height <input id="new-berth-height" type="number" value="{DEFAULT_BERTH_HEIGHT}" /></label>
      <label>Type <input id="new-berth-berth_type" value="normal" /></label>
    </div>
    <p><button type="button" onclick="createBerth()">Create berth</button></p>
    <table>
      <thead><tr><th>ID</th><th>Layout</th><th>Name</th><th>X</th><th>Y</th><th>Width</th><th>Height</th><th>Type</th><th>Actions</th></tr></thead>
      <tbody>{berth_rows_html()}</tbody>
    </table>
  </section>

  <section>
    <div class="section-head">
      <div>
        <h2>Signal editor</h2>
        <div class="hint">Signals can be created, moved, renamed, or deleted from the browser.</div>
      </div>
    </div>
    <div class="signal-editor grid">
      <label>ID <input id="new-signal-id" /></label>
      <label>Layout <select id="new-signal-layout_id">{layout_options()}</select></label>
      <label>Name <input id="new-signal-name" /></label>
      <label>X <input id="new-signal-x" type="number" value="0" /></label>
      <label>Y <input id="new-signal-y" type="number" value="0" /></label>
      <label>Type <input id="new-signal-signal_type" value="auto" /></label>
    </div>
    <p><button type="button" onclick="createSignal()">Create signal</button></p>
    <table>
      <thead><tr><th>ID</th><th>Layout</th><th>Name</th><th>X</th><th>Y</th><th>Type</th><th>Actions</th></tr></thead>
      <tbody>{signal_rows_html()}</tbody>
    </table>
  </section>
</main>
<script>
let headcodeRows = [];
let currentChain = [];

function rowValue(rowKey, field) {{
  const el = document.getElementById(`${{rowKey}}-${{field}}`);
  return el ? el.value.trim() : "";
}}

function rowNumber(rowKey, field) {{
  return Number(rowValue(rowKey, field));
}}

async function submitJson(url, method, payload) {{
  const response = await fetch(url, {{
    method,
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify(payload),
  }});
  const text = await response.text();
  if (!response.ok) {{
    throw new Error(text || response.statusText);
  }}
  return text ? JSON.parse(text) : {{}};
}}

function parseLayoutData(inputId) {{
  const raw = document.getElementById(inputId).value.trim() || "{{}}";
  return JSON.parse(raw);
}}

function setChainStatus(message, isError = false) {{
  const status = document.getElementById("chain-import-status");
  status.textContent = message;
  status.className = isError ? "status-message error" : "status-message";
}}

function createTextCell(text) {{
  const cell = document.createElement("td");
  cell.textContent = text || "";
  return cell;
}}

function trainChainUrl(train) {{
  if (train.td_area && train.headcode) {{
    return `/td/${{encodeURIComponent(train.td_area)}}/${{encodeURIComponent(train.headcode)}}/chain`;
  }}
  return `/train/${{encodeURIComponent(train.id)}}/chain`;
}}

function renderHeadcodeResults() {{
  const tbody = document.getElementById("headcode-results");
  const query = document.getElementById("headcode-search").value.trim().toLowerCase();
  tbody.innerHTML = "";
  const liveDuplicateKeys = new Set(
    headcodeRows
      .filter(train => train.source === "td_state" && train.duplicate_key)
      .map(train => train.duplicate_key)
  );
  const filtered = headcodeRows.filter(train => {{
    if (train.source === "train" && train.duplicate_key && liveDuplicateKeys.has(train.duplicate_key)) {{
      return false;
    }}
    return [train.headcode, train.td_area, train.id, train.description, train.current_berth]
      .some(value => String(value || "").toLowerCase().includes(query));
  }});
  if (!filtered.length) {{
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "No headcodes matched the search.";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }}
  filtered.forEach(train => {{
    const row = document.createElement("tr");
    row.appendChild(createTextCell(train.headcode || ""));
    row.appendChild(createTextCell(train.td_area || ""));
    row.appendChild(createTextCell(train.id || ""));
    row.appendChild(createTextCell(train.description || ""));
    row.appendChild(createTextCell(train.current_berth || ""));
    const actionCell = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = "View chain";
    button.addEventListener("click", () => loadTrainChain(train));
    actionCell.appendChild(button);
    row.appendChild(actionCell);
    tbody.appendChild(row);
  }});
}}

function renderChainRows(chainData, train) {{
  currentChain = Array.isArray(chainData.chain) ? chainData.chain : [];
  const tbody = document.getElementById("chain-results");
  const summary = document.getElementById("chain-summary");
  tbody.innerHTML = "";
  const label = [train.headcode || chainData.headcode || train.id, train.td_area || chainData.td_area]
    .filter(Boolean)
    .join(" • ");
  summary.textContent = label ? `Chain for ${{label}}` : "Selected berth chain";
  if (!currentChain.length) {{
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "No berth chain is available for the selected headcode.";
    row.appendChild(cell);
    tbody.appendChild(row);
    return;
  }}
  currentChain.forEach((item, index) => {{
    const row = document.createElement("tr");
    const checkboxCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(item.berth_id);
    checkbox.className = "chain-import-checkbox";
    checkbox.dataset.berthId = item.berth_id || "";
    checkbox.dataset.chainIndex = String(index);
    checkbox.setAttribute("aria-label", `Import berth ${{item.berth_id || index + 1}}`);
    checkboxCell.appendChild(checkbox);
    row.appendChild(checkboxCell);
    row.appendChild(createTextCell(item.berth_id || ""));
    row.appendChild(createTextCell(item.enter_time || item.last_time_iso || ""));
    row.appendChild(createTextCell(item.source || ""));
    row.appendChild(createTextCell(item.reason || ""));
    tbody.appendChild(row);
  }});
}}

async function loadHeadcodes() {{
  setChainStatus("");
  try {{
    const response = await fetch("/trains");
    if (!response.ok) {{
      throw new Error(`HTTP ${{response.status}}`);
    }}
    headcodeRows = await response.json();
    renderHeadcodeResults();
  }} catch (error) {{
    console.error(error);
    const tbody = document.getElementById("headcode-results");
    tbody.innerHTML = '<tr><td colspan="6">Failed to load headcodes.</td></tr>';
  }}
}}

async function loadTrainChain(train) {{
  setChainStatus("Loading berth chain…");
  try {{
    const response = await fetch(trainChainUrl(train));
    if (!response.ok) {{
      throw new Error(`HTTP ${{response.status}}`);
    }}
    const chainData = await response.json();
    renderChainRows(chainData, train);
    setChainStatus(`Loaded ${{currentChain.length}} berth item(s).`);
  }} catch (error) {{
    console.error(error);
    setChainStatus("Failed to load the berth chain.", true);
  }}
}}

function setAllChainSelections(selected) {{
  document.querySelectorAll(".chain-import-checkbox").forEach((checkbox) => {{
    checkbox.checked = selected;
  }});
}}

async function importSelectedChainBerths() {{
  const layoutId = document.getElementById("chain-import-layout_id").value;
  const berthIds = Array.from(document.querySelectorAll(".chain-import-checkbox"))
    .filter((checkbox) => checkbox.checked && checkbox.dataset.berthId)
    .map((checkbox) => checkbox.dataset.berthId);
  if (!layoutId) {{
    setChainStatus("Choose a layout before importing berths.", true);
    return;
  }}
  if (!berthIds.length) {{
    setChainStatus("Select at least one berth to import.", true);
    return;
  }}
  try {{
    const result = await submitJson("/api/berths/import-chain", "POST", {{
      layout_id: layoutId,
      berth_ids: berthIds,
    }});
    const importedCount = Array.isArray(result.imported) ? result.imported.length : 0;
    const skippedCount = Array.isArray(result.skipped_existing) ? result.skipped_existing.length : 0;
    setChainStatus(`Imported ${{importedCount}} berth(s) into ${{layoutId}}${{skippedCount ? `; skipped ${{skippedCount}} existing.` : "."}} Refresh to see them in the berth editor.`);
  }} catch (error) {{
    console.error(error);
    setChainStatus("Failed to import selected berths.", true);
  }}
}}

async function createLayout() {{
  await submitJson("/api/layouts", "POST", {{
    id: rowValue("new-layout", "id"),
    name: rowValue("new-layout", "name"),
    description: rowValue("new-layout", "description") || null,
    data: parseLayoutData("new-layout-data"),
  }});
  window.location.reload();
}}

async function saveLayout(rowKey) {{
  const originalId = rowValue(rowKey, "original");
  await submitJson(`/api/layouts/${{encodeURIComponent(originalId)}}`, "PUT", {{
    id: rowValue(rowKey, "id"),
    name: rowValue(rowKey, "name"),
    description: rowValue(rowKey, "description") || null,
    data: parseLayoutData(`${{rowKey}}-data`),
  }});
  window.location.reload();
}}

async function deleteLayout(rowKey) {{
  const originalId = rowValue(rowKey, "original");
  if (!confirm(`Delete layout ${{originalId}}?`)) return;
  await submitJson(`/api/layouts/${{encodeURIComponent(originalId)}}`, "DELETE", {{}});
  window.location.reload();
}}

async function createBerth() {{
  await submitJson("/api/berths", "POST", {{
    id: rowValue("new-berth", "id"),
    layout_id: rowValue("new-berth", "layout_id"),
    name: rowValue("new-berth", "name"),
    x: rowNumber("new-berth", "x"),
    y: rowNumber("new-berth", "y"),
    width: rowNumber("new-berth", "width"),
    height: rowNumber("new-berth", "height"),
    berth_type: rowValue("new-berth", "berth_type"),
  }});
  window.location.reload();
}}

async function saveBerth(rowKey) {{
  const originalId = rowValue(rowKey, "original");
  await submitJson(`/api/berths/${{encodeURIComponent(originalId)}}`, "PUT", {{
    id: rowValue(rowKey, "id"),
    layout_id: rowValue(rowKey, "layout_id"),
    name: rowValue(rowKey, "name"),
    x: rowNumber(rowKey, "x"),
    y: rowNumber(rowKey, "y"),
    width: rowNumber(rowKey, "width"),
    height: rowNumber(rowKey, "height"),
    berth_type: rowValue(rowKey, "berth_type"),
  }});
  window.location.reload();
}}

async function deleteBerth(rowKey) {{
  const originalId = rowValue(rowKey, "original");
  if (!confirm(`Delete berth ${{originalId}}?`)) return;
  await submitJson(`/api/berths/${{encodeURIComponent(originalId)}}`, "DELETE", {{}});
  window.location.reload();
}}

async function createSignal() {{
  await submitJson("/api/signals", "POST", {{
    id: rowValue("new-signal", "id"),
    layout_id: rowValue("new-signal", "layout_id"),
    name: rowValue("new-signal", "name"),
    x: rowNumber("new-signal", "x"),
    y: rowNumber("new-signal", "y"),
    signal_type: rowValue("new-signal", "signal_type"),
  }});
  window.location.reload();
}}

async function saveSignal(rowKey) {{
  const originalId = rowValue(rowKey, "original");
  await submitJson(`/api/signals/${{encodeURIComponent(originalId)}}`, "PUT", {{
    id: rowValue(rowKey, "id"),
    layout_id: rowValue(rowKey, "layout_id"),
    name: rowValue(rowKey, "name"),
    x: rowNumber(rowKey, "x"),
    y: rowNumber(rowKey, "y"),
    signal_type: rowValue(rowKey, "signal_type"),
  }});
  window.location.reload();
}}

async function deleteSignal(rowKey) {{
  const originalId = rowValue(rowKey, "original");
  if (!confirm(`Delete signal ${{originalId}}?`)) return;
  await submitJson(`/api/signals/${{encodeURIComponent(originalId)}}`, "DELETE", {{}});
  window.location.reload();
}}

loadHeadcodes();
</script>
</body>
</html>
"""


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _format_event_row(row: sqlite3.Row, source: str) -> dict[str, Any]:
    data = _row_dict(row)
    data["source"] = source
    data["berth_id"] = data.get("to_berth") or data.get("from_berth") or data.get("object_id")
    data["train_ref"] = data.get("headcode") or data.get("train_id")
    data["display_ts"] = data.get("ts_iso") or data.get("ts")
    return data


def _fetch_td_snapshot(
    conn: sqlite3.Connection,
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
                    latest_by_key: dict[tuple[str | None, str | None], sqlite3.Row] = {}
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
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


def _fetch_td_chain(
    conn: sqlite3.Connection,
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
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/admin", response_class=HTMLResponse)
async def admin():
    """Render a lightweight browser CRUD UI for layout, berth, and signal tables."""
    with get_conn() as conn:
        _require_visualisation_schema(conn)
        layout_rows = conn.execute("SELECT * FROM layout ORDER BY id").fetchall()
        berth_rows = conn.execute("SELECT * FROM berth ORDER BY layout_id, name").fetchall()
        signal_rows = conn.execute("SELECT * FROM signal ORDER BY layout_id, name").fetchall()
        return _render_admin_page(layout_rows, berth_rows, signal_rows)


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
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "created", "rowid": cursor.lastrowid, "layout": _model_dump(payload)}


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
        except sqlite3.IntegrityError as exc:
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
        except sqlite3.IntegrityError as exc:
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
        except sqlite3.IntegrityError as exc:
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
        except sqlite3.IntegrityError as exc:
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
        except sqlite3.IntegrityError as exc:
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
            "data": json.loads(row["data"]) if row["data"] else {},
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
        except sqlite3.OperationalError as exc:
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
        except sqlite3.OperationalError:
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
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/td/{td_area}/{headcode}/chain")
async def get_td_chain(td_area: str, headcode: str):
    """Build a TD-backed berth chain for a headcode within one TD area."""
    with get_conn() as conn:
        try:
            return _fetch_td_chain(conn, td_area=td_area, headcode=headcode)
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.post("/event")
async def create_event(event: EventCreate):
    """Create a new event and broadcast to WebSocket clients."""
    try:
        with get_conn() as conn:
            cursor = conn.execute(
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
            event_id = cursor.lastrowid
        
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
