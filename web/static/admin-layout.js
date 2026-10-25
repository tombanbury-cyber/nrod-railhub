const body = document.body;
let layoutId = body.dataset.layoutId || "";
const isNew = body.dataset.mode === "new";

const TABLES = {
  berth: {
    api: "/api/berths", status: "berth-status", body: "berth-rows", cols: 9,
    fields: [["id", "text"], ["name", "text"], ["x", "number"], ["y", "number"], ["width", "number"], ["height", "number"], ["berth_type", ["normal", "platform", "siding"]]],
  },
  signal: {
    api: "/api/signals", status: "signal-status", body: "signal-rows", cols: 7,
    fields: [["id", "text"], ["name", "text"], ["x", "number"], ["y", "number"], ["signal_type", ["auto", "controlled", "shunt"]]],
  },
};
const BERTH_LINKED_KINDS = ["level_crossing", "platform", "label"];
const OBJECT_API = "/api/layout-objects";
const OBJECT_KINDS = {
  level_crossing: { title: "Level crossing", variants: ["automatic", "barrier", "user_worked", "foot-crossing"], width: 40, height: 40 },
  bridge: { title: "Bridge", variants: ["road_over", "road_under", "footbridge"], width: 80, height: 24 },
  label: { title: "Label", variants: ["station", "line", "note", "text"], width: 60, height: 20 },
  platform: { title: "Platform", variants: ["side", "island", "bay"], width: 120, height: 24 },
};
const ADD_KINDS = ["berth", "signal", "level_crossing", "bridge", "label", "platform"];
const CROSSING_ROTATIONS = [0, 45, 90];
const BRIDGE_ROTATIONS = [0, 45, 90];
const SIGNAL_ROTATIONS = [0, 45, 90, 135, 180, 225, 270, 315];
const CROSSING_PAD = 4;
const OBJECT_TABLE_FIELDS = [["id", "text"], ["name", "text"], ["x", "number"], ["y", "number"], ["width", "number"], ["height", "number"]];
function objectTable(kind, extraFields) {
  return {
    api: OBJECT_API, status: `${kind}-status`, body: `${kind}-rows`, objectType: kind,
    fields: [...OBJECT_TABLE_FIELDS, ["variant", OBJECT_KINDS[kind].variants], ...extraFields],
  };
}
Object.assign(TABLES, {
  level_crossing: objectTable("level_crossing", [["berth_id", "text"], ["rotation", ["0", "45", "90"]]]),
  platform: objectTable("platform", [["berth_id", "text"]]),
  bridge: objectTable("bridge", [["rotation", ["0", "45", "90"]]]),
  label: objectTable("label", [["berth_id", "text"]]),
});
const tableState = Object.fromEntries(Object.keys(TABLES).map((kind) => [kind, { page: 1 }]));
const editorSvg = document.getElementById("layout-canvas-svg");
const SNAP_STORAGE_KEY = "nrod-railhub-layout-snap-to-grid";
const snapToGridToggle = document.getElementById("snap-to-grid");
try {
  const savedSnapState = window.localStorage.getItem(SNAP_STORAGE_KEY);
  if (savedSnapState === "true" || savedSnapState === "false") {
    snapToGridToggle.checked = savedSnapState === "true";
  }
} catch { }
snapToGridToggle.addEventListener("change", () => {
  try {
    window.localStorage.setItem(SNAP_STORAGE_KEY, String(snapToGridToggle.checked));
  } catch { }
});
const editorItems = [];
const trackItems = [];
const junctionItems = [];
const TRACK_API = "/api/tracks";
const JUNCTION_API = "/api/junctions";
const SOCKET_SIDES = ["left", "right"];
const SOCKET_SNAP_RADIUS = 14;
const Routing = window.TrackRouting;
let pendingConnection = null;
let connectionDrag = null;
let selectedElement = null;
let dragOffset = null;
let canvasSaving = false;
const BASE_CANVAS_WIDTH = 1200;
const BASE_CANVAS_HEIGHT = 550;
const CANVAS_PADDING = 0;
let CANVAS_WIDTH = BASE_CANVAS_WIDTH;
let CANVAS_HEIGHT = BASE_CANVAS_HEIGHT;
function setupPanZoom(svg, options) {
  const MIN_SCALE = 0.2;
  const MAX_SCALE = 6;
  const pointers = new Map();
  let view = null;
  let panning = null;
  let pinch = null;
  function base() { return options.getBase(); }
  function clampView() {
    const b = options.getBounds();
    const margin = 0.2;
    view.x = Math.max(b.x - view.w * (1 - margin), Math.min(b.x + b.w - view.w * margin, view.x));
    view.y = Math.max(b.y - view.h * (1 - margin), Math.min(b.y + b.h - view.h * margin, view.y));
  }
  function apply() {
    clampView();
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
    if (options.onChange) options.onChange(base().w / view.w);
  }
  function reset() {
    const b = base();
    view = { x: 0, y: 0, w: b.w, h: b.h };
    apply();
  }
  function fit() {
    const b = base();
    const c = options.getBounds();
    const pad = 20;
    const scale = Math.min(b.w / (c.w + 2 * pad), b.h / (c.h + 2 * pad), MAX_SCALE);
    view = { w: b.w / scale, h: b.h / scale, x: 0, y: 0 };
    view.x = c.x - pad - (view.w - c.w - 2 * pad) / 2;
    view.y = c.y - pad - (view.h - c.h - 2 * pad) / 2;
    apply();
  }
  function zoomAt(factor, cx, cy) {
    if (!view) reset();
    const b = base();
    const current = b.w / view.w;
    const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, current * factor));
    const ratio = current / next;
    const nw = b.w / next;
    const nh = b.h / next;
    view = { x: cx - (cx - view.x) * ratio, y: cy - (cy - view.y) * ratio, w: nw, h: nh };
    apply();
  }
  function toWorld(clientX, clientY) {
    const p = svg.createSVGPoint();
    p.x = clientX;
    p.y = clientY;
    return p.matrixTransform(svg.getScreenCTM().inverse());
  }
  function zoomBy(factor) {
    if (!view) reset();
    zoomAt(factor, view.x + view.w / 2, view.y + view.h / 2);
  }
  function worldPerPixel() {
    const ctm = svg.getScreenCTM();
    return ctm && ctm.a ? 1 / ctm.a : 1;
  }
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (!view) reset();
    if (event.shiftKey && !event.ctrlKey && !event.metaKey) {
      const k = worldPerPixel();
      const dx = event.deltaX || event.deltaY;
      view.x += dx * k;
      apply();
    } if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      const p = toWorld(event.clientX, event.clientY);
      zoomAt(Math.exp(-event.deltaY * 0.01), p.x, p.y);
    }
  }, { passive: false });
  svg.addEventListener("pointerdown", (event) => {
    if (!options.isBackground(event.target)) return;
    if (event.pointerType === "mouse" && event.button !== 0 && event.button !== 1) return;
    if (!view) reset();
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 1) {
      panning = { id: event.pointerId, x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, moved: false };
    } else if (pointers.size === 2) {
      panning = null;
      const [a, b] = [...pointers.values()];
      pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
    }
  });
  svg.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const p = toWorld((a.x + b.x) / 2, (a.y + b.y) / 2);
      zoomAt(dist / pinch.dist, p.x, p.y);
      pinch.dist = dist;
    } else if (panning && panning.id === event.pointerId) {
      const dx = event.clientX - panning.x;
      const dy = event.clientY - panning.y;
      if (!panning.moved && Math.hypot(dx, dy) < 3) return;
      if (!panning.moved) {
        panning.moved = true;
        try { svg.setPointerCapture(event.pointerId); } catch (e) { /* ignore */ }
      }
      const k = worldPerPixel();
      view.x = panning.vx - dx * k;
      view.y = panning.vy - dy * k;
      apply();
    }
  });
  function end(event) {
    pointers.delete(event.pointerId);
    if (pointers.size < 2) pinch = null;
    if (panning && panning.id === event.pointerId) panning = null;
  }
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
  return { zoomBy, fit, reset };
}

function itemSize(item) {
  if (item.kind === "bridge") return bridgeGeometry(item);
  if (item.kind === "berth" || item.kind === "platform") {
    return { width: Number(item.width), height: Number(item.height) };
  }
  if (item.kind === "level_crossing") return crossingGeometry(item);
  if (item.kind === "label") return { width: labelWidth(item), height: 20 };
  return { width: 12, height: 30 };
}

function crossingGeometry(item) {
  const length = Math.max(1, Number(item.height) || 1);
  const rotation = Number(item.rotation) || 0;
  if (rotation === 90) {
    return { width: length, height: CROSSING_PAD * 2, x1: 0, y1: CROSSING_PAD, x2: length, y2: CROSSING_PAD };
  }
  if (rotation === 45) {
    const d = Math.round(length * Math.SQRT1_2);
    return { width: d, height: d, x1: 0, y1: 0, x2: d, y2: d };
  }
  return { width: CROSSING_PAD * 2, height: length, x1: CROSSING_PAD, y1: 0, x2: CROSSING_PAD, y2: length };
}

function bridgeGeometry(item) {
  const width = Number(item.width);
  const height = Number(item.height);
  const rotation = Number(item.rotation) || 0;
  if (rotation === 90) return { width: height, height: width, transform: `translate(${height} 0) rotate(90)` };
  if (rotation === 45) {
    const s = Math.SQRT1_2;
    const extent = Math.round((width + height) * s);
    return { width: extent, height: extent, transform: `translate(${height * s} 0) rotate(45)` };
  }
  return { width, height, transform: "" };
}

function labelWidth(item) {
  return Math.max(30, String(item.name || item.id || "New label").length * 8 + 16);
}

function elementApi(item) {
  return item.kind in OBJECT_KINDS ? OBJECT_API : TABLES[item.kind].api;
}

let trackControlsLocked = isNew;
function setAddControlsDisabled(disabled) {
  document.querySelectorAll(".add-control button, .add-control select")
    .forEach((control) => { control.disabled = disabled; });
  trackControlsLocked = disabled;
  updateTrackControls();
}

function updateTrackControls() {
  const button = document.getElementById("add-junction");
  if (button) button.disabled = trackControlsLocked || !selectedElement || selectedElement.kind !== "track";
}

function updateCanvasExtent() {
  CANVAS_WIDTH = BASE_CANVAS_WIDTH;
  CANVAS_HEIGHT = BASE_CANVAS_HEIGHT;
  editorItems.forEach((item) => {
    const { width, height } = itemSize(item);
    CANVAS_WIDTH = Math.max(CANVAS_WIDTH, Number(item.x) + width + CANVAS_PADDING);
    CANVAS_HEIGHT = Math.max(CANVAS_HEIGHT, Number(item.y) + height + CANVAS_PADDING);
  });
  trackItems.forEach((track) => track.points.forEach((p) => {
    CANVAS_WIDTH = Math.max(CANVAS_WIDTH, p.x + CANVAS_PADDING);
    CANVAS_HEIGHT = Math.max(CANVAS_HEIGHT, p.y + CANVAS_PADDING);
  }));
  const grid = editorSvg.querySelector("#canvas-grid-bg");
  if (grid) {
    grid.setAttribute("width", CANVAS_WIDTH);
    grid.setAttribute("height", CANVAS_HEIGHT);
  }
}
const canvasView = setupPanZoom(editorSvg, {
  getBase: () => ({ w: BASE_CANVAS_WIDTH, h: BASE_CANVAS_HEIGHT }),
  getBounds: () => ({ x: 0, y: 0, w: CANVAS_WIDTH, h: CANVAS_HEIGHT }),
  isBackground: (target) => !target.closest(".layout-element, .track-element, .junction-element"),
  onChange: (scale) => {
    const label = document.getElementById("canvas-zoom-level");
    if (label) label.textContent = `${Math.round(scale * 100)}%`;
  },
});
document.getElementById("canvas-zoom-in").addEventListener("click", () => canvasView.zoomBy(1.25));
document.getElementById("canvas-zoom-out").addEventListener("click", () => canvasView.zoomBy(0.8));
document.getElementById("canvas-zoom-fit").addEventListener("click", () => { updateCanvasExtent(); canvasView.fit(); });
document.getElementById("canvas-zoom-reset").addEventListener("click", () => canvasView.reset());

function endpointPayload(endpoint) {
  return endpoint.junction_id ? { junction_id: endpoint.junction_id } : { berth_id: endpoint.berth_id, side: endpoint.side };
}

function editorPayload(item) {
  if (item.kind === "track") {
    return {
      id: item.id, layout_id: layoutId, name: item.name, start: endpointPayload(item.start),
      end: endpointPayload(item.end), points: item.points.map((p) => ({ x: p.x, y: p.y })),
    };
  }
  if (item.kind === "junction") {
    return { id: item.id, layout_id: layoutId, track_id: item.track_id, name: item.name, x: item.x, y: item.y };
  }
  const payload = {
    id: item.id, layout_id: layoutId, name: item.name, x: item.x, y: item.y,
    z_index: Number(item.z_index) || 0,
  };
  if (item.kind === "berth") {
    Object.assign(payload, {
      width: item.width, height: item.height, berth_type: item.berth_type,
    });
  } else if (item.kind in OBJECT_KINDS) {
    Object.assign(payload, {
      object_type: item.kind, width: item.width, height: item.height, variant: item.variant,
      berth_id: BERTH_LINKED_KINDS.includes(item.kind) ? (item.berth_id || null) : null,
      rotation: item.kind === "level_crossing" || item.kind === "bridge" ? Number(item.rotation) || 0 : 0,
    });
  } else {
    payload.signal_type = item.signal_type;
    payload.rotation = Number(item.rotation) || 0;
  }
  return payload;
}

function editorItemIsDirty(item) {
  return item._isNew || JSON.stringify(editorPayload(item)) !== JSON.stringify(item._savedPayload);
}

function editorHasChanges() {
  return editorItems.some(editorItemIsDirty) || trackItems.some(editorItemIsDirty) || junctionItems.some(editorItemIsDirty);
}

function updateEditorStatus(message, isError) {
  const dirty = editorHasChanges();
  setStatus("canvas-status", message || (dirty ? "Unsaved element changes." : "All element changes saved."), isError);
  document.getElementById("save-canvas").disabled = !dirty;
}

function editorPoint(event) {
  const point = editorSvg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(editorSvg.getScreenCTM().inverse());
}

function snapGridSize() {
  const toggle = document.getElementById("snap-to-grid");
  const select = document.getElementById("snap-grid-size");
  if (!toggle || !select || !toggle.checked) return 0;
  const size = Number(select.value);
  return size > 0 ? size : 0;
}

function nextElementIdentity(kind) {
  const titles = {
    berth: "Berth", signal: "Signal", ...Object.fromEntries(
      Object.entries(OBJECT_KINDS).map(([key, value]) => [key, value.title]),
    )
  };
  let number = 1;
  let id;
  do {
    id = `${layoutId}_${kind}_${number}`;
    number += 1;
  } while (editorItems.some((item) => item.id === id));
  return { id, name: `${titles[kind]} ${number - 1}` };
}

function snapValue(value, size) {
  return size > 0 ? Math.round(value / size) * size : value;
}

function clampElementPosition(item, x, y) {
  const grid = snapGridSize();
  x = snapValue(x, grid);
  y = snapValue(y, grid);
  const { width, height } = itemSize(item);
  item.x = Math.round(Math.max(0, Math.min(CANVAS_WIDTH - width, x)));
  item.y = Math.round(Math.max(0, Math.min(CANVAS_HEIGHT - height, y)));
}

function svgNode(name, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderLevelCrossing(group, item) {
  const g = crossingGeometry(item);
  const coords = { x1: g.x1, y1: g.y1, x2: g.x2, y2: g.y2 };
  group.appendChild(svgNode("line", { class: "crossing-hit", ...coords }));
  group.appendChild(svgNode("line", { class: `crossing-line crossing-${item.variant}`, ...coords }));
  group.appendChild(svgNode("text", { x: g.x2 + 8, y: Math.max(g.y1, g.y2) - 2, fill: "black" },
    item.name || item.id || "New level crossing"));
}

function renderPlatform(group, item) {
  const width = Number(item.width);
  const height = Number(item.height);
  group.appendChild(svgNode("rect", { class: `platform-object-shape platform-${item.variant}`, width, height, rx: 2 }));
  group.appendChild(svgNode("text", { x: width / 2, y: height / 2 + 4, "text-anchor": "middle", fill: "black" },
    item.name || item.id || "New platform"));
}

function renderBridge(group, item) {
  const width = Number(item.width);
  const height = Number(item.height);
  const inner = svgNode("g", { class: "bridge-rotation" });
  const { transform } = bridgeGeometry(item);
  if (transform) inner.setAttribute("transform", transform);
  inner.appendChild(svgNode("rect", { class: `bridge-shape bridge-${item.variant}`, width, height, rx: 3 }));
  inner.appendChild(svgNode("line", { class: "bridge-end", x1: 0, y1: 0, x2: 0, y2: height }));
  inner.appendChild(svgNode("line", { class: "bridge-end", x1: width, y1: 0, x2: width, y2: height }));
  inner.appendChild(svgNode("text", { x: width / 2, y: height / 2 + 4, "text-anchor": "middle", fill: "black" },
    item.name || item.id || "New bridge"));
  group.appendChild(inner);
}

function renderLabel(group, item) {
  const shapeClass = item.variant === "text" ? "label-hit" : `label-shape label-${item.variant}`;
  group.appendChild(svgNode("rect", { class: shapeClass, width: labelWidth(item), height: 20, rx: 3 }));
  group.appendChild(svgNode("text", {
    class: `label-text label-text-${item.variant}`, x: 8, y: 14, fill: "black",
  }, item.name || item.id || "New label"));
}

function sortedByLayer(items) {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => (Number(a.item.z_index) || 0) - (Number(b.item.z_index) || 0) || a.index - b.index)
    .map((entry) => entry.item);
}

function changeLayer(item, toFront) {
  const others = editorItems.filter((other) => other !== item).map((other) => Number(other.z_index) || 0);
  if (!others.length) return;
  item.z_index = toFront ? Math.max(...others) + 1 : Math.min(...others) - 1;
  renderCanvas();
  renderInspector();
  updateEditorStatus();
}

const trackLayer = svgNode("g", { id: "track-layer" });
const junctionLayer = svgNode("g", { id: "junction-layer" });
const draftLayer = svgNode("g", { id: "draft-layer", "pointer-events": "none" });
editorSvg.append(trackLayer, junctionLayer, draftLayer);

function routingState() {
  return {
    berths: editorItems.filter((item) => item.kind === "berth"),
    tracks: trackItems,
    junctions: junctionItems,
    obstacles: editorItems.map((item) => ({ x: Number(item.x), y: Number(item.y), ...itemSize(item) })),
  };
}

function syncTracks() {
  return Routing.reconcile(routingState());
}

function nextTrackIdentity(kind) {
  const list = kind === "track" ? trackItems : junctionItems;
  let number = 1;
  let id;
  do {
    id = `${layoutId}_${kind}_${number}`;
    number += 1;
  } while ([...editorItems, ...trackItems, ...junctionItems].some((item) => item.id === id));
  return { id, name: `${kind === "track" ? "Track" : "Junction"} ${list.length + 1}` };
}

function retargetBerthRefs(oldId, newId) {
  trackItems.forEach((track) => [track.start, track.end].forEach((endpoint) => {
    if (endpoint.berth_id === oldId) endpoint.berth_id = newId;
  }));
}

function berthName(id) {
  const berth = editorItems.find((item) => item.kind === "berth" && item.id === id);
  return berth ? (berth.name || berth.id) : id;
}

function endpointLabel(endpoint) {
  if (endpoint.junction_id) {
    const junction = junctionItems.find((item) => item.id === endpoint.junction_id);
    return `junction ${junction ? (junction.name || junction.id) : endpoint.junction_id}`;
  }
  return `berth ${berthName(endpoint.berth_id)} (${endpoint.side})`;
}

function connectionKey(source) {
  return source.kind === "junction" ? `junction:${source.item.id}` : `berth:${source.item.id}:${source.side}`;
}

function connectionPoint(source) {
  return source.kind === "junction" ? { x: source.item.x, y: source.item.y } : Routing.socketPoint(source.item, source.side);
}

function connectionRef(source) {
  return source.kind === "junction"
    ? { junction_id: source.item.id }
    : { berth_id: source.item.id, side: source.side };
}

function connectionLabel(source) {
  return source.kind === "junction"
    ? `junction ${source.item.name || source.item.id}`
    : `${source.side} socket of berth ${source.item.name || source.item.id}`;
}

function connectionTargetAt(point) {
  let best = null;
  const consider = (source) => {
    const p = connectionPoint(source);
    const distance = Math.hypot(p.x - point.x, p.y - point.y);
    if (distance <= SOCKET_SNAP_RADIUS && (!best || distance < best.distance)) best = { source, distance };
  };
  editorItems.filter((item) => item.kind === "berth").forEach((item) => {
    SOCKET_SIDES.forEach((side) => consider({ kind: "berth", item, side }));
  });
  junctionItems.forEach((item) => consider({ kind: "junction", item }));
  return best ? best.source : null;
}

function drawDraft(point) {
  draftLayer.replaceChildren();
  if (!pendingConnection) return;
  const from = connectionPoint(pendingConnection);
  const target = point ? connectionTargetAt(point) : null;
  const to = target && connectionKey(target) !== connectionKey(pendingConnection) ? connectionPoint(target) : point;
  if (!to) return;
  draftLayer.appendChild(svgNode("path", { class: "track-draft", d: `M ${from.x} ${from.y} L ${to.x} ${to.y}` }));
}

function updatePendingHighlight() {
  editorSvg.querySelectorAll("[data-connect-key]").forEach((node) => {
    const active = Boolean(pendingConnection) && node.dataset.connectKey === connectionKey(pendingConnection);
    node.classList.toggle("pending", active);
    node.setAttribute("aria-pressed", String(active));
  });
}

function cancelConnection(message) {
  pendingConnection = null;
  connectionDrag = null;
  draftLayer.replaceChildren();
  updatePendingHighlight();
  if (message) updateEditorStatus(message);
}

function activateConnection(source) {
  if (canvasSaving) return;
  if (!pendingConnection) {
    pendingConnection = source;
    updatePendingHighlight();
    updateEditorStatus(`Track start set at ${connectionLabel(source)}. Select another socket or junction to finish; Escape cancels.`);
    return;
  }
  if (connectionKey(pendingConnection) === connectionKey(source)) {
    cancelConnection("Track drawing cancelled.");
    return;
  }
  const from = pendingConnection;
  cancelConnection();
  createTrack(from, source);
}

function beginConnectionDrag(source, event) {
  if (canvasSaving || event.button !== 0) return;
  event.stopPropagation();
  event.preventDefault();
  connectionDrag = { source, x: event.clientX, y: event.clientY, moved: false };
}

editorSvg.addEventListener("pointermove", (event) => {
  if (connectionDrag && !connectionDrag.moved && Math.hypot(event.clientX - connectionDrag.x, event.clientY - connectionDrag.y) > 4) {
    connectionDrag.moved = true;
    pendingConnection = connectionDrag.source;
    updatePendingHighlight();
  }
  if (pendingConnection) drawDraft(editorPoint(event));
});
window.addEventListener("pointerup", (event) => {
  if (!connectionDrag) return;
  const drag = connectionDrag;
  connectionDrag = null;
  if (!drag.moved) {
    activateConnection(drag.source);
    return;
  }
  const target = connectionTargetAt(editorPoint(event));
  cancelConnection();
  if (target && connectionKey(target) !== connectionKey(drag.source)) createTrack(drag.source, target);
  else updateEditorStatus("Track drawing cancelled: drop on another socket or junction.");
});
window.addEventListener("pointercancel", () => { if (connectionDrag) cancelConnection(); });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && pendingConnection) cancelConnection("Track drawing cancelled.");
});

function createTrack(from, to) {
  const start = connectionRef(from);
  const end = connectionRef(to);
  const points = Routing.routeBetween(routingState(), start, end, 0, null);
  if (!points) {
    updateEditorStatus("Could not route a track between those connection points.", true);
    return;
  }
  const track = {
    kind: "track", ...nextTrackIdentity("track"), layout_id: layoutId, start, end, points,
    _isNew: true, _alt: 0,
  };
  trackItems.push(track);
  syncTracks();
  renderCanvas();
  selectEditorElement(track);
  updateEditorStatus(`Track added from ${endpointLabel(start)} to ${endpointLabel(end)}. Save canvas changes to keep it.`);
}

function rerouteTrack(track, alt) {
  const points = Routing.routeBetween(routingState(), track.start, track.end, alt, track.id);
  if (!points) return;
  track._alt = alt;
  track.points = points;
  syncTracks();
  renderCanvas();
  renderInspector();
  updateEditorStatus();
}

function addJunctionAt(track, point) {
  if (canvasSaving) return;
  const hit = Routing.nearestPointOnRoute(track.points, point);
  const junction = {
    kind: "junction", ...nextTrackIdentity("junction"), layout_id: layoutId, track_id: track.id,
    x: hit.x, y: hit.y, _isNew: true,
  };
  junctionItems.push(junction);
  renderCanvas();
  selectEditorElement(junction);
  updateEditorStatus(`Junction added to ${track.name || track.id}. Drag from it to a berth socket to connect another track.`);
}

function attachedTrackIds(junction) {
  return trackItems
    .filter((track) => track.start.junction_id === junction.id || track.end.junction_id === junction.id)
    .map((track) => track.id);
}

async function deleteTrackElement(item) {
  if (canvasSaving) return;
  const isJunction = item.kind === "junction";
  const removal = Routing.cascadeDelete(trackItems, junctionItems, isJunction ? attachedTrackIds(item) : [item.id]);
  if (isJunction) removal.junctions.add(item.id);
  const extraTracks = removal.tracks.size - (isJunction ? 0 : 1);
  const extraJunctions = removal.junctions.size - (isJunction ? 1 : 0);
  const detail = extraTracks || extraJunctions
    ? ` This also removes ${extraTracks} connected track(s) and ${extraJunctions} junction(s).` : "";
  if (!confirm(`Delete ${item.kind} ${item.name || item.id}?${detail}`)) return;
  try {
    if (!item._isNew) {
      await submitJson(`${isJunction ? JUNCTION_API : TRACK_API}/${encodeURIComponent(item._originalId || item.id)}`, "DELETE");
    }
    for (let i = trackItems.length - 1; i >= 0; i -= 1) if (removal.tracks.has(trackItems[i].id)) trackItems.splice(i, 1);
    for (let i = junctionItems.length - 1; i >= 0; i -= 1) if (removal.junctions.has(junctionItems[i].id)) junctionItems.splice(i, 1);
    selectedElement = null;
    renderCanvas();
    renderInspector();
    updateEditorStatus(`${isJunction ? "Junction" : "Track"} deleted.`);
  } catch (error) {
    updateEditorStatus(`Failed to delete ${item.kind}: ${error.message}`, true);
  }
}

function trackSelectionKey(item) {
  return item && (item.kind === "track" || item.kind === "junction") ? `${item.kind}:${item.id}` : "";
}

function updateTrackSelection() {
  const selectedKey = trackSelectionKey(selectedElement);
  editorSvg.querySelectorAll("[data-track-focus]").forEach((node) => {
    const selected = node.dataset.trackFocus === selectedKey;
    node.classList.toggle("selected", selected);
    node.setAttribute("aria-pressed", String(selected));
  });
  updateTrackControls();
}

function trackElementKeydown(item) {
  return (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectEditorElement(item);
    } else if ((event.key === "Delete" || event.key === "Backspace") && !canvasSaving) {
      event.preventDefault();
      deleteTrackElement(item);
    }
  };
}

function renderTracks() {
  const focusKey = document.activeElement?.dataset?.trackFocus || null;
  trackLayer.replaceChildren();
  junctionLayer.replaceChildren();
  trackItems.forEach((track) => {
    const group = svgNode("g", {
      class: "track-element", tabindex: 0, role: "button", "data-track-focus": trackSelectionKey(track),
      "aria-label": `track: ${track.name || track.id}, from ${endpointLabel(track.start)} to ${endpointLabel(track.end)}`,
    });
    const d = Routing.smoothPath(track.points);
    group.appendChild(svgNode("path", { class: "track-hit", d }));
    group.appendChild(svgNode("path", { class: "track-line", d }));
    group.addEventListener("click", () => selectEditorElement(track));
    group.addEventListener("dblclick", (event) => addJunctionAt(track, editorPoint(event)));
    group.addEventListener("keydown", trackElementKeydown(track));
    trackLayer.appendChild(group);
  });
  junctionItems.forEach((junction) => {
    const source = { kind: "junction", item: junction };
    const group = svgNode("g", {
      class: "junction-element", tabindex: 0, role: "button", transform: `translate(${junction.x} ${junction.y})`,
      "data-track-focus": trackSelectionKey(junction), "data-connect-key": connectionKey(source),
      "aria-label": `junction: ${junction.name || junction.id}, x ${junction.x}, y ${junction.y}`,
    });
    group.appendChild(svgNode("circle", { class: "junction-hit", r: 10 }));
    group.appendChild(svgNode("circle", { class: "junction-node", r: 5 }));
    group.addEventListener("click", () => selectEditorElement(junction));
    group.addEventListener("pointerdown", (event) => beginConnectionDrag(source, event));
    group.addEventListener("keydown", (event) => {
      if (event.key === "c" || event.key === "C") {
        event.preventDefault();
        activateConnection(source);
      } else {
        trackElementKeydown(junction)(event);
      }
    });
    junctionLayer.appendChild(group);
  });
  updateTrackSelection();
  updatePendingHighlight();
  if (focusKey) editorSvg.querySelector(`[data-track-focus="${focusKey}"]`)?.focus();
}

function renderSockets(group, item) {
  SOCKET_SIDES.forEach((side) => {
    const source = { kind: "berth", item, side };
    const p = Routing.socketPoint(item, side);
    const socket = svgNode("g", {
      class: "berth-socket-group", transform: `translate(${p.x - Number(item.x)} ${p.y - Number(item.y)})`,
      tabindex: 0, role: "button", "aria-pressed": "false", "data-connect-key": connectionKey(source),
      "aria-label": `Track socket on the ${side} of berth ${item.name || item.id}. Press Enter to start or finish a track.`,
    });
    socket.appendChild(svgNode("circle", { class: "berth-socket-hit", r: 10 }));
    socket.appendChild(svgNode("circle", { class: "berth-socket", r: 4 }));
    socket.addEventListener("pointerdown", (event) => beginConnectionDrag(source, event));
    socket.addEventListener("click", (event) => event.stopPropagation());
    socket.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activateConnection(source);
      } else if (event.key === "Escape") {
        cancelConnection("Track drawing cancelled.");
      }
    });
    group.appendChild(socket);
  });
}

function renderTrackInspector(inspector, track) {
  const form = document.createElement("form");
  form.className = "editor-inspector";
  addInspectorField(form, "id", track.id);
  form.querySelector("#element-id").readOnly = true;
  addInspectorField(form, "name", track.name);
  const summary = document.createElement("p");
  summary.className = "hint";
  summary.textContent = `From ${endpointLabel(track.start)} to ${endpointLabel(track.end)} · ${track.points.length} route points`;
  form.appendChild(summary);
  [
    ["track-alt-route", "Try another route", () => rerouteTrack(track, (track._alt || 0) + 1)],
    ["track-reroute", "Re-route automatically", () => rerouteTrack(track, 0)],
    ["track-add-junction", "Add junction", () => addJunctionAt(track, Routing.pointAtFraction(track.points, 0.5))],
  ].forEach(([id, text, handler]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.id = id;
    button.textContent = text;
    button.disabled = canvasSaving;
    button.addEventListener("click", handler);
    form.appendChild(button);
  });
  appendTrackActions(form, track, "Delete track");
  form.addEventListener("input", () => {
    if (canvasSaving) return;
    track.name = new FormData(form).get("name");
    renderTracks();
    updateEditorStatus();
  });
  inspector.appendChild(form);
}

function renderJunctionInspector(inspector, junction) {
  const form = document.createElement("form");
  form.className = "editor-inspector";
  addInspectorField(form, "id", junction.id);
  form.querySelector("#element-id").readOnly = true;
  addInspectorField(form, "name", junction.name);
  const host = trackItems.find((track) => track.id === junction.track_id);
  const attached = attachedTrackIds(junction).length > 0;
  const label = document.createElement("label");
  label.textContent = "position along track (%)";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.id = "element-position";
  slider.min = "0";
  slider.max = "100";
  slider.value = String(Math.round(host ? Routing.fractionOfPoint(host.points, junction) * 100 : 0));
  slider.disabled = canvasSaving || attached || !host;
  label.appendChild(slider);
  form.appendChild(label);
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = attached
    ? "Tracks are connected here, so the junction position is fixed. Delete the connected tracks to move it."
    : "Drag from this junction to a berth socket (or focus it and press C, then choose a socket) to connect a track.";
  form.appendChild(hint);
  appendTrackActions(form, junction, "Delete junction");
  form.addEventListener("input", (event) => {
    if (canvasSaving) return;
    junction.name = new FormData(form).get("name");
    if (event.target === slider && host && !attached) {
      const point = Routing.pointAtFraction(host.points, Number(slider.value) / 100);
      junction.x = point.x;
      junction.y = point.y;
    }
    renderTracks();
    updateEditorStatus();
  });
  inspector.appendChild(form);
}

function appendTrackActions(form, item, deleteText) {
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Apply properties";
  save.disabled = canvasSaving;
  form.appendChild(save);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "danger";
  remove.id = "element-delete";
  remove.textContent = deleteText;
  remove.disabled = canvasSaving;
  remove.addEventListener("click", () => deleteTrackElement(item));
  form.appendChild(remove);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!canvasSaving) saveCanvas();
  });
}

async function loadTrackData() {
  const [tracks, junctions] = await Promise.all(["tracks", "junctions"].map(async (kind) => {
    const response = await fetch(`/api/layouts/${encodeURIComponent(layoutId)}/${kind}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return (await response.json()).items;
  }));
  const build = (kind) => (row) => {
    const item = { ...row, kind, _originalId: row.id, _isNew: false, _alt: 0 };
    item._savedPayload = editorPayload(item);
    return item;
  };
  trackItems.splice(0, trackItems.length, ...tracks.map(build("track")));
  junctionItems.splice(0, junctionItems.length, ...junctions.map(build("junction")));
}

async function saveTrackItem(item, api, failures) {
  const payload = editorPayload(item);
  try {
    await submitJson(item._isNew ? api : `${api}/${encodeURIComponent(item._originalId || item.id)}`, item._isNew ? "POST" : "PUT", payload);
    item._originalId = payload.id;
    item._isNew = false;
    item._savedPayload = payload;
  } catch (error) {
    failures.push(`${item.kind} ${item.id}: ${error.message}`);
  }
}

async function saveTracks(failures) {
  for (const track of Routing.orderTracks(trackItems, junctionItems)) {
    if (editorItemIsDirty(track)) await saveTrackItem(track, TRACK_API, failures);
    for (const junction of junctionItems.filter((j) => j.track_id === track.id && editorItemIsDirty(j))) {
      await saveTrackItem(junction, JUNCTION_API, failures);
    }
  }
}

function renderCanvas() {
  syncTracks();
  updateCanvasExtent();
  const focusedIndex = document.activeElement?.classList?.contains("layout-element")
    ? document.activeElement.dataset.editorIndex : null;
  editorSvg.querySelectorAll(".layout-element").forEach((node) => node.remove());
  sortedByLayer(editorItems).forEach((item) => {
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.setAttribute("class", `layout-element${selectedElement === item ? " selected" : ""}`);
    group.dataset.editorIndex = String(editorItems.indexOf(item));
    group.setAttribute("transform", `translate(${item.x} ${item.y})`);
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("fill", "black");
    group.setAttribute("aria-pressed", String(selectedElement === item));
    group.setAttribute("aria-label", `${item.kind}: ${item.name || item.id || "new element"}, x ${item.x}, y ${item.y}`);
    if (item.kind === "berth") {
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("class", `${item.berth_type}-shape`);
      rect.setAttribute("width", item.width);
      rect.setAttribute("height", item.height);
      rect.setAttribute("rx", "5");
      group.appendChild(rect);
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", Number(item.width) / 2);
      label.setAttribute("y", Number(item.height) / 2 + 4);
      label.setAttribute("text-anchor", "middle");
      group.setAttribute("fill", "grey");
      label.setAttribute("fill", "black");
      label.textContent = item.name || item.id || "New berth";
      group.appendChild(label);
      renderSockets(group, item);
    } else if (item.kind === "level_crossing") {
      renderLevelCrossing(group, item);
    } else if (item.kind === "platform") {
      renderPlatform(group, item);
    } else if (item.kind === "bridge") {
      renderBridge(group, item);
    } else if (item.kind === "label") {
      renderLabel(group, item);
    } else {
      const stem = document.createElementNS("http://www.w3.org/2000/svg", "line");
      stem.setAttribute("class", "signal-stem");
      stem.setAttribute("x1", "0");
      stem.setAttribute("y1", "8");
      stem.setAttribute("x2", "0");
      stem.setAttribute("y2", "30");
      const signalParts = svgNode("g", { class: "signal-rotation" });
      const signalRotation = Number(item.rotation) || 0;
      if (signalRotation) signalParts.setAttribute("transform", `rotate(${signalRotation} 0 8)`);
      signalParts.appendChild(stem);
      group.appendChild(signalParts);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("class", "signal-shape");
      circle.setAttribute("cx", "0");
      circle.setAttribute("cy", "8");
      circle.setAttribute("r", "8");
      signalParts.appendChild(circle);
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("fill", "white");
      label.setAttribute("x", "12");
      label.setAttribute("y", "12");
      label.textContent = item.name || item.id || "New signal";
      group.appendChild(label);
    }
    group.addEventListener("click", () => selectEditorElement(item));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectEditorElement(item);
      } else if (!canvasSaving && selectedElement === item && event.key.startsWith("Arrow")) {
        event.preventDefault();
        const grid = snapGridSize();
        const step = Math.max(event.shiftKey ? 10 : 1, grid);
        clampElementPosition(
          item,
          Number(item.x) + (event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0),
          Number(item.y) + (event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0),
        );
        renderCanvas();
        renderInspector();
        updateEditorStatus();
      }
    });
    group.addEventListener("pointerdown", (event) => {
      if (canvasSaving || event.button !== 0) return;
      selectEditorElement(item);
      dragOffset = { item, point: editorPoint(event) };
      dragOffset.x = dragOffset.point.x - Number(item.x);
      dragOffset.y = dragOffset.point.y - Number(item.y);
      group.setPointerCapture(event.pointerId);
    });
    group.addEventListener("pointermove", (event) => {
      if (!dragOffset || dragOffset.item !== item || !group.hasPointerCapture(event.pointerId)) return;
      const point = editorPoint(event);
      clampElementPosition(item, point.x - dragOffset.x, point.y - dragOffset.y);
      group.setAttribute("transform", `translate(${item.x} ${item.y})`);
      if (item.kind === "berth") {
        syncTracks();
        renderTracks();
      }
      const xInput = document.getElementById("element-x");
      const yInput = document.getElementById("element-y");
      if (selectedElement === item && xInput && yInput) {
        xInput.value = item.x;
        yInput.value = item.y;
      }
      updateEditorStatus();
    });
    group.addEventListener("pointerup", () => { dragOffset = null; });
    group.addEventListener("pointercancel", () => { dragOffset = null; });
    editorSvg.insertBefore(group, junctionLayer);
  });
  renderTracks();
  if (focusedIndex !== null) {
    editorSvg.querySelector(`[data-editor-index="${focusedIndex}"]`)?.focus();
  }
}

function addInspectorField(form, name, value, type = "text", options = null) {
  const label = document.createElement("label");
  label.textContent = name.replace("_", " ");
  let input;
  if (options) {
    input = document.createElement("select");
    options.forEach((option) => {
      const choice = document.createElement("option");
      choice.value = option;
      choice.textContent = option;
      input.appendChild(choice);
    });
  } else {
    input = document.createElement("input");
    input.type = type;
  }
  input.id = `element-${name}`;
  input.name = name;
  input.value = value ?? "";
  input.required = name === "id" || name === "name";
  input.disabled = canvasSaving;
  if (type === "number") {
    const size = itemSize(selectedElement);
    const maxX = CANVAS_WIDTH - size.width;
    const maxY = CANVAS_HEIGHT - size.height;
    input.min = "0";
    input.max = String(name === "x" ? maxX : name === "y" ? maxY : name === "width" ? CANVAS_WIDTH : CANVAS_HEIGHT);
    input.step = "1";
  }
  label.appendChild(input);
  form.appendChild(label);
}

function renderInspector() {
  const inspector = document.getElementById("element-inspector");
  inspector.replaceChildren();
  if (!selectedElement) {
    inspector.textContent = "Select an element on the canvas or add a berth, signal, level crossing, bridge, label or platform.";
    return;
  }
  const item = selectedElement;
  if (item.kind === "track") return renderTrackInspector(inspector, item);
  if (item.kind === "junction") return renderJunctionInspector(inspector, item);
  const form = document.createElement("form");
  form.className = "editor-inspector";
  addInspectorField(form, "id", item.id);
  addInspectorField(form, "name", item.name);
  addInspectorField(form, "x", item.x, "number");
  addInspectorField(form, "y", item.y, "number");
  if (item.kind === "berth") {
    addInspectorField(form, "width", item.width, "number");
    addInspectorField(form, "height", item.height, "number");
    addInspectorField(form, "berth_type", item.berth_type, "text", ["normal", "platform", "siding"]);
  } else if (item.kind in OBJECT_KINDS) {
    if (item.kind === "bridge" || item.kind === "platform") {
      addInspectorField(form, "width", item.width, "number");
      addInspectorField(form, "height", item.height, "number");
    }
    if (item.kind === "bridge") {
      addInspectorField(form, "rotation", Number(item.rotation) || 0, "text", BRIDGE_ROTATIONS.map(String));
    }
    if (item.kind === "level_crossing") {
      addInspectorField(form, "height", item.height, "number");
      addInspectorField(form, "rotation", Number(item.rotation) || 0, "text", CROSSING_ROTATIONS.map(String));
    }
    addInspectorField(form, "variant", item.variant, "text", OBJECT_KINDS[item.kind].variants);
    if (BERTH_LINKED_KINDS.includes(item.kind)) addInspectorField(form, "berth_id", item.berth_id || "");
  } else {
    addInspectorField(form, "signal_type", item.signal_type, "text", ["auto", "controlled", "shunt"]);
    addInspectorField(form, "rotation", Number(item.rotation) || 0, "text", SIGNAL_ROTATIONS.map(String));
  }
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = item._isNew ? "Add element" : "Apply properties";
  save.disabled = canvasSaving;
  form.appendChild(save);
  [["element-send-back", "Send to back", false], ["element-bring-front", "Bring to front", true]].forEach(([id, text, toFront]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.id = id;
    button.textContent = text;
    button.disabled = canvasSaving;
    button.addEventListener("click", () => changeLayer(item, toFront));
    form.appendChild(button);
  });
  if (item.kind in OBJECT_KINDS) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.id = "element-delete";
    remove.textContent = "Delete element";
    remove.disabled = canvasSaving;
    remove.addEventListener("click", () => deleteEditorElement(item));
    form.appendChild(remove);
  }
  form.addEventListener("input", () => {
    if (canvasSaving) return;
    const data = new FormData(form);
    const previousId = item.id;
    for (const [name, value] of data.entries()) {
      item[name] = ["x", "y", "width", "height", "rotation", "z_index"].includes(name) ? Number(value) : value;
    }
    if (item.kind === "berth" && item.id !== previousId) retargetBerthRefs(previousId, item.id);
    if (["x", "y", "width", "height", "rotation"].some((name) => data.has(name))) {
      clampElementPosition(item, Number(item.x), Number(item.y));
      document.getElementById("element-x").value = item.x;
      document.getElementById("element-y").value = item.y;
    }
    renderCanvas();
    updateEditorStatus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (canvasSaving) return;
    clampElementPosition(item, Number(item.x), Number(item.y));
    renderCanvas();
    saveCanvas();
  });
  inspector.appendChild(form);
}

function selectEditorElement(item) {
  selectedElement = item;
  editorSvg.querySelectorAll(".layout-element").forEach((group) => {
    const selected = Number(group.dataset.editorIndex) === editorItems.indexOf(item);
    group.classList.toggle("selected", selected);
    group.setAttribute("aria-pressed", String(selected));
  });
  updateTrackSelection();
  renderInspector();
}

async function deleteEditorElement(item) {
  if (canvasSaving) return;
  if (!confirm(`Delete ${item.kind.replace("_", " ")} ${item.name || item.id || "(new)"}?`)) return;
  try {
    if (!item._isNew) {
      await submitJson(`${OBJECT_API}/${encodeURIComponent(item._originalId || item.id)}`, "DELETE");
    }
    editorItems.splice(editorItems.indexOf(item), 1);
    selectedElement = null;
    renderCanvas();
    renderInspector();
    updateEditorStatus("Element deleted.");
  } catch (error) {
    updateEditorStatus(`Failed to delete element: ${error.message}`, true);
  }
}

function beginAddingElement(kind) {
  if (!layoutId || canvasSaving) return;
  const typeSelect = document.getElementById(`add-${kind}-type`);
  const chosen = typeSelect ? typeSelect.value : "";
  const defaults = OBJECT_KINDS[kind] || { width: 40, height: 30 };
  const identity = nextElementIdentity(kind);
  const item = {
    kind, ...identity, layout_id: layoutId,
    x: 60, y: 160, width: defaults.width, height: defaults.height,
    berth_type: kind === "berth" && chosen ? chosen : "normal",
    signal_type: kind === "signal" && chosen ? chosen : "auto",
    variant: kind in OBJECT_KINDS ? (chosen || OBJECT_KINDS[kind].variants[0]) : undefined,
    berth_id: null, rotation: 0, z_index: 0,
    _isNew: true,
  };
  editorItems.push(item);
  renderCanvas();
  selectEditorElement(item);
  document.getElementById("element-id").focus();
}

async function prepareTableChange() {
  if (canvasSaving) return false;
  if (!editorHasChanges()) return true;
  if (!confirm("Discard unsaved canvas edits before changing elements in the table?")) return false;
  return await loadEditor();
}

async function loadEditorItems(kind) {
  const items = [];
  let page = 1;
  let pages = 1;
  do {
    const response = await fetch(`/api/layouts/${encodeURIComponent(layoutId)}/${kind}s?page=${page}&page_size=200`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    items.push(...data.items.map((row) => {
      const item = { ...row, kind, _originalId: row.id, _isNew: false };
      item._savedPayload = editorPayload(item);
      return item;
    }));
    pages = data.pages;
    page += 1;
  } while (page <= pages);
  return items;
}

async function loadEditorObjects() {
  const items = [];
  let page = 1;
  let pages = 1;
  do {
    const response = await fetch(`/api/layouts/${encodeURIComponent(layoutId)}/objects?page=${page}&page_size=200`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    items.push(...data.items.filter((row) => row.object_type in OBJECT_KINDS).map((row) => {
      const item = { ...row, kind: row.object_type, _originalId: row.id, _isNew: false };
      item._savedPayload = editorPayload(item);
      return item;
    }));
    pages = data.pages;
    page += 1;
  } while (page <= pages);
  return items;
}

async function loadEditor() {
  setAddControlsDisabled(true);
  try {
    editorItems.splice(0, editorItems.length,
      ...(await loadEditorItems("berth")), ...(await loadEditorItems("signal")),
      ...(await loadEditorObjects()));
    await loadTrackData();
    selectedElement = null;
    cancelConnection();
    renderCanvas();
    if (CANVAS_WIDTH > BASE_CANVAS_WIDTH || CANVAS_HEIGHT > BASE_CANVAS_HEIGHT) canvasView.fit();
    renderInspector();
    updateEditorStatus("Loaded layout elements.");
    setAddControlsDisabled(false);
    return true;
  } catch (error) {
    updateEditorStatus(`Failed to load canvas elements: ${error.message}`, true);
    return false;
  }
}

async function saveCanvas() {
  if (canvasSaving || !editorHasChanges()) return !canvasSaving;
  canvasSaving = true;
  document.getElementById("save-canvas").disabled = true;
  setAddControlsDisabled(true);
  document.querySelectorAll("#element-inspector input, #element-inspector select, #element-inspector button")
    .forEach((control) => { control.disabled = true; });
  try {
    const failures = [];
    const changedKinds = new Set();
    for (const item of editorItems.filter(editorItemIsDirty)) {
      const originalId = item._originalId || item.id;
      const payload = editorPayload(item);
      try {
        await submitJson(
          item._isNew ? elementApi(item) : `${elementApi(item)}/${encodeURIComponent(originalId)}`,
          item._isNew ? "POST" : "PUT",
          payload,
        );
        item._originalId = payload.id;
        item._isNew = false;
        item._savedPayload = payload;
        changedKinds.add(item.kind);
      } catch (error) {
        failures.push(`${item.kind} ${item.id || "(new)"}: ${error.message}`);
      }
    }
    await saveTracks(failures);
    renderCanvas();
    for (const kind of changedKinds) if (TABLES[kind]) await loadTable(kind);
    renderInspector();
    if (failures.length) {
      updateEditorStatus(`Some changes were not saved: ${failures.join("; ")}`, true);
      return false;
    }
    updateEditorStatus("Canvas changes saved.");
    return true;
  } finally {
    canvasSaving = false;
    setAddControlsDisabled(isNew);
    document.querySelectorAll("#element-inspector input, #element-inspector select, #element-inspector button")
      .forEach((control) => { control.disabled = false; });
  }
}

window.addEventListener("beforeunload", (event) => {
  if (!editorHasChanges()) return;
  event.preventDefault();
  event.returnValue = "";
});

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
    if (!isNew && !(await saveCanvas())) return;
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
  const cfg = TABLES[kind];
  const payload = { layout_id: layoutId };
  if (cfg.objectType) payload.object_type = cfg.objectType;
  cfg.fields.forEach(([name, type]) => {
    const input = container.querySelector(`[name="${name}"]`);
    const value = input.value.trim();
    if (type === "number" || name === "rotation") payload[name] = Number(value);
    else if (name === "berth_id") payload[name] = value || null;
    else payload[name] = value;
  });
  return payload;
}

async function reloadAfter(kind, action, okMessage) {
  const cfg = TABLES[kind];
  try {
    if (!(await prepareTableChange())) return;
    await action();
    setStatus(cfg.status, okMessage);
    await loadTable(kind);
    if (editorHasChanges()) updateEditorStatus("Table changes saved; canvas edits are still unsaved.", true);
    else await loadEditor();
  } catch (error) {
    setStatus(cfg.status, `Failed: ${error.message}`, true);
  }
}

function rowIsDirty(kind, row) {
  return JSON.stringify(collect(kind, row)) !== row.dataset.savedValues;
}

function updateSaveAllButton(kind) {
  const button = document.getElementById(`save-all-${kind}s`);
  if (!button) return;
  button.disabled = !Array.from(document.querySelectorAll(`#${TABLES[kind].body} tr[data-original-id]`))
    .some((row) => rowIsDirty(kind, row));
}

async function saveAllEdited(kind) {
  const cfg = TABLES[kind];
  const button = document.getElementById(`save-all-${kind}s`);
  const rows = Array.from(document.querySelectorAll(`#${cfg.body} tr[data-original-id]`))
    .filter((row) => rowIsDirty(kind, row));
  if (!rows.length) return;
  if (!(await prepareTableChange())) return;

  button.disabled = true;
  let saved = 0;
  const failures = [];
  for (const row of rows) {
    const oldId = row.dataset.originalId;
    const payload = collect(kind, row);
    try {
      await submitJson(`${cfg.api}/${encodeURIComponent(oldId)}`, "PUT", payload);
      row.dataset.originalId = payload.id;
      row.dataset.savedValues = JSON.stringify(payload);
      saved += 1;
    } catch (error) {
      failures.push(`${oldId}: ${error.message}`);
    }
  }

  if (failures.length) {
    setStatus(cfg.status, `Saved ${saved}; failed: ${failures.join("; ")}`, true);
    updateSaveAllButton(kind);
  } else {
    setStatus(cfg.status, `Saved ${saved} edited ${kind.replace("_", " ")}(s).`);
    await loadTable(kind);
    if (editorHasChanges()) updateEditorStatus("Table changes saved; canvas edits are still unsaved.", true);
    else await loadEditor();
  }
}

function renderRow(kind, item) {
  const cfg = TABLES[kind];
  const tr = el("tr");
  tr.dataset.originalId = item.id;
  cfg.fields.forEach(([name, type]) => {
    tr.appendChild(el("td", {}, [fieldInput(name, type, item[name], `${kind} ${item.id} ${name}`)]));
  });
  tr.dataset.savedValues = JSON.stringify(collect(kind, tr));
  tr.addEventListener("input", () => updateSaveAllButton(kind));
  tr.addEventListener("change", () => updateSaveAllButton(kind));
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
  const type = cfg.objectType || document.getElementById(`${kind}-type`).value;
  const size = document.getElementById(`${kind}-page-size`).value;
  const filterType = cfg.objectType ? "" : type;
  const params = new URLSearchParams({ page: tableState[kind].page, page_size: size });
  if (q) params.set("q", q);
  if (type) params.set("type", type);
  tbody.setAttribute("aria-busy", "true");
  tbody.replaceChildren(messageRow(cfg.fields.length + 1, "Loading…"));
  try {
    const response = await fetch(`/api/layouts/${encodeURIComponent(layoutId)}/${cfg.objectType ? "object" : kind}s?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    tableState[kind].page = data.page;
    tbody.replaceChildren();
    if (!data.items.length) {
      tbody.appendChild(messageRow(cfg.fields.length + 1, q || filterType ? `No ${kind}s match the filters.` : `No ${kind}s in this layout yet.`));
    }
    data.items.forEach((item) => tbody.appendChild(renderRow(kind, item)));
    updateSaveAllButton(kind);
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
ADD_KINDS.forEach((kind) => {
  document.getElementById(`add-${kind}`).addEventListener("click", () => beginAddingElement(kind));
});
document.getElementById("save-canvas").addEventListener("click", saveCanvas);
document.getElementById("add-junction").addEventListener("click", () => {
  if (selectedElement && selectedElement.kind === "track") {
    addJunctionAt(selectedElement, Routing.pointAtFraction(selectedElement.points, 0.5));
  }
});
if (isNew) {
  setAddControlsDisabled(true);
  updateEditorStatus("Save the layout first to add and arrange elements.");
}
if (!isNew) {
  loadLayout();
  loadEditor();
  document.getElementById("delete-layout").addEventListener("click", deleteLayout);
  Object.keys(TABLES).forEach((kind) => {
    setupTable(kind);
    document.getElementById(`save-all-${kind}s`).addEventListener("click", () => saveAllEdited(kind));
  });
  document.getElementById("open-import").addEventListener("click", () => openImportDialog(layoutId));
  document.addEventListener("berths-imported", () => loadTable("berth"));
}
