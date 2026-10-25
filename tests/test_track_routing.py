"""Unit tests for the deterministic track routing module (run through Node)."""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from app.visualisation import tracks as track_store

MODULE = Path(__file__).parent.parent / "web" / "static" / "track-routing.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required for JS module tests")

PRELUDE = f"""
const T = require({json.dumps(str(MODULE))});
const berth = (id, x, y, width = 60, height = 30) => ({{ id, x, y, width, height }});
const right = (b) => ({{ ...T.socketPoint(b, "right"), dir: 1 }});
const left = (b) => ({{ ...T.socketPoint(b, "left"), dir: -1 }});
const out = (value) => console.log(JSON.stringify(value));
"""


def run_js(code: str):
    result = subprocess.run(
        ["node", "-e", PRELUDE + code], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_sockets_are_midpoints_of_all_four_edges():
    data = run_js("""
        const b = berth("a", 10, 20, 60, 30);
        const odd = berth("o", 0, 0, 61, 31);
        out(["left", "right", "top", "bottom"].map((side) => T.socketPoint(b, side))
            .concat(["left", "right", "top", "bottom"].map((side) => T.socketPoint(odd, side))));
    """)
    assert data == [
        {"x": 10, "y": 35}, {"x": 70, "y": 35}, {"x": 40, "y": 20}, {"x": 40, "y": 50},
        {"x": 0, "y": 16}, {"x": 61, "y": 16}, {"x": 31, "y": 0}, {"x": 31, "y": 31},
    ]


def test_socket_coordinates_match_server_for_all_four_sides():
    berth_data = {"x": 10, "y": 20, "width": 61, "height": 31}
    sides = ["left", "right", "top", "bottom"]
    client_points = run_js("""
        const b = berth("odd", 10, 20, 61, 31);
        out(["left", "right", "top", "bottom"].map((side) => T.socketPoint(b, side)));
    """)
    assert client_points == [
        {"x": x, "y": y} for x, y in (track_store.socket_point(berth_data, side) for side in sides)
    ]


def test_routes_resolve_top_and_bottom_sockets():
    data = run_js("""
        const a = berth("a", 20, 100), b = berth("b", 200, 100);
        const state = { berths: [a, b], tracks: [], junctions: [], obstacles: [a, b] };
        const results = ["top", "bottom"].map((side) => {
          const start = { berth_id: "a", side }, end = { berth_id: "b", side };
          const points = T.routeBetween(state, start, end, 0, null);
          return {
            points,
            start: T.socketPoint(a, side),
            end: T.socketPoint(b, side),
            valid: T.isOctilinear(points),
          };
        });
        out(results);
    """)
    assert len(data) == 2
    assert all(route["valid"] for route in data)
    assert all(route["points"][0] == route["start"] and route["points"][-1] == route["end"] for route in data)


def test_top_and_bottom_tracks_reconcile_when_berths_move():
    data = run_js("""
        const a = berth("a", 20, 100), b = berth("b", 200, 100);
        const track = { id: "t", start: { berth_id: "a", side: "top" },
                        end: { berth_id: "b", side: "bottom" }, points: [] };
        const state = { berths: [a, b], tracks: [track], junctions: [], obstacles: [a, b] };
        track.points = T.routeBetween(state, track.start, track.end, 0, null);
        const initial = T.reconcile(state);
        b.x += 20;
        state.obstacles = [a, b];
        const moved = T.reconcile(state);
        const stable = T.reconcile(state);
        out({
          initial: [...initial.tracks], moved: [...moved.tracks], stable: [...stable.tracks],
          end: track.points[track.points.length - 1], expected: T.socketPoint(b, "bottom"),
          octilinear: T.isOctilinear(track.points),
        });
    """)
    assert data["initial"] == []
    assert data["moved"] == ["t"]
    assert data["stable"] == []
    assert data["end"] == data["expected"]
    assert data["octilinear"]


def test_aligned_berths_get_a_straight_route():
    points = run_js('const a = berth("a", 0, 100), b = berth("b", 200, 100); out(T.routeTrack(right(a), left(b), {}));')
    assert points == [{"x": 60, "y": 115}, {"x": 200, "y": 115}]


def test_misaligned_berths_get_constrained_routes_with_exact_endpoints():
    cases = run_js("""
        const results = [];
        const a = berth("a", 0, 100);
        const obstacles = [a];
        for (const dx of [20, 40, 100, 260, -40, -200]) {
          for (const dy of [-170, -45, -5, 0, 7, 20, 60, 175]) {
            const b = berth("b", 60 + dx, Math.max(0, 100 + dy));
            obstacles.push(b);
            const pts = T.routeTrack(right(a), left(b), { obstacles: [a, b] });
            const s = right(a), e = left(b);
            results.push({
              ok: T.isOctilinear(pts) && pts[0].x === s.x && pts[0].y === s.y &&
                  pts[pts.length - 1].x === e.x && pts[pts.length - 1].y === e.y,
              dx, dy,
            });
          }
        }
        out(results);
    """)
    assert len(cases) == 48
    assert all(case["ok"] for case in cases), [c for c in cases if not c["ok"]]


def test_route_bends_never_reverse_for_ordinary_forward_connections():
    bends = run_js("""
        const a = berth("a", 0, 100), b = berth("b", 200, 160);
        const pts = T.routeTrack(right(a), left(b), { obstacles: [a, b] });
        out({ pts, bends: pts.slice(2).map((p, i) => T.bendAngle(pts[i], pts[i + 1], p)) });
    """)
    assert max(bends["bends"]) <= 45
    assert len(bends["pts"]) >= 4  # at least stub, diagonal and run-in segments to join misaligned sockets


def test_route_avoids_obstacles_and_overlapping_tracks_when_possible():
    data = run_js("""
        const a = berth("a", 0, 100), b = berth("b", 300, 100), c = berth("c", 120, 100);
        const inside = (pts, r) => pts.some((p, i) => i > 0 && [0, .25, .5, .75, 1].some((t) => {
          const x = pts[i - 1].x + (p.x - pts[i - 1].x) * t, y = pts[i - 1].y + (p.y - pts[i - 1].y) * t;
          return x > r.x && x < r.x + r.width && y > r.y && y < r.y + r.height; }));
        const blocked = T.routeTrack(right(a), left(b), { obstacles: [a, b, c] });
        const existing = [{ x: 60, y: 115 }, { x: 300, y: 115 }];
        const shared = T.routeTrack(right(a), left(b), { obstacles: [a, b], otherRoutes: [existing] });
        out({ blockedHits: inside(blocked, c), blocked, shared });
    """)
    assert data["blockedHits"] is False
    assert any(p["y"] != 115 for p in data["blocked"])
    assert any(p["y"] != 115 for p in data["shared"])  # leaves the occupied straight line


def test_route_is_still_returned_when_every_option_overlaps():
    pts = run_js("""
        const a = berth("a", 0, 100), b = berth("b", 200, 100);
        const wall = [];
        for (let y = 0; y < 400; y += 20) wall.push(berth("w" + y, 100, y, 40, 20));
        out(T.routeTrack(right(a), left(b), { obstacles: [a, b, ...wall] }));
    """)
    assert pts[0] == {"x": 60, "y": 115} and pts[-1] == {"x": 200, "y": 115}


def test_routing_is_deterministic_and_alternatives_differ():
    data = run_js("""
        const a = berth("a", 0, 100), b = berth("b", 200, 160);
        const run = (alt) => JSON.stringify(T.routeTrack(right(a), left(b), { alt, obstacles: [a, b] }));
        out({ same: run(0) === run(0), differs: run(0) !== run(1), valid: [0, 1, 2, 3].every(
          (alt) => T.isOctilinear(JSON.parse(run(alt)))) });
    """)
    assert data == {"same": True, "differs": True, "valid": True}


def test_smooth_path_is_tangent_continuous_through_bends():
    path = run_js("""
        out(T.smoothPath([{ x: 0, y: 0 }, { x: 40, y: 0 }, { x: 80, y: 40 }, { x: 120, y: 40 }]));
    """)
    assert path.startswith("M 0 0 L ")
    assert path.count(" Q ") == 2
    # Each curve starts and ends on the straight segments adjoining its vertex (control = vertex).
    assert "L 28 0 Q 40 0 48.49 8.49 L 71.51 31.51 Q 80 40 92 40 L 120 40" in path


def test_junction_geometry_is_exact_on_diagonals():
    data = run_js("""
        const pts = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 40, y: 30 }, { x: 60, y: 30 }];
        out({ near: T.nearestPointOnRoute(pts, { x: 25, y: 12 }), onRoute: T.pointOnRoute(pts, { x: 25, y: 15 }),
              off: T.pointOnRoute(pts, { x: 25, y: 16 }), midOnRoute: T.pointOnRoute(pts, T.pointAtFraction(pts, 0.5)) });
    """)
    assert (data["near"]["x"], data["near"]["y"]) == (24, 14)
    assert data["onRoute"] is True and data["off"] is False
    assert data["midOnRoute"] is True


def test_nearest_point_matches_server_implementation():
    rng = random.Random(7)
    points = [(0, 0), (10, 0), (40, 30), (40, 70), (20, 90), (60, 90)]
    probes = [(rng.randint(-5, 80), rng.randint(-5, 100)) for _ in range(200)]
    js = run_js(f"""
        const pts = {json.dumps([{"x": x, "y": y} for x, y in points])};
        out({json.dumps(probes)}.map(([x, y]) => {{ const h = T.nearestPointOnRoute(pts, {{ x, y }}); return [h.x, h.y]; }}));
    """)
    for (px, py), (jx, jy) in zip(probes, js):
        assert track_store.nearest_point_on_route(points, px, py)[:2] == (jx, jy)


RECONCILE_SETUP = """
    const berths = [berth("a", 0, 100), berth("b", 300, 100), berth("c", 300, 250)];
    const obstacles = () => berths.map((b) => ({ ...b }));
    const state = { berths, tracks: [], junctions: [], obstacles: obstacles() };
    const t1 = { id: "t1", start: { berth_id: "a", side: "right" }, end: { berth_id: "b", side: "left" }, points: [] };
    state.tracks.push(t1);
    t1.points = T.routeBetween(state, t1.start, t1.end, 0, "t1");
    const j1 = { id: "j1", track_id: "t1", ...T.pointAtFraction(t1.points, 0.9) };
    state.junctions.push(j1);
    const t2 = { id: "t2", start: { berth_id: "c", side: "left" }, end: { junction_id: "j1" }, points: [] };
    state.tracks.push(t2);
    t2.points = T.routeBetween(state, t2.start, t2.end, 0, "t2");
"""


def test_junction_attached_track_joins_host_and_stays_coherent_when_berths_move():
    data = run_js(RECONCILE_SETUP + """
        const first = { t2: T.isOctilinear(t2.points), joined: T.pointOnRoute(t1.points, t2.points[t2.points.length - 1]),
                        idle: T.reconcile(state) };
        berths[1].y = 160;
        state.obstacles = obstacles();
        const changed = T.reconcile(state);
        const e1 = T.socketPoint(berths[1], "left");
        const last = t1.points[t1.points.length - 1];
        const again = T.reconcile(state);
        out({
          first: { t2: first.t2, joined: first.joined, idle: [...first.idle.tracks] },
          changed: [...changed.tracks].sort(), movedJunction: [...changed.junctions],
          hostEnd: last.x === e1.x && last.y === e1.y,
          junctionOnHost: T.pointOnRoute(t1.points, j1),
          branchEndsAtJunction: t2.points[t2.points.length - 1].x === j1.x && t2.points[t2.points.length - 1].y === j1.y,
          octilinear: T.isOctilinear(t1.points) && T.isOctilinear(t2.points),
          stable: [...again.tracks].length + [...again.junctions].length,
        });
    """)
    assert data["first"] == {"t2": True, "joined": True, "idle": []}
    assert data["changed"] == ["t1", "t2"]
    assert data["hostEnd"] and data["junctionOnHost"] and data["branchEndsAtJunction"] and data["octilinear"]
    assert data["stable"] == 0


def test_reconcile_skips_tracks_with_missing_endpoints_and_cascade_delete_follows_junctions():
    data = run_js(RECONCILE_SETUP + """
        state.berths = berths.filter((b) => b.id !== "c");
        const changed = T.reconcile(state);
        const removal = T.cascadeDelete(state.tracks, state.junctions, ["t1"]);
        const none = T.cascadeDelete(state.tracks, state.junctions, ["t2"]);
        out({ changed: [...changed.tracks], removed: [[...removal.tracks].sort(), [...removal.junctions]],
              none: [[...none.tracks], [...none.junctions]] });
    """)
    assert data["changed"] == []
    assert data["removed"] == [["t1", "t2"], ["j1"]]
    assert data["none"] == [["t2"], []]
