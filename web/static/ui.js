const API_BASE = window.location.origin;
const WS_URL = `ws://${window.location.hostname}:${window.location.port}/ws`;
const DEFAULT_LAYOUT_ID = 'demo';
const STATE_REFRESH_MS = 5000;

// SVG presentation constants
const COLORS = {
    berthFill: '#4CAF50',
    berthStroke: '#2E7D32',
    occupiedFill: '#FFC107',
    occupiedStroke: '#F57C00',
    title: '#4CAF50',
    berthLabel: 'white',
    headcodeLabel: 'grey',
    crossingFree: '#dc2626',
    crossingCaption: '#5f5454',
    crossingTextOccupied: '#FFC107',
    crossingTextFree: '#000',
    accentFree: '#555',
    fillFree: '#999',
    blockLabel: '#222',
    markerFill: '#FF5722',
    markerStroke: '#D32F2F',
};
const STROKE = {
    thin: '1',
    footCrossingWidth: 3,
    crossingWidth: 5,
    markerWidth: '1',
    footCrossingDash: '2 4',
    userWorkedDash: '6 4',
    noDash: '',
};
const FONT = {
    titleSize: '18',
    titleWeight: 'bold',
    berthSize: '11',
    berthWeight: 'normal',
    headcodeSize: '13',
    headcodeWeight: 'bold',
    crossingCaptionSize: 12,
    crossingTextSize: 13,
    blockSize: 11,
    anchorMiddle: 'middle',
};
const MARKER_GLOW = 'drop-shadow(0 0 2px #FF5722)';

let ws = null;
let statePollTimer = null;
let berths = [];
let layouts = [];
let currentLayout = null;
let layoutLoadToken = 0;
let trainPositions = {};  // train_id -> berth_id

// Initialize
async function init() {
    await loadLayouts();
    startStatePolling();
    connectWebSocket();
}

function setLayoutMessage(text) {
    document.getElementById('layoutInfo').textContent = text;
}

function updateQueryLayout(layoutId) {
    const url = new URL(window.location.href);
    url.searchParams.set('layout_id', layoutId);
    window.history.replaceState(null, '', url.toString());
}

function resetLayoutView() {
    berths = [];
    currentLayout = null;
    trainPositions = {};
    document.getElementById('eventLog').textContent = '';
    renderSchematic();
}

async function loadLayouts() {
    const select = document.getElementById('layoutSelect');
    select.textContent = '';
    try {
        const response = await fetch(`${API_BASE}/api/layouts`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        layouts = Array.isArray(data) ? data : [];
    } catch (error) {
        console.error('Failed to load layouts:', error);
        layouts = [];
        resetLayoutView();
        setLayoutMessage('Error loading layouts');
        return;
    }

    if (layouts.length === 0) {
        select.disabled = true;
        resetLayoutView();
        setLayoutMessage('No layouts available. Create one in /admin.');
        return;
    }

    layouts.forEach(layout => {
        const option = document.createElement('option');
        option.value = layout.id;
        option.textContent = layout.name ? `${layout.name} (${layout.id})` : layout.id;
        select.appendChild(option);
    });
    select.disabled = false;
    select.addEventListener('change', () => selectLayout(select.value));

    const requested = new URLSearchParams(window.location.search).get('layout_id');
    if (requested && !layouts.some(l => l.id === requested)) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = `Unknown layout: ${requested}`;
        select.insertBefore(option, select.firstChild);
        select.value = '';
        resetLayoutView();
        setLayoutMessage(`Unknown layout "${requested}". Choose a layout from the list.`);
        return;
    }
    const initial = requested
        || (layouts.some(l => l.id === DEFAULT_LAYOUT_ID) ? DEFAULT_LAYOUT_ID : layouts[0].id);
    select.value = initial;
    await selectLayout(initial);
}

async function selectLayout(layoutId) {
    const select = document.getElementById('layoutSelect');
    const unknown = select.querySelector('option[value=""]');
    if (unknown && layoutId) unknown.remove();
    if (!layoutId) return;
    const token = ++layoutLoadToken;
    resetLayoutView();
    setLayoutMessage('Loading layout...');
    updateQueryLayout(layoutId);
    const ok = await loadLayout(layoutId, token);
    if (!ok || token !== layoutLoadToken) return;
    await loadBerths(layoutId, token);
    if (token !== layoutLoadToken) return;
    renderSchematic();
    resetSchematicView();
    await refreshState();
}

async function loadLayout(layoutId, token) {
    try {
        const response = await fetch(`${API_BASE}/layout/${encodeURIComponent(layoutId)}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const layout = await response.json();
        if (token !== layoutLoadToken) return false;
        currentLayout = layout;
        setLayoutMessage(`Layout: ${layout.name}`);
        return true;
    } catch (error) {
        console.error('Failed to load layout:', error);
        if (token === layoutLoadToken) setLayoutMessage('Error loading layout');
        return false;
    }
}

async function loadBerths(layoutId, token) {
    try {
        const response = await fetch(`${API_BASE}/berths/${encodeURIComponent(layoutId)}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (token === layoutLoadToken) berths = Array.isArray(data) ? data : [];
    } catch (error) {
        console.error('Failed to load berths:', error);
        if (token === layoutLoadToken) berths = [];
    }
}

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
        } else if (event.shiftKey) {
            event.preventDefault();
            const k = worldPerPixel();
            // Browsers often convert Shift+wheel into deltaX already
            const d = event.deltaX || event.deltaY;
            view.x += d * k;
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

function schematicBase() {
    const svg = document.getElementById('schematic');
    return { w: svg.clientWidth || 800, h: svg.clientHeight || 360 };
}

function schematicBounds() {
    if (!berths.length) return { x: 0, y: 0, w: schematicBase().w, h: schematicBase().h };
    const xs = berths.flatMap(b => [Number(b.x), Number(b.x) + Number(b.width)]);
    const ys = berths.flatMap(b => [Number(b.y), Number(b.y) + Number(b.height)]);
    const x = Math.min(0, ...xs), y = Math.min(0, ...ys);
    return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}

const schematicView = setupPanZoom(document.getElementById('schematic'), {
    getBase: schematicBase,
    getBounds: schematicBounds,
    isBackground: () => true,
    onChange: (scale) => {
        document.getElementById('zoomLevel').textContent = `${Math.round(scale * 100)}%`;
    },
});
document.getElementById('zoomIn').addEventListener('click', () => schematicView.zoomBy(1.25));
document.getElementById('zoomOut').addEventListener('click', () => schematicView.zoomBy(0.8));
document.getElementById('zoomFit').addEventListener('click', () => schematicView.fit());
document.getElementById('zoomReset').addEventListener('click', () => schematicView.reset());

function resetSchematicView() {
    const base = schematicBase();
    const b = schematicBounds();
    if (b.x + b.w > base.w || b.y + b.h > base.h) {
        schematicView.fit();
    } else {
        schematicView.reset();
    }
}

function renderSchematic() {
    const svg = document.getElementById('schematic');
    svg.innerHTML = '';

    // Add title
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    title.setAttribute('x', '0');
    title.setAttribute('y', '0');
    title.setAttribute('fill', COLORS.title);
    title.setAttribute('font-size', FONT.titleSize);
    title.setAttribute('font-weight', FONT.titleWeight);
    title.textContent = currentLayout ? currentLayout.name : 'No layout selected';
    svg.appendChild(title);

    //console.log(berths);

    // Render berths
    berths.forEach(berth => {
        renderBerth(berth);
    });
}

function clearSchematicState() {
    trainPositions = {};
    berths.forEach(berth => {
        const group = document.getElementById(`berth-${berth.id}`);
        if (!group) return;

        const rect = group.querySelector('rect');
        if (!rect) return;
        rect.setAttribute('fill', COLORS.berthFill);
        rect.setAttribute('stroke', COLORS.berthStroke);
        group.classList.remove('occupied');

        const headcodeElem = group.querySelector('.headcode');
        if (!headcodeElem) return;
        headcodeElem.textContent = '';

    });
    document.querySelectorAll('[id^="train-"]').forEach(marker => marker.remove());
}

function renderBerth(berth) {
    const svg = document.getElementById('schematic');

    // Create group for berth
    const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    group.id = `berth-${berth.id}`;

    // Berth rectangle
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', berth.x);
    rect.setAttribute('y', berth.y);
    rect.setAttribute('width', berth.width);
    rect.setAttribute('height', berth.height);
    rect.setAttribute('fill', COLORS.berthFill);
    rect.setAttribute('stroke', COLORS.berthStroke);
    rect.setAttribute('stroke-width', STROKE.thin);
    rect.setAttribute('rx', '4');
    rect.classList.add('berth-rect');

    // Berth label
    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('class', 'berth-label');
    text.setAttribute('x', berth.x + berth.width / 2);
    text.setAttribute('y', berth.y + berth.height / 2 - 2);
    text.setAttribute('text-anchor', FONT.anchorMiddle);
    text.setAttribute('fill', COLORS.berthLabel);
    text.setAttribute('font-size', FONT.berthSize);
    text.setAttribute('font-weight', FONT.berthWeight);
    text.textContent = berth.name;

    // Headcode
    const headcode = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    headcode.setAttribute('class', 'headcode');
    headcode.setAttribute('x', berth.x + berth.width / 2);
    headcode.setAttribute('y', berth.y + berth.height / 2 + 10);
    headcode.setAttribute('text-anchor', FONT.anchorMiddle);
    headcode.setAttribute('fill', COLORS.headcodeLabel);
    headcode.setAttribute('font-size', FONT.headcodeSize);
    headcode.setAttribute('font-weight', FONT.headcodeWeight);
    headcode.textContent = '';

    group.appendChild(rect);
    group.appendChild(text);
    group.appendChild(headcode);
    svg.appendChild(group);
}

function updateBerthOccupancy(berthId, occupied, trainId = null) {

    const berthElemID = `berth-${currentLayout.id}:${berthId}`;
    const group = document.getElementById(berthElemID);

    if (!group) return;

    //console.log(berthElemID, occupied, trainId, group);

    const rect = group.querySelector('rect');
    const headcodeElem = group.querySelector('.headcode');

    if (occupied) {

        group.classList.add('occupied');

        rect.setAttribute('fill', COLORS.occupiedFill);
        rect.setAttribute('stroke', COLORS.occupiedStroke);

        // Add or update train marker
        if (trainId) {
            trainPositions[trainId] = berthId;
            updateTrainMarker(trainId, berthId);
            headcodeElem.textContent = trainId.split(':')[1];
        }

    } else {

        //console.log(berthElemID, occupied, trainId, group);

        group.classList.remove('occupied');

        rect.setAttribute('fill', COLORS.berthFill);
        rect.setAttribute('stroke', COLORS.berthStroke);

        headcodeElem.textContent = '';

        // Remove train marker if present
        if (trainId && trainPositions[trainId] === berthId) {
            removeTrainMarker(trainId);
            delete trainPositions[trainId];
        }
    }

    //console.log( trainPositions );

}

function renderSnapshot(snapshot) {

    clearSchematicState();

    const trains = Array.isArray(snapshot.trains) ? snapshot.trains : [];

    trains.forEach(train => {
        //const berthId = train.current_berth || train.to_berth || train.from_berth;
        const berthId = train.current_berth || train.to_berth;

        //console.log('renderSnapshot', train.current_berth, train.to_berth, train.from_berth);

        //console.log(train.marker_id, train.train_id, train.td_area, train.headcode);

        const trainId = train.marker_id || train.train_id || `${train.td_area || ''}:${train.headcode || ''}`;

        //console.log('trainId', trainId);

        if (berthId && trainId) {
            updateBerthOccupancy(berthId, true, trainId);
        }

    });

    if (Array.isArray(snapshot.events)) {
        const log = document.getElementById('eventLog');
        log.innerHTML = '';
        snapshot.events.slice(0, 20).forEach(event => {
            log.appendChild(renderEventItem(event));
        });
    }

    const layoutInfo = document.getElementById('layoutInfo');
    const source = snapshot.source || 'unknown';
    const trainCount = trains.length;
    layoutInfo.textContent = `Layout: ${currentLayout ? currentLayout.name : 'none'} • Source: ${source} • Trains: ${trainCount}`;
}

function renderEventItem(event) {
    const item = document.createElement('div');
    item.className = 'event-item';
    const timestamp = event.display_ts || event.ts_iso || event.ts || '';
    const label = event.event_type || event.msg_type || 'event';
    const berth = event.object_id || event.berth_id || event.to_berth || event.from_berth || 'N/A';
    const trainRef = event.train_id || event.headcode || event.train_ref || 'N/A';
    const time = timestamp ? new Date(timestamp).toLocaleTimeString() : '';
    item.textContent = `[${time}] ${String(label).toUpperCase()} - ${berth} (Train: ${trainRef})`;
    return item;
}

function svgEl(name, attrs, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
    if (text !== undefined) node.textContent = text;
    return node;
}

function renderLayoutObject(obj) {
    const occupied = Boolean(obj.occupied);
    const group = svgEl('g', { id: `object-${obj.id}`, class: `layout-object${occupied ? ' occupied' : ''}` });
    const w = Number(obj.width), h = Number(obj.height), x = Number(obj.x), y = Number(obj.y);
    const accent = occupied ? COLORS.occupiedStroke : COLORS.accentFree;
    const fill = occupied ? COLORS.occupiedFill : COLORS.fillFree;
    let caption = obj.name;
    if (obj.object_type === 'level_crossing') {
        const rot = Number(obj.rotation) || 0;
        const len = Math.max(1, h);
        const d = Math.round(len * Math.SQRT1_2);
        const [x2, y2] = rot === 90 ? [x + len, y] : rot === 45 ? [x + d, y + d] : [x, y + len];
        group.appendChild(svgEl('line', {
            x1: x, y1: y, x2, y2, stroke: occupied ? COLORS.occupiedStroke : COLORS.crossingFree,
            'stroke-width': obj.variant === 'foot-crossing' ? STROKE.footCrossingWidth : STROKE.crossingWidth,
            'stroke-dasharray': obj.variant === 'foot-crossing' ? STROKE.footCrossingDash : (obj.variant === 'user_worked' ? STROKE.userWorkedDash : STROKE.noDash),
        }));
        group.appendChild(svgEl('text', { x: x + 8, y: y - 2, fill: COLORS.crossingCaption, 'font-size': FONT.crossingCaptionSize }, caption));
    } else if (obj.object_type === 'label') {
        if (obj.variant !== 'text') {
            group.appendChild(svgEl('rect', { x, y, width: Math.max(30, caption.length * 8 + 16), height: 20, rx: 3, fill, stroke: accent }));
        }
        group.appendChild(svgEl('text', { x: x + 8, y: y + 14, fill: occupied ? COLORS.crossingTextOccupied : COLORS.crossingTextFree, 'font-size': FONT.crossingTextSize }, caption));
    } else {
        const bridgeRot = obj.object_type === 'bridge' ? Number(obj.rotation) || 0 : 0;
        if (bridgeRot === 90) group.setAttribute('transform', `translate(${x + h} ${y}) rotate(90) translate(${-x} ${-y})`);
        else if (bridgeRot === 45) group.setAttribute('transform', `translate(${x + h * Math.SQRT1_2} ${y}) rotate(45) translate(${-x} ${-y})`);
        group.appendChild(svgEl('rect', { x, y, width: w, height: h, rx: 3, fill, stroke: accent, 'stroke-width': STROKE.thin }));
        group.appendChild(svgEl('text', { x: x + w / 2, y: y + h / 2 + 4, 'text-anchor': FONT.anchorMiddle, fill: COLORS.blockLabel, 'font-size': FONT.blockSize }, caption));
    }
    return group;
}

function renderLayoutObjects(items) {
    const svg = document.getElementById('schematic');
    svg.querySelectorAll('.layout-object').forEach(node => node.remove());
    (Array.isArray(items) ? [...items] : []).sort((p, q) => (Number(p.z_index) || 0) - (Number(q.z_index) || 0))
        .forEach(obj => svg.appendChild(renderLayoutObject(obj)));
}

async function refreshObjects(params, token) {
    try {
        const url = new URL(`${API_BASE}/objects/${encodeURIComponent(currentLayout.id)}`);
        ['td_area', 'headcode'].forEach(k => { if (params.has(k)) url.searchParams.set(k, params.get(k)); });
        const response = await fetch(url.toString());
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const items = await response.json();
        if (token === layoutLoadToken) renderLayoutObjects(items);
    } catch (error) {
        console.error('Failed to refresh layout objects:', error);
    }
}

async function refreshState() {
    if (!currentLayout) return;
    const token = layoutLoadToken;
    try {
        const params = new URLSearchParams(window.location.search);
        const url = new URL(`${API_BASE}/state`);
        const area = params.get('area');
        const hc = params.get('hc');
        if (area) url.searchParams.set('td_area', area);
        if (hc) url.searchParams.set('headcode', hc);

        const response = await fetch(url.toString());
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const snapshot = await response.json();
        if (token !== layoutLoadToken) return;
        renderSnapshot(snapshot);
        await refreshObjects(new URLSearchParams({
            ...(area ? { td_area: area } : {}), ...(hc ? { headcode: hc } : {}),
        }), token);
    } catch (error) {
        console.error('Failed to refresh state:', error);
    }
}

function startStatePolling() {
    if (statePollTimer) {
        clearInterval(statePollTimer);
    }
    statePollTimer = setInterval(refreshState, STATE_REFRESH_MS);
}

function updateTrainMarker(trainId, berthId) {

    const berth = berths.find(b => b.id === currentLayout.id + ':' + berthId);

    //console.log(trainId, berthId, berth, berths);

    if (!berth) return;

    const svg = document.getElementById('schematic');

    // Remove old marker if exists
    removeTrainMarker(trainId);

    // Create new marker
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    marker.id = `train-${trainId}`;
    marker.setAttribute('cx', berth.x + 2);
    marker.setAttribute('cy', berth.y - 2);
    marker.setAttribute('r', '4');
    marker.setAttribute('fill', COLORS.markerFill);
    marker.setAttribute('stroke', COLORS.markerStroke);
    marker.setAttribute('stroke-width', STROKE.markerWidth);
    marker.style.filter = MARKER_GLOW;

    //svg.appendChild(marker);
}

function removeTrainMarker(trainId) {
    const marker = document.getElementById(`train-${trainId}`);
    if (marker) {
        marker.remove();
    }
}

function connectWebSocket() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log('WebSocket connected');
        updateConnectionStatus(true);

        // Send periodic ping
        setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send('ping');
            }
        }, 25000);
    };

    ws.onmessage = (event) => {
        // Handle text responses (like "pong")
        if (typeof event.data === 'string' && event.data === 'pong') {
            //console.log('string or pong');
            return;
        }

        try {
            const message = JSON.parse(event.data);
            handleWebSocketMessage(message);
        } catch (error) {
            console.error('Failed to parse WebSocket message:', error);
        }
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        updateConnectionStatus(false);
    };

    ws.onclose = () => {
        console.log('WebSocket disconnected');
        updateConnectionStatus(false);

        // Reconnect after 5 seconds
        setTimeout(() => {
            console.log('Attempting to reconnect...');
            connectWebSocket();
        }, 5000);
    };
}

function handleWebSocketMessage(message) {
    if (message.type === 'event') {
        const event = message.data;
        logEvent(event);
        if (event.event_type === 'berth_enter') {
            updateBerthOccupancy(event.object_id, true, event.train_id);
        } else if (event.event_type === 'berth_exit') {
            updateBerthOccupancy(event.object_id, false, event.train_id);
        }
    }
}

function updateConnectionStatus(connected) {
    const indicator = document.getElementById('wsStatus');
    const text = document.getElementById('wsStatusText');

    if (connected) {
        indicator.classList.add('connected');
        indicator.classList.remove('disconnected');
        text.textContent = 'Connected';
    } else {
        indicator.classList.remove('connected');
        indicator.classList.add('disconnected');
        text.textContent = 'Disconnected';
    }
}

function logEvent(event) {
    const log = document.getElementById('eventLog');
    const item = renderEventItem(event);
    log.insertBefore(item, log.firstChild);

    // Keep only last 20 events
    while (log.children.length > 20) {
        log.removeChild(log.lastChild);
    }
}

// Initialize on page load
init();
