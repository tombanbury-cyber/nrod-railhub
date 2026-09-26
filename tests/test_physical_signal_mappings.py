#!/usr/bin/env python3
"""Tests for physical signal identity review support."""

import os
import tempfile

from flask import Flask

from nrod_railhub.database import RailDB
from nrod_railhub import web


def _build_test_app(db_path: str):
    app_holder = {}
    original_flask_init = Flask.__init__

    def patched_init(self, *args, **kwargs):
        original_flask_init(self, *args, **kwargs)
        app_holder["app"] = self

    Flask.__init__ = patched_init
    try:
        import unittest.mock as mock

        with mock.patch("flask.Flask.run"):
            web.start_web_dashboard(db_path, 8088, None, None)
    finally:
        Flask.__init__ = original_flask_init

    return app_holder["app"]


def test_physical_signal_mapping_lifecycle():
    """Physical signal mappings should support create, confirm, correction, and revoke."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)

        created_id = db.add_physical_signal_mapping(
            "EK",
            "D0",
            0,
            7,
            physical_signal_number="SN123",
            physical_signal_location="Clapham Junction",
            from_berth="0152",
            to_berth="0153",
            mapping_confidence=0.62,
            correlation_confidence=0.91,
            verification_status="inferred",
            source="manual",
            reviewer="alice",
            evidence_json={"source": "cab log"},
            notes="initial candidate",
        )
        db.update_physical_signal_mapping(
            created_id,
            verification_status="confirmed",
            reviewer="bob",
            notes="reviewed against photo evidence",
        )
        corrected_id = db.add_physical_signal_mapping(
            "EK",
            "D0",
            0,
            7,
            physical_signal_number="SN124",
            physical_signal_location="Clapham Junction northbound",
            from_berth="0152",
            to_berth="0153",
            mapping_confidence=0.83,
            correlation_confidence=0.91,
            verification_status="corrected",
            source="survey",
            reviewer="carol",
            evidence_json='{"photo":"img-1"}',
            notes="corrected after site survey",
            supersedes_id=created_id,
        )
        db.revoke_physical_signal_mapping(corrected_id, reviewer="dave", notes="withdrawn")

        rows = db.get_physical_signal_mappings(td_area="EK", address="D0")
        assert len(rows) == 2
        assert {row["verification_status"] for row in rows} == {"confirmed", "revoked"}
        assert any(row["physical_signal_number"] == "SN123" for row in rows)
        assert any(row["physical_signal_number"] == "SN124" for row in rows)
        assert any(row["evidence_json"] for row in rows)
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_physical_signal_api_and_page_rendering():
    """The web API and review page should expose physical signal mappings."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)
        db.ensure_mapper_schema()
        db.add_physical_signal_mapping(
            "EK",
            "D0",
            0,
            7,
            physical_signal_number="SN123",
            physical_signal_location="Clapham Junction",
            from_berth="0152",
            to_berth="0153",
            mapping_confidence=0.74,
            correlation_confidence=0.96,
            verification_status="probable",
            source="import",
            reviewer="alice",
            evidence_json={"source": "import"},
            notes="seed record",
        )
        db.close()

        app = _build_test_app(db_path)
        client = app.test_client()

        post_response = client.post(
            "/api/physical-signal-mappings",
            json={
                "td_area": "WK",
                "address": "D1",
                "byte_offset": 0,
                "bit": 4,
                "physical_signal_number": "SN900",
                "physical_signal_location": "Woking",
                "from_berth": "1001",
                "to_berth": "1002",
                "mapping_confidence": 0.81,
                "correlation_confidence": 0.93,
                "verification_status": "inferred",
                "source": "manual",
                "reviewer": "tester",
                "evidence_json": {"evidence": "note"},
                "notes": "created through API",
            },
        )
        assert post_response.status_code == 200
        created_id = post_response.get_json()["id"]

        patch_response = client.patch(
            f"/api/physical-signal-mappings/{created_id}",
            json={"verification_status": "confirmed", "reviewer": "qa"},
        )
        assert patch_response.status_code == 200

        delete_response = client.delete(
            f"/api/physical-signal-mappings/{created_id}",
            json={"reviewer": "qa", "notes": "revoked in test"},
        )
        assert delete_response.status_code == 200

        api_response = client.get("/api/physical-signal-mappings?td_area=EK&address=D0")
        api_payload = api_response.get_json()
        assert api_payload["count"] >= 1
        assert any(item["physical_signal_number"] == "SN123" for item in api_payload["items"])

        page_response = client.get("/signal-mappings?area=EK&address=D0")
        page_text = page_response.data.decode("utf-8")
        assert "Physical Signal Identity Review" in page_text
        assert "SN123" in page_text
        assert "Confirm" in page_text
        assert "Revoke" in page_text
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
