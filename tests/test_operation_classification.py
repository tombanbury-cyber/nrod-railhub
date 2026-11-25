"""Tests for conservative operation classification and its database persistence."""

import sqlite3

import pytest

from nrod_railhub.database import RailDB
from nrod_railhub.interesting import classify_train_operation


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("EE", "ECS"),
        ("H1", "Freight"),
        ("OL", "Passenger"),
    ],
)
def test_cif_category_classification_is_high_confidence(category, expected):
    result = classify_train_operation(train_category=category)

    assert result == {
        "operation_type": expected,
        "operation_source": "CIF train category",
        "operation_confidence": "high",
        "operation_evidence": f"CIF category {category}",
    }


def test_schedule_metadata_classifies_rhtt_with_high_confidence():
    result = classify_train_operation(train_service_code="RHTT service")

    assert result["operation_type"] == "RHTT"
    assert result["operation_source"] == "Schedule metadata"
    assert result["operation_confidence"] == "high"
    assert "RHTT" in result["operation_evidence"]


@pytest.mark.parametrize(
    ("headcode", "expected"),
    [
        ("0Q01", "Test / monitoring"),
        ("3S21", "Possible RHTT / engineering"),
    ],
)
def test_headcode_patterns_remain_explicitly_low_confidence(headcode, expected):
    result = classify_train_operation(headcode=headcode)

    assert result["operation_type"] == expected
    assert result["operation_source"] == "Headcode pattern"
    assert result["operation_confidence"] == "low"
    assert result["operation_evidence"]


def test_missing_evidence_is_unknown():
    assert classify_train_operation(headcode="2C90") == {
        "operation_type": "Unknown",
        "operation_source": "Insufficient evidence",
        "operation_confidence": "none",
        "operation_evidence": "",
    }


def test_legacy_td_state_migration_and_operation_persistence(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE td_state (
            td_area TEXT NOT NULL,
            headcode TEXT NOT NULL,
            last_time_ms INTEGER NOT NULL,
            last_time_iso TEXT,
            from_berth TEXT,
            to_berth TEXT,
            stanox TEXT,
            location_name TEXT,
            platform TEXT,
            sched_dep TEXT,
            sched_arr TEXT,
            origin_name TEXT,
            dest_name TEXT,
            uid TEXT,
            PRIMARY KEY (td_area, headcode)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO td_state (
            td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth, uid
        ) VALUES ('EK', '2C90', 1000, '2026-01-01T00:00:01Z', '0152', '0153', 'C12345')
        """
    )
    conn.commit()
    conn.close()

    db = RailDB(str(db_path), enable_mapper=False)
    try:
        columns = set(db._conn.table_columns("td_state"))
        assert {
            "CIF_train_category",
            "CIF_power_type",
            "CIF_train_service_code",
            "operation_type",
            "operation_source",
            "operation_confidence",
            "operation_evidence",
        } <= columns

        db.upsert_td_state(
            "EK",
            "2C90",
            2000,
            "2026-01-01T00:00:02Z",
            "0153",
            "0154",
            uid="C12345",
            cif_train_category="H1",
            cif_power_type="DMU",
            cif_train_service_code="12345678",
            operation_type="Freight",
            operation_source="CIF train category",
            operation_confidence="high",
            operation_evidence="CIF category H1",
        )
    finally:
        db.close()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT last_time_ms, uid, CIF_train_category, CIF_power_type,
                   CIF_train_service_code, operation_type, operation_source,
                   operation_confidence, operation_evidence
            FROM td_state WHERE td_area='EK' AND headcode='2C90'
            """
        ).fetchone()
    finally:
        conn.close()

    assert row == (
        2000,
        "C12345",
        "H1",
        "DMU",
        "12345678",
        "Freight",
        "CIF train category",
        "high",
        "CIF category H1",
    )
