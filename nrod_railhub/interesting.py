#!/usr/bin/env python3
"""Helpers for identifying interesting trains."""

from __future__ import annotations

from typing import Optional


def classify_train_operation(
    headcode: str = "",
    train_category: str = "",
    power_type: str = "",
    train_service_code: str = "",
) -> dict[str, str]:
    """Classify a train operation conservatively and explain the evidence used."""
    category = (train_category or "").strip().upper()
    hc = (headcode or "").strip().upper()
    service_code = (train_service_code or "").strip().upper()

    def result(operation: str, source: str, confidence: str, evidence: str) -> dict[str, str]:
        return {
            "operation_type": operation,
            "operation_source": source,
            "operation_confidence": confidence,
            "operation_evidence": evidence,
        }

    if category in {"EE", "EL", "ES"}:
        return result("ECS", "CIF train category", "high", f"CIF category {category}")
    if category.startswith(("H", "J")):
        return result("Freight", "CIF train category", "high", f"CIF category {category}")
    if category.startswith("X") or category in {"OL", "OO", "OS", "OU", "OW"}:
        return result("Passenger", "CIF train category", "high", f"CIF category {category}")

    searchable = " ".join((category, service_code, (power_type or "").strip().upper()))
    if "RHTT" in searchable:
        return result("RHTT", "Schedule metadata", "high", "RHTT appears in schedule metadata")
    if any(term in searchable for term in ("TEST", "MEASUREMENT", "MONITORING")):
        return result("Test / monitoring", "Schedule metadata", "high", "Test or monitoring appears in schedule metadata")
    if hc.startswith(("0Q", "1Q", "2Q")):
        return result("Test / monitoring", "Headcode pattern", "low", f"Headcode {hc} uses a Q test-train prefix")
    if hc.startswith(("3S", "3Y")):
        return result("Possible RHTT / engineering", "Headcode pattern", "low", f"Headcode {hc} is an engineering-service clue")

    return result("Unknown", "Insufficient evidence", "none", "")


def classify_interesting_train(
    headcode: str = "",
    train_category: str = "",
    power_type: str = "",
    description: str = "",
) -> Optional[str]:
    """Return the interesting-train bucket for a service, if any."""
    hc = (headcode or "").strip().upper()
    power = (power_type or "").strip().upper()
    text = " ".join(
        part.strip().upper()
        for part in (headcode, train_category, power_type, description)
        if part and part.strip()
    )

    if "STEAM" in text or power.startswith("S"):
        return "Steam"
    if any(keyword in text for keyword in ("TRACK", "TAMPER", "BALLAST", "GRINDER", "PLASSER", "MEASUR")) or power.startswith("T"):
        return "Track Equipment"
    if any(keyword in text for keyword in ("ECS", "EMPTY COACH", "EMPTY STOCK")) or hc.startswith(("5Z", "5E", "0Z", "0E")):
        return "ECS"
    if any(keyword in text for keyword in ("SPECIAL", "EXCURSION", "CHARTER", "RAILTOUR", "TOUR")) or (len(hc) >= 2 and hc[1] == "Z"):
        return "Specials"
    if any(keyword in text for keyword in ("DIESEL", "DMU", "DEMU", "DPU")) or power.startswith("D"):
        return "Diesel"
    return None


def format_location(location_name: str = "", stanox: str = "", platform: str = "") -> str:
    """Render a readable location string."""
    location_name = (location_name or "").strip()
    stanox = (stanox or "").strip()
    platform = (platform or "").strip()

    if location_name and stanox:
        return f"{location_name} ({stanox})" + (f" plat {platform}" if platform else "")
    if location_name:
        return location_name + (f" plat {platform}" if platform else "")
    if stanox:
        return stanox + (f" plat {platform}" if platform else "")
    return "N/A"
