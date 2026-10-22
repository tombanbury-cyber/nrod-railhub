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
