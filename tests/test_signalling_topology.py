"""Tests for evidence-backed signalling topology reconstruction."""

import os
import tempfile
from unittest import mock

from flask import Flask

from nrod_railhub import web
from nrod_railhub.database import RailDB


def _new_db():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    return handle.name, RailDB(handle.name, enable_mapper=False)


def test_rebuild_keeps_branching_routes_and_evidence_idempotently():
    db_path, db = _new_db()
    try:
        for movement_id, ts_ms, headcode, origin, destination in (
            (1, 1000, "2C90", "0152", "0153"),
            (2, 2000, "2C90", "0152", "0154"),
        ):
            db._conn.execute(
                """
                INSERT INTO td_berth_movements(
                    id, ts_ms, ts_iso, td_area, headcode, from_berth, to_berth,
                    source_msg_type
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (movement_id, ts_ms, f"2026-01-01T00:00:0{movement_id}Z",
                 "EK", headcode, origin, destination, "CC"),
            )
        db._conn.execute(
            """
            INSERT INTO td_sclass_movement_scores(
                td_area, from_berth, to_berth, observation_count, matching_count,
                correlation_pct, associated_bits_json, last_seen_ts_ms, last_seen_iso
            ) VALUES ('EK','0152','0153',4,3,0.75,'["D0.0"]',1000,'2026-01-01T00:00:01Z')
            """
        )
        db._conn.execute(
            """
            INSERT INTO td_sclass_movement_scores(
                td_area, from_berth, to_berth, observation_count, matching_count,
                correlation_pct, associated_bits_json, last_seen_ts_ms, last_seen_iso
            ) VALUES ('EK','0152','0154',2,1,0.5,'["D1.1"]',2000,'2026-01-01T00:00:02Z')
            """
        )
        db.add_physical_signal_mapping(
            "EK", "D0", 0, 0, physical_signal_number="SN1",
            from_berth="0152", to_berth="0153", mapping_confidence=0.9,
            verification_status="confirmed", source="survey", evidence_json={"photo": "p1"},
        )
        db.add_physical_signal_mapping(
            "EK", "D1", 0, 1, physical_signal_number="SN2",
            from_berth="0152", to_berth="0154", mapping_confidence=0.8,
            verification_status="inferred", source="review", evidence_json={"note": "candidate"},
        )

        first_summary = db.rebuild_signalling_topology("EK")
        first = db.get_signalling_topology(td_area="EK")
        second_summary = db.rebuild_signalling_topology("EK")
        second = db.get_signalling_topology(td_area="EK")

        start_id = "EK:berth:0152"
        outgoing = [edge for edge in second["edges"] if edge["from"] == start_id
                    and edge["relationship"] == "uses_route"]
        assert {second_edge["to"] for second_edge in outgoing} == {
            "EK:route:0152>0153", "EK:route:0152>0154"
        }
        assert first_summary["candidate_edges"] == second_summary["candidate_edges"]
        assert first == second
        assert all(edge["verification_status"] == "inferred" for edge in second["edges"])
        assert any(evidence["source_type"] == "td_berth_movement"
                   for edge in second["edges"] for evidence in edge["evidence"])
        assert any(evidence["source_type"] == "sclass_correlation"
                   for edge in second["edges"] for evidence in edge["evidence"])
        assert any(evidence["source_type"] == "physical_signal_mapping"
                   for edge in second["edges"] for evidence in edge["evidence"])

        confirmed_edge = next(
            edge["id"] for edge in second["edges"]
            if edge["relationship"] == "uses_route" and edge["to"] == "EK:route:0152>0153"
        )
        db.set_topology_edge_status(confirmed_edge, "confirmed", reviewer="reviewer")
        db.rebuild_signalling_topology("EK")
        confirmed_graph = db.get_signalling_topology(td_area="EK")
        assert next(edge for edge in confirmed_graph["edges"] if edge["id"] == confirmed_edge)[
            "verification_status"
        ] == "confirmed"
    finally:
        db.close()
        os.unlink(db_path)


def test_topology_keeps_conflicting_hypotheses_and_review_statuses_separate():
    db_path, db = _new_db()
    try:
        origin = db.add_topology_node("EK", "points", "P1", "Points P1")
        normal_route = db.add_topology_node("EK", "route", "normal", "Normal route")
        reverse_route = db.add_topology_node("EK", "route", "reverse", "Reverse route")
        normal = db.add_topology_edge(
            "EK", origin, "sets_route", normal_route, 0.7,
            hypothesis="normal-position", source_type="survey", source_id="obs-1",
            evidence={"position": "normal"},
        )
        alternate = db.add_topology_edge(
            "EK", origin, "sets_route", reverse_route, 0.6,
            hypothesis="reverse-position", source_type="survey", source_id="obs-2",
            evidence={"position": "reverse"},
        )
        db.set_topology_edge_status(normal, "confirmed", reviewer="reviewer")

        topology = db.get_signalling_topology(td_area="EK", start_node_id=origin, max_depth=1)
        edges = {edge["id"]: edge for edge in topology["edges"]}

        assert set(edges) == {normal, alternate}
        assert edges[normal]["verification_status"] == "confirmed"
        assert edges[alternate]["verification_status"] == "inferred"
        assert edges[normal]["hypothesis"] == "normal-position"
        assert edges[alternate]["evidence"][0]["details"] == {"position": "reverse"}
    finally:
        db.close()
        os.unlink(db_path)


def test_manual_topology_can_represent_incomplete_and_extended_paths():
    db_path, db = _new_db()
    try:
        berth = db.add_topology_node("EK", "berth", "0152", "0152")
        signal = db.add_topology_node("EK", "signal", "SN1", "Signal SN1")
        route = db.add_topology_node("EK", "route", "R1", "Route R1")
        points = db.add_topology_node("EK", "points", "P1", "Points P1")
        track = db.add_topology_node("EK", "track_section", "T1", "Track T1")
        next_berth = db.add_topology_node("EK", "berth", "0153", "0153")
        db.add_topology_edge("EK", berth, "approach_signal", signal, 1.0)
        db.add_topology_edge("EK", signal, "protects_route", route, 0.9)
        db.add_topology_edge("EK", route, "sets_points", points, 0.8)
        db.add_topology_edge("EK", points, "covers_track", track, 0.8)
        db.add_topology_edge("EK", track, "leads_to", next_berth, 1.0)
        isolated = db.add_topology_node("EK", "track_section", "UNKNOWN", "Unmapped section")
        foreign = db.add_topology_node("WK", "berth", "1001", "Other area")

        graph = db.get_signalling_topology(td_area="EK", start_node_id=berth, max_depth=5)
        all_nodes = db.get_signalling_topology(td_area="EK")
        wrong_area = db.get_signalling_topology(
            td_area="EK", start_node_id=foreign, max_depth=5
        )

        assert [edge["from"] for edge in graph["edges"][:1]] == [berth]
        assert len(graph["edges"]) == 5
        assert isolated in {node["id"] for node in all_nodes["nodes"]}
        assert isolated not in {node["id"] for node in graph["nodes"]}
        assert wrong_area["nodes"] == []
        assert wrong_area["edges"] == []
    finally:
        db.close()
        os.unlink(db_path)


def test_topology_api_returns_graph_and_accepts_rebuild_request():
    db_path, db = _new_db()
    origin = db.add_topology_node("EK", "berth", "0152", "0152")
    destination = db.add_topology_node("EK", "route", "R1", "Route R1")
    edge_id = db.add_topology_edge("EK", origin, "uses_route", destination, 0.8)
    db.close()
    app_holder = {}
    original_init = Flask.__init__

    def capture_app(instance, *args, **kwargs):
        original_init(instance, *args, **kwargs)
        app_holder["app"] = instance

    Flask.__init__ = capture_app
    try:
        with mock.patch("flask.Flask.run"):
            web.start_web_dashboard(db_path, 8088, None, None)
    finally:
        Flask.__init__ = original_init

    try:
        client = app_holder["app"].test_client()
        rebuild = client.post("/api/signalling-topology/rebuild", json={"area": "EK"})
        response = client.get("/api/signalling-topology?area=EK")
        invalid_rebuild = client.post("/api/signalling-topology/rebuild", json=[])
        reviewed = client.patch(
            f"/api/signalling-topology/edges/{edge_id}",
            json={"verification_status": "confirmed", "reviewer": "qa"},
        )
        confirmed = client.get("/api/signalling-topology?area=EK&status=confirmed")

        assert rebuild.status_code == 200
        assert rebuild.get_json()["status"] == "ok"
        assert response.status_code == 200
        assert invalid_rebuild.status_code == 400
        assert reviewed.status_code == 200
        assert confirmed.get_json()["edges"][0]["verification_status"] == "confirmed"
    finally:
        os.unlink(db_path)
