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
const OBJECT_API = "/api/layout-objects";
const OBJECT_KINDS = {
  level_crossing: { title: "Level crossing", variants: ["automatic", "barrier", "user_worked"], width: 40, height: 40 },
  bridge: { title: "Bridge", variants: ["road_over", "road_under", "footbridge"], width: 80, height: 24 },
  label: { title: "Label", variants: ["station", "line", "note"], width: 60, height: 20 },
  platform: { title: "Platform", variants: ["side", "island", "bay"], width: 120, height: 24 },
};
const ADD_KINDS = ["berth", "signal", "level_crossing", "bridge", "label", "platform"];
const CROSSING_ROTATIONS = [0, 45, 90];
const CROSSING_PAD = 4;
const tableState = { berth: { page: 1 }, signal: { page: 1 } };
const editorSvg = document.getElementById("layout-canvas-svg");
const editorItems = [];
let selectedElement = null;
let dragOffset = null;
let canvasSaving = false;
const BASE_CANVAS_WIDTH = 1200;
const BASE_CANVAS_HEIGHT = 700;
const CANVAS_PADDING = 100;
let CANVAS_WIDTH = BASE_CANVAS_WIDTH;
let CANVAS_HEIGHT = BASE_CANVAS_HEIGHT;
function setupPanZoom(svg, options) {
  const MIN_SCALE = 0.1;
  const MAX_SCALE = 8;
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
    if (event.ctrlKey || event.metaKey) {
      const p = toWorld(event.clientX, event.clientY);
      zoomAt(Math.exp(-event.deltaY * 0.01), p.x, p.y);
    } else {
      const k = worldPerPixel();
      const dx = event.shiftKey && !event.deltaX ? event.deltaY : event.deltaX;
      const dy = event.shiftKey && !event.deltaX ? 0 : event.deltaY;
      view.x += dx * k;
      view.y += dy * k;
      apply();
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
  if (item.kind === "berth" || item.kind === "bridge" || item.kind === "platform") {
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

function labelWidth(item) {
  return Math.max(30, String(item.name || item.id || "New label").length * 8 + 16);
}

function elementApi(item) {
  return item.kind in OBJECT_KINDS ? OBJECT_API : TABLES[item.kind].api;
}

function setAddControlsDisabled(disabled) {
  document.querySelectorAll(".add-control button, .add-control select")
    .forEach((control) => { control.disabled = disabled; });
}

function updateCanvasExtent() {
  CANVAS_WIDTH = BASE_CANVAS_WIDTH;
  CANVAS_HEIGHT = BASE_CANVAS_HEIGHT;
  editorItems.forEach((item) => {
    const { width, height } = itemSize(item);
    CANVAS_WIDTH = Math.max(CANVAS_WIDTH, Number(item.x) + width + CANVAS_PADDING);
    CANVAS_HEIGHT = Math.max(CANVAS_HEIGHT, Number(item.y) + height + CANVAS_PADDING);
  });
  const grid = editorSvg.querySelector("#canvas-grid-bg");
  if (grid) {
    grid.setAttribute("width", CANVAS_WIDTH);
    grid.setAttribute("height", CANVAS_HEIGHT);
  }
}
const canvasView = setupPanZoom(editorSvg, {
  getBase: () => ({ w: BASE_CANVAS_WIDTH, h: BASE_CANVAS_HEIGHT }),
  getBounds: () => ({ x: 0, y: 0, w: CANVAS_WIDTH, h: CANVAS_HEIGHT }),
  isBackground: (target) => !target.closest(".layout-element"),
  onChange: (scale) => {
    const label = document.getElementById("canvas-zoom-level");
    if (label) label.textContent = `${Math.round(scale * 100)}%`;
  },
});
document.getElementById("canvas-zoom-in").addEventListener("click", () => canvasView.zoomBy(1.25));
document.getElementById("canvas-zoom-out").addEventListener("click", () => canvasView.zoomBy(0.8));
document.getElementById("canvas-zoom-fit").addEventListener("click", () => { updateCanvasExtent(); canvasView.fit(); });
document.getElementById("canvas-zoom-reset").addEventListener("click", () => canvasView.reset());

function editorPayload(item) {
  const payload = {
    id: item.id, layout_id: layoutId, name: item.name, x: item.x, y: item.y,
  };
  if (item.kind === "berth") {
    Object.assign(payload, {
      width: item.width, height: item.height, berth_type: item.berth_type,
    });
  } else if (item.kind in OBJECT_KINDS) {
    Object.assign(payload, {
      object_type: item.kind, width: item.width, height: item.height, variant: item.variant,
      berth_id: item.kind === "platform" ? (item.berth_id || null) : null,
      rotation: item.kind === "level_crossing" ? Number(item.rotation) || 0 : 0,
    });
  } else {
    payload.signal_type = item.signal_type;
  }
  return payload;
}

function editorItemIsDirty(item) {
  return item._isNew || JSON.stringify(editorPayload(item)) !== JSON.stringify(item._savedPayload);
}

function editorHasChanges() {
  return editorItems.some(editorItemIsDirty);
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
  group.appendChild(svgNode("rect", { class: `bridge-shape bridge-${item.variant}`, width, height, rx: 3 }));
  group.appendChild(svgNode("line", { class: "bridge-end", x1: 0, y1: 0, x2: 0, y2: height }));
  group.appendChild(svgNode("line", { class: "bridge-end", x1: width, y1: 0, x2: width, y2: height }));
  group.appendChild(svgNode("text", { x: width / 2, y: height / 2 + 4, "text-anchor": "middle", fill: "black" },
    item.name || item.id || "New bridge"));
}

function renderLabel(group, item) {
  group.appendChild(svgNode("rect", { class: `label-shape label-${item.variant}`, width: labelWidth(item), height: 20, rx: 3 }));
  group.appendChild(svgNode("text", {
    class: `label-text label-text-${item.variant}`, x: 8, y: 14, fill: "black",
  }, item.name || item.id || "New label"));
}

function renderCanvas() {
  updateCanvasExtent();
  const focusedIndex = document.activeElement?.classList?.contains("layout-element")
    ? document.activeElement.dataset.editorIndex : null;
  editorSvg.querySelectorAll(".layout-element").forEach((node) => node.remove());
  editorItems.forEach((item) => {
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
      group.appendChild(stem);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("class", "signal-shape");
      circle.setAttribute("cx", "0");
      circle.setAttribute("cy", "8");
      circle.setAttribute("r", "8");
      group.appendChild(circle);
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
    editorSvg.appendChild(group);
  });
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
    inspector.textContent = "Select an element on the canvas or add a berth or signal.";
    return;
  }
  const item = selectedElement;
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
    if (item.kind === "level_crossing") {
      addInspectorField(form, "height", item.height, "number");
      addInspectorField(form, "rotation", Number(item.rotation) || 0, "text", CROSSING_ROTATIONS.map(String));
    }
    addInspectorField(form, "variant", item.variant, "text", OBJECT_KINDS[item.kind].variants);
    if (item.kind === "platform") addInspectorField(form, "berth_id", item.berth_id || "");
  } else {
    addInspectorField(form, "signal_type", item.signal_type, "text", ["auto", "controlled", "shunt"]);
  }
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = item._isNew ? "Add element" : "Apply properties";
  save.disabled = canvasSaving;
  form.appendChild(save);
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
    for (const [name, value] of data.entries()) {
      item[name] = ["x", "y", "width", "height", "rotation"].includes(name) ? Number(value) : value;
    }
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
  const defaults = OBJECT_KINDS[kind] || { width: 60, height: 30 };
  const item = {
    kind, id: "", layout_id: layoutId, name: "",
    x: 60, y: 60, width: defaults.width, height: defaults.height,
    berth_type: kind === "berth" && chosen ? chosen : "normal",
    signal_type: kind === "signal" && chosen ? chosen : "auto",
    variant: kind in OBJECT_KINDS ? (chosen || OBJECT_KINDS[kind].variants[0]) : undefined,
    berth_id: null, rotation: 0,
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
  try {
    editorItems.splice(0, editorItems.length,
      ...(await loadEditorItems("berth")), ...(await loadEditorItems("signal")),
      ...(await loadEditorObjects()));
    selectedElement = null;
    renderCanvas();
    if (CANVAS_WIDTH > BASE_CANVAS_WIDTH || CANVAS_HEIGHT > BASE_CANVAS_HEIGHT) canvasView.fit();
    renderInspector();
    updateEditorStatus("Loaded layout elements.");
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

function updateSaveAllBerthsButton() {
  const button = document.getElementById("save-all-berths");
  if (!button) return;
  button.disabled = !Array.from(document.querySelectorAll("#berth-rows tr[data-original-id]"))
    .some((row) => rowIsDirty("berth", row));
}

async function saveAllEditedBerths() {
  const button = document.getElementById("save-all-berths");
  const rows = Array.from(document.querySelectorAll("#berth-rows tr[data-original-id]"))
    .filter((row) => rowIsDirty("berth", row));
  if (!rows.length) return;
  if (!(await prepareTableChange())) return;

  button.disabled = true;
  let saved = 0;
  const failures = [];
  for (const row of rows) {
    const oldId = row.dataset.originalId;
    const payload = collect("berth", row);
    try {
      await submitJson(`/api/berths/${encodeURIComponent(oldId)}`, "PUT", payload);
      row.dataset.originalId = payload.id;
      row.dataset.savedValues = JSON.stringify(payload);
      saved += 1;
    } catch (error) {
      failures.push(`${oldId}: ${error.message}`);
    }
  }

  if (failures.length) {
    setStatus("berth-status", `Saved ${saved}; failed: ${failures.join("; ")}`, true);
    updateSaveAllBerthsButton();
  } else {
    setStatus("berth-status", `Saved ${saved} edited berth(s).`);
    await loadTable("berth");
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
  tr.addEventListener("input", updateSaveAllBerthsButton);
  tr.addEventListener("change", updateSaveAllBerthsButton);
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
    if (kind === "berth") updateSaveAllBerthsButton();
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
if (isNew) {
  setAddControlsDisabled(true);
  updateEditorStatus("Save the layout first to add and arrange elements.");
}
if (!isNew) {
  loadLayout();
  loadEditor();
  document.getElementById("delete-layout").addEventListener("click", deleteLayout);
  setupTable("berth");
  document.getElementById("save-all-berths").addEventListener("click", saveAllEditedBerths);
  setupTable("signal");
  document.getElementById("open-import").addEventListener("click", () => openImportDialog(layoutId));
  document.addEventListener("berths-imported", () => loadTable("berth"));
}
