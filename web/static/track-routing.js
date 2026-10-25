/*
 * Deterministic track geometry for the visualisation layout editor.
 *
 * Routes are polylines whose segments are horizontal, vertical or 45 degrees only. Endpoints are
 * berth sockets (midpoint of any berth edge) or junctions on another track's route.
 * The module has no DOM dependencies so it can be unit tested in Node (see tests/test_tracks.py).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TrackRouting = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STUB_OPTIONS = [10, 5, 0];
  const LANE_OFFSETS = [20, 40, 80];
  const CORNER_RADIUS = 12;
  const OBSTACLE_INSET = 1;
  const OBSTACLE_SAMPLE_COST = 20;
  const OVERLAP_COST_PER_UNIT = 4;
  const CROSSING_COST = 40;
  const STUB_SHORTFALL_COST = 2;
  const BEND_COST = { 0: 0, 45: 6, 90: 25, 135: 1500, 180: 3000 };
  const MAX_ALTERNATIVES = 8;
  const THROUGH_BERTH_COST = 200;

  const sign = (v) => (v > 0) - (v < 0);
  const samePoint = (a, b) => a.x === b.x && a.y === b.y;
  const P = (x, y) => ({ x, y });

  function socketPoint(berth, side) {
    const x = Number(berth.x);
    const y = Number(berth.y);
    const width = Number(berth.width);
    const height = Number(berth.height);
    const middleX = x + Math.floor((width + 1) / 2);
    const middleY = y + Math.floor((height + 1) / 2);
    if (side === "left") return { x, y: middleY };
    if (side === "right") return { x: x + width, y: middleY };
    if (side === "top") return { x: middleX, y };
    if (side === "bottom") return { x: middleX, y: y + height };
    throw new RangeError(`Unknown berth socket side: ${side}`);
  }

  function socketDirection(side) {
    if (side === "left") return { x: -1, y: 0 };
    if (side === "right") return { x: 1, y: 0 };
    if (side === "top") return { x: 0, y: -1 };
    if (side === "bottom") return { x: 0, y: 1 };
    throw new RangeError(`Unknown berth socket side: ${side}`);
  }

  function isOctilinear(points) {
    for (let i = 1; i < points.length; i += 1) {
      const dx = points[i].x - points[i - 1].x;
      const dy = points[i].y - points[i - 1].y;
      if (dx === 0 && dy === 0) return false;
      if (dx !== 0 && dy !== 0 && Math.abs(dx) !== Math.abs(dy)) return false;
    }
    return points.length >= 2;
  }

  function direction(a, b) {
    return { x: sign(b.x - a.x), y: sign(b.y - a.y) };
  }

  /** Drop duplicate points and merge consecutive collinear same-direction segments. */
  function normalizePoints(points) {
    const unique = [];
    points.forEach((p) => {
      if (!unique.length || !samePoint(unique[unique.length - 1], p)) unique.push(P(p.x, p.y));
    });
    const out = [];
    unique.forEach((p) => {
      while (out.length >= 2) {
        const d1 = direction(out[out.length - 2], out[out.length - 1]);
        const d2 = direction(out[out.length - 1], p);
        if (d1.x === d2.x && d1.y === d2.y) out.pop();
        else break;
      }
      out.push(p);
    });
    return out;
  }

  function bendAngle(a, b, c) {
    const d1 = direction(a, b);
    const d2 = direction(b, c);
    const dot = d1.x * d2.x + d1.y * d2.y;
    const cos = dot / (Math.hypot(d1.x, d1.y) * Math.hypot(d2.x, d2.y));
    return Math.round((Math.acos(Math.max(-1, Math.min(1, cos))) * 180) / Math.PI);
  }

  function pathLength(points) {
    let total = 0;
    for (let i = 1; i < points.length; i += 1) {
      total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    }
    return total;
  }

  // ---- candidate generation ------------------------------------------------------------------

  /** Minimal octilinear connections S -> E with the diagonal placed at several offsets. */
  function connect(S, E) {
    const dx = E.x - S.x;
    const dy = E.y - S.y;
    const ax = Math.abs(dx);
    const ay = Math.abs(dy);
    const sx = sign(dx);
    const sy = sign(dy);
    if (dx === 0 || dy === 0 || ax === ay) return [[S, E]];
    const horizontal = ax > ay;
    const diag = horizontal ? ay : ax;
    const flat = horizontal ? ax - ay : ay - ax;
    const results = [];
    [0, 0.25, 0.5, 0.75, 1].forEach((fraction) => {
      const run = Math.round(fraction * flat);
      const first = horizontal ? P(S.x + run * sx, S.y) : P(S.x, S.y + run * sy);
      const second = P(first.x + diag * sx, first.y + diag * sy);
      results.push([S, first, second, E]);
    });
    return results;
  }

  const transpose = (points) => points.map((p) => P(p.y, p.x));

  function laneDetours(S, E, lanesAreRows) {
    if (!lanesAreRows) {
      return laneDetours(P(S.y, S.x), P(E.y, E.x), true).map(transpose);
    }
    const dir = sign(E.x - S.x) || 1;
    const paths = [];
    const low = Math.min(S.y, E.y);
    const high = Math.max(S.y, E.y);
    LANE_OFFSETS.forEach((offset) => {
      [low - offset, high + offset].forEach((lane) => {
        if (lane < 0) return;
        const via1 = P(S.x + dir * Math.abs(lane - S.y), lane);
        const via2 = P(E.x - dir * Math.abs(lane - E.y), lane);
        if ((via2.x - via1.x) * dir < 0) return;
        paths.push([S, via1, via2, E]);
      });
    });
    return paths;
  }

  /** Loops [v1, v2, v3] that turn a route from exit direction `dir` back the opposite way. */
  function uTurnLoops(S, dir) {
    const loops = [];
    const perpendicular = { x: -dir.y, y: dir.x };
    [-1, 1].forEach((sv) => {
      [10, 20].forEach((m) => {
        [0, 20, 40, 80].forEach((n) => {
          const v1 = P(S.x + dir.x * m + perpendicular.x * sv * m,
            S.y + dir.y * m + perpendicular.y * sv * m);
          const v2 = P(v1.x + perpendicular.x * sv * n, v1.y + perpendicular.y * sv * n);
          const v3 = P(v2.x - dir.x * m + perpendicular.x * sv * m,
            v2.y - dir.y * m + perpendicular.y * sv * m);
          if (v1.y >= 0 && v2.y >= 0 && v3.y >= 0) loops.push([v1, v2, v3]);
        });
      });
    });
    return loops;
  }

  function middlePaths(S, E, startDir, endDir) {
    const paths = [...connect(S, E), ...laneDetours(S, E, true), ...laneDetours(S, E, false)];
    const startLoops = startDir ? uTurnLoops(S, startDir) : [];
    const endLoops = endDir ? uTurnLoops(E, endDir) : [];
    startLoops.forEach((loop) => {
      connect(loop[2], E).forEach((tail) => paths.push([S, ...loop, ...tail]));
    });
    endLoops.forEach((loop) => {
      connect(S, loop[2]).forEach((head) => paths.push([...head, loop[1], loop[0], E]));
    });
    if (startDir && endDir &&
        ((E.x - S.x) * startDir.x + (E.y - S.y) * startDir.y) < 0 &&
        ((E.x - S.x) * endDir.x + (E.y - S.y) * endDir.y) > 0) {
      startLoops.forEach((a) => endLoops.forEach((b) => {
        connect(a[2], b[2]).forEach((mid) => paths.push([S, a[0], a[1], ...mid, b[1], b[0], E]));
      }));
    }
    return paths;
  }

  // ---- scoring ---------------------------------------------------------------------------------

  function prepareObstacles(obstacles) {
    return (obstacles || []).map((o) => ({
      x1: Number(o.x) + OBSTACLE_INSET,
      y1: Number(o.y) + OBSTACLE_INSET,
      x2: Number(o.x) + Number(o.width) - OBSTACLE_INSET,
      y2: Number(o.y) + Number(o.height) - OBSTACLE_INSET,
    }));
  }

  function obstacleCost(points, rects) {
    let samples = 0;
    for (let i = 1; i < points.length; i += 1) {
      const a = points[i - 1];
      const b = points[i];
      const minX = Math.min(a.x, b.x);
      const maxX = Math.max(a.x, b.x);
      const minY = Math.min(a.y, b.y);
      const maxY = Math.max(a.y, b.y);
      const steps = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 4));
      rects.forEach((r) => {
        if (maxX < r.x1 || minX > r.x2 || maxY < r.y1 || minY > r.y2) return;
        for (let s = 0; s <= steps; s += 1) {
          const x = a.x + ((b.x - a.x) * s) / steps;
          const y = a.y + ((b.y - a.y) * s) / steps;
          if (x > r.x1 && x < r.x2 && y > r.y1 && y < r.y2) samples += 1;
        }
      });
    }
    return samples * OBSTACLE_SAMPLE_COST;
  }

  const cross = (ox, oy, ax, ay, bx, by) => (ax - ox) * (by - oy) - (ay - oy) * (bx - ox);

  function segmentInteraction(a, b, c, d) {
    const d1 = direction(a, b);
    const d2 = direction(c, d);
    if (d1.x * d2.y - d1.y * d2.x === 0) {
      if (cross(a.x, a.y, b.x, b.y, c.x, c.y) !== 0) return { overlap: 0, crossing: 0 };
      const len = Math.hypot(b.x - a.x, b.y - a.y);
      const ux = (b.x - a.x) / len;
      const uy = (b.y - a.y) / len;
      const t1 = (c.x - a.x) * ux + (c.y - a.y) * uy;
      const t2 = (d.x - a.x) * ux + (d.y - a.y) * uy;
      const overlap = Math.min(len, Math.max(t1, t2)) - Math.max(0, Math.min(t1, t2));
      return { overlap: Math.max(0, overlap), crossing: 0 };
    }
    const o1 = Math.sign(cross(a.x, a.y, b.x, b.y, c.x, c.y));
    const o2 = Math.sign(cross(a.x, a.y, b.x, b.y, d.x, d.y));
    const o3 = Math.sign(cross(c.x, c.y, d.x, d.y, a.x, a.y));
    const o4 = Math.sign(cross(c.x, c.y, d.x, d.y, b.x, b.y));
    return { overlap: 0, crossing: o1 * o2 < 0 && o3 * o4 < 0 ? 1 : 0 };
  }

  function trackCost(points, others) {
    let cost = 0;
    (others || []).forEach((other) => {
      for (let i = 1; i < points.length; i += 1) {
        for (let j = 1; j < other.length; j += 1) {
          const hit = segmentInteraction(points[i - 1], points[i], other[j - 1], other[j]);
          cost += hit.overlap * OVERLAP_COST_PER_UNIT + hit.crossing * CROSSING_COST;
        }
      }
    });
    return cost;
  }

  function axisCost(a, b, axis) {
    if (axis === undefined || axis === null) return 0;
    const angle = ((Math.round((Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI) % 180) + 180) % 180;
    const diff = Math.abs(angle - axis);
    return Math.min(diff, 180 - diff) === 90 ? 15 : 0;
  }

  function scorePath(points, ctx, shortfall) {
    let cost = pathLength(points) + shortfall * STUB_SHORTFALL_COST;
    for (let i = 2; i < points.length; i += 1) {
      cost += BEND_COST[bendAngle(points[i - 2], points[i - 1], points[i])] || 0;
    }
    cost += obstacleCost(points, ctx.rects) + trackCost(points, ctx.others);
    if (ctx.start.dir && direction(points[0], points[1]).x * ctx.start.dir.x +
        direction(points[0], points[1]).y * ctx.start.dir.y < 0) cost += THROUGH_BERTH_COST;
    const n = points.length;
    if (ctx.end.dir && direction(points[n - 2], points[n - 1]).x * ctx.end.dir.x +
        direction(points[n - 2], points[n - 1]).y * ctx.end.dir.y > 0) cost += THROUGH_BERTH_COST;
    cost += axisCost(points[0], points[1], ctx.start.axis);
    cost += axisCost(points[points.length - 2], points[points.length - 1], ctx.end.axis);
    return cost;
  }

  /**
   * Ranked, de-duplicated candidate routes from `start` to `end` (best first). Endpoints are
   * {x, y, dir, axis}: `dir` (-1/1) is the horizontal exit direction of a berth socket (0/undefined
   * for a junction) and `axis` the host-track angle (0/45/90/135) to join a junction cleanly.
   * Every candidate is a valid octilinear route; costs only rank them, so a route is always
   * returned even when every option overlaps something.
   */
  function routeCandidates(start, end, options) {
    const opts = options || {};
    const asDirection = (dir) => !dir ? null : (typeof dir === "number" ? { x: dir, y: 0 } : dir);
    const ctx = {
      start: { ...start, dir: asDirection(start.dir) },
      end: { ...end, dir: asDirection(end.dir) },
      rects: prepareObstacles(opts.obstacles),
      others: opts.otherRoutes || [],
    };
    const seen = new Set();
    const ranked = [];
    const constrainCoordinates = start.x >= 0 && start.y >= 0 && end.x >= 0 && end.y >= 0;
    const stubs = ctx.start.dir || ctx.end.dir ? STUB_OPTIONS : [0];
    const maxStub = Math.max(...stubs);
    stubs.forEach((stub) => {
      const S = ctx.start.dir && stub
        ? P(start.x + ctx.start.dir.x * stub, start.y + ctx.start.dir.y * stub) : P(start.x, start.y);
      const E = ctx.end.dir && stub
        ? P(end.x + ctx.end.dir.x * stub, end.y + ctx.end.dir.y * stub) : P(end.x, end.y);
      middlePaths(S, E, stub ? ctx.start.dir : 0, stub ? ctx.end.dir : 0).forEach((middle) => {
        const points = normalizePoints([P(start.x, start.y), ...middle, P(end.x, end.y)]);
        if (points.length < 2 || !isOctilinear(points) ||
            (constrainCoordinates && points.some((point) => point.x < 0 || point.y < 0))) return;
        const key = points.map((p) => `${p.x},${p.y}`).join(";");
        if (seen.has(key)) return;
        seen.add(key);
        ranked.push({ points, cost: scorePath(points, ctx, maxStub - stub), order: ranked.length });
      });
    });
    ranked.sort((a, b) => a.cost - b.cost || a.order - b.order);
    return ranked;
  }

  function routeTrack(start, end, options) {
    const ranked = routeCandidates(start, end, options);
    const alt = Math.max(0, Number((options || {}).alt) || 0);
    return ranked[alt % Math.min(ranked.length, MAX_ALTERNATIVES)].points;
  }

  // ---- rendering ---------------------------------------------------------------------------

  /** SVG path: straight segments joined by tangent-continuous quadratic corners. */
  function smoothPath(points, radius) {
    const r = radius === undefined ? CORNER_RADIUS : radius;
    const f = (n) => String(Math.round(n * 100) / 100);
    let d = `M ${f(points[0].x)} ${f(points[0].y)}`;
    for (let i = 1; i < points.length - 1; i += 1) {
      const prev = points[i - 1];
      const v = points[i];
      const next = points[i + 1];
      const l1 = Math.hypot(v.x - prev.x, v.y - prev.y);
      const l2 = Math.hypot(next.x - v.x, next.y - v.y);
      const cut = Math.min(r, l1 / 2, l2 / 2);
      const ax = v.x - ((v.x - prev.x) / l1) * cut;
      const ay = v.y - ((v.y - prev.y) / l1) * cut;
      const bx = v.x + ((next.x - v.x) / l2) * cut;
      const by = v.y + ((next.y - v.y) / l2) * cut;
      d += ` L ${f(ax)} ${f(ay)} Q ${f(v.x)} ${f(v.y)} ${f(bx)} ${f(by)}`;
    }
    const last = points[points.length - 1];
    return `${d} L ${f(last.x)} ${f(last.y)}`;
  }

  // ---- junction geometry -------------------------------------------------------------------

  /** Exact integer point on the route nearest to p, with segment index, distance and arc offset. */
  function nearestPointOnRoute(points, p) {
    let best = null;
    let travelled = 0;
    for (let i = 1; i < points.length; i += 1) {
      const a = points[i - 1];
      const b = points[i];
      const u = direction(a, b);
      const steps = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
      const t = Math.min(steps, Math.max(0, Math.floor(((p.x - a.x) * u.x + (p.y - a.y) * u.y) / (u.x * u.x + u.y * u.y) + 0.5)));
      const q = P(a.x + t * u.x, a.y + t * u.y);
      const distance = Math.hypot(p.x - q.x, p.y - q.y);
      if (!best || distance < best.distance) {
        best = { x: q.x, y: q.y, segmentIndex: i - 1, distance, offset: travelled + t * Math.hypot(u.x, u.y) };
      }
      travelled += Math.hypot(b.x - a.x, b.y - a.y);
    }
    return best;
  }

  function pointOnRoute(points, p) {
    return nearestPointOnRoute(points, p).distance === 0;
  }

  /** Point at `fraction` (0..1) of the route's length, snapped to an exact integer route point. */
  function pointAtFraction(points, fraction) {
    const target = pathLength(points) * Math.max(0, Math.min(1, fraction));
    let travelled = 0;
    for (let i = 1; i < points.length; i += 1) {
      const a = points[i - 1];
      const b = points[i];
      const len = Math.hypot(b.x - a.x, b.y - a.y);
      if (travelled + len >= target || i === points.length - 1) {
        const u = direction(a, b);
        const steps = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
        const t = Math.min(steps, Math.max(0, Math.round(((target - travelled) / len) * steps)));
        return P(a.x + t * u.x, a.y + t * u.y);
      }
      travelled += len;
    }
    return P(points[0].x, points[0].y);
  }

  function fractionOfPoint(points, p) {
    const total = pathLength(points);
    return total ? nearestPointOnRoute(points, p).offset / total : 0;
  }

  /** Undirected angle (0/45/90/135) of the host segment a junction sits on. */
  function junctionAxis(hostPoints, p) {
    const hit = nearestPointOnRoute(hostPoints, p);
    const a = hostPoints[hit.segmentIndex];
    const b = hostPoints[hit.segmentIndex + 1];
    return ((Math.round((Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI) % 180) + 180) % 180;
  }

  // ---- layout-wide coherence ---------------------------------------------------------------

  function indexById(items) {
    return new Map((items || []).map((item) => [item.id, item]));
  }

  /** Tracks ordered so a junction's host route is always processed before tracks attached to it. */
  function orderTracks(tracks, junctions) {
    const byId = indexById(tracks);
    const junctionById = indexById(junctions);
    const ordered = [];
    const state = new Map();
    function visit(track) {
      if (state.get(track.id)) return;
      state.set(track.id, "visiting");
      [track.start, track.end].forEach((endpoint) => {
        const junction = endpoint && endpoint.junction_id ? junctionById.get(endpoint.junction_id) : null;
        const host = junction ? byId.get(junction.track_id) : null;
        if (host && state.get(host.id) !== "visiting") visit(host);
      });
      state.set(track.id, "done");
      ordered.push(track);
    }
    tracks.forEach(visit);
    return ordered;
  }

  /** Resolve a track endpoint to {x, y, dir, axis}; null if its berth/junction is missing. */
  function resolveEndpoint(endpoint, ctx) {
    if (endpoint.junction_id) {
      const junction = ctx.junctionById.get(endpoint.junction_id);
      if (!junction) return null;
      const host = ctx.trackById.get(junction.track_id);
      const axis = host && host.points.length > 1 ? junctionAxis(host.points, junction) : undefined;
      return { x: junction.x, y: junction.y, dir: 0, axis };
    }
    const berth = ctx.berthById.get(endpoint.berth_id);
    if (!berth) return null;
    return { ...socketPoint(berth, endpoint.side), dir: socketDirection(endpoint.side) };
  }

  /**
   * Make every track and junction consistent with the current berths: keep junctions on their host
   * route, and reroute any track whose endpoints no longer match its sockets/junctions. Mutates
   * `state.tracks`/`state.junctions` in place and returns the ids it changed. Idempotent.
   */
  function reconcile(state) {
    const tracks = state.tracks || [];
    const junctions = state.junctions || [];
    const ctx = buildContext(state);
    const changed = { tracks: new Set(), junctions: new Set() };
    const keepJunctionsOnRoute = (track) => {
      junctions.filter((j) => j.track_id === track.id).forEach((junction) => {
        if (!track.points.length || pointOnRoute(track.points, junction)) return;
        const hit = nearestPointOnRoute(track.points, junction);
        junction.x = hit.x;
        junction.y = hit.y;
        changed.junctions.add(junction.id);
      });
    };
    orderTracks(tracks, junctions).forEach((track) => {
      const start = resolveEndpoint(track.start, ctx);
      const end = resolveEndpoint(track.end, ctx);
      if (start && end) {
        const first = track.points[0];
        const last = track.points[track.points.length - 1];
        const attached = first && last && samePoint(first, start) && samePoint(last, end) && isOctilinear(track.points);
        if (!attached) {
          track.points = routeTrack(start, end, {
            obstacles: state.obstacles,
            otherRoutes: tracks.filter((t) => t !== track).map((t) => t.points).filter((pts) => pts.length > 1),
          });
          changed.tracks.add(track.id);
        }
      }
      keepJunctionsOnRoute(track);
    });
    return changed;
  }

  function buildContext(state) {
    return {
      berthById: indexById(state.berths),
      junctionById: indexById(state.junctions),
      trackById: indexById(state.tracks),
    };
  }

  /**
   * Route between two endpoint references ({berth_id, side} or {junction_id}). `alt` selects a
   * ranked alternative; `excludeTrackId` leaves a track's own current route out of the scoring.
   * Returns null when an endpoint cannot be resolved.
   */
  function routeBetween(state, startRef, endRef, alt, excludeTrackId) {
    const ctx = buildContext(state);
    const start = resolveEndpoint(startRef, ctx);
    const end = resolveEndpoint(endRef, ctx);
    if (!start || !end) return null;
    return routeTrack(start, end, {
      alt,
      obstacles: state.obstacles,
      otherRoutes: (state.tracks || []).filter((t) => t.id !== excludeTrackId).map((t) => t.points)
        .filter((pts) => pts.length > 1),
    });
  }

  /** Ids removed when deleting tracks: their junctions and every track attached to those junctions. */
  function cascadeDelete(tracks, junctions, trackIds) {
    const removedTracks = new Set();
    const removedJunctions = new Set();
    const queue = [...trackIds];
    while (queue.length) {
      const id = queue.pop();
      if (removedTracks.has(id)) continue;
      removedTracks.add(id);
      junctions.filter((j) => j.track_id === id).forEach((j) => {
        removedJunctions.add(j.id);
        tracks.forEach((t) => {
          if ((t.start && t.start.junction_id === j.id) || (t.end && t.end.junction_id === j.id)) queue.push(t.id);
        });
      });
    }
    return { tracks: removedTracks, junctions: removedJunctions };
  }

  return {
    socketPoint, socketDirection, isOctilinear, normalizePoints, bendAngle, pathLength,
    routeCandidates, routeTrack, smoothPath, nearestPointOnRoute, pointOnRoute, pointAtFraction,
    fractionOfPoint, junctionAxis, orderTracks, resolveEndpoint, reconcile, indexById, routeBetween,
    cascadeDelete,
  };
});
