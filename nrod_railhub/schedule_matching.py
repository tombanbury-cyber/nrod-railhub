"""Shared helpers for dated, ambiguity-aware schedule matching."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Callable, Generic, Hashable, Iterable, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ScheduleMatch(Generic[T]):
    """Outcome of ranking candidates without guessing between equally ranked runs."""

    status: str
    candidate: Optional[T]
    alternatives: tuple[T, ...] = ()


def _value(candidate: Any, *names: str) -> Any:
    for name in names:
        if isinstance(candidate, dict):
            value = candidate.get(name)
        elif hasattr(candidate, "keys") and name in candidate.keys():
            value = candidate[name]
        else:
            value = getattr(candidate, name, None)
        if value is not None:
            return value
    return None


def schedule_runs_on(candidate: Any, service_date: datetime.date) -> bool:
    """Return whether a schedule's validity range and weekday include a date."""
    start = str(_value(candidate, "start_date", "schedule_start_date") or "").strip()
    end = str(_value(candidate, "end_date", "schedule_end_date") or "").strip()
    days = str(_value(candidate, "days_run", "schedule_days_runs") or "").strip()

    try:
        start_date = datetime.date.fromisoformat(start[:10]) if start else None
        end_date = datetime.date.fromisoformat(end[:10]) if end else None
    except ValueError:
        return False
    if start_date and service_date < start_date:
        return False
    if end_date and service_date > end_date:
        return False
    if days and len(days) == 7 and all(day in "01" for day in days):
        return days[service_date.weekday()] == "1"
    return True


def planned_datetime(
    candidate: Any,
    planned_time: str,
    *,
    service_date: Optional[datetime.date] = None,
) -> Optional[datetime.datetime]:
    """Combine the service date and CIF time, including post-midnight 24+ hour times."""
    start = str(_value(candidate, "start_date", "schedule_start_date") or "").strip()
    text = (planned_time or "").strip()
    if (not service_date and not start) or not text:
        return None
    try:
        service_day = service_date or datetime.date.fromisoformat(start[:10])
        parts = text.split(":")
        if len(parts) == 1 and len(text) in (4, 6) and text.isdigit():
            hour, minute = int(text[:2]), int(text[2:4])
        else:
            hour, minute = int(parts[0]), int(parts[1])
        if hour < 0 or minute < 0 or minute > 59:
            return None
        return datetime.datetime.combine(
            service_day,
            datetime.time(hour=hour % 24, minute=minute),
            tzinfo=datetime.timezone.utc,
        ) + datetime.timedelta(days=hour // 24)
    except (IndexError, TypeError, ValueError):
        return None


def stp_precedence(candidate: Any) -> int:
    """Return the Network Rail STP variant rank; a lower rank takes precedence."""
    indicator = str(_value(candidate, "stp_indicator", "CIF_stp_indicator") or "P").upper()
    return {"C": 0, "O": 1, "N": 2, "P": 3}.get(indicator, 3)


def select_active_schedule_versions(candidates: Iterable[T]) -> list[T]:
    """Keep the winning CIF variant for each UID, excluding effective cancellations."""
    by_uid: dict[str, list[T]] = {}
    for candidate in candidates:
        uid = str(_value(candidate, "uid", "CIF_train_uid") or "").strip()
        if uid:
            by_uid.setdefault(uid, []).append(candidate)

    active: list[T] = []
    for variants in by_uid.values():
        match = resolve_ranked_candidates(
            variants,
            rank=lambda variant: (
                -stp_precedence(variant),
                str(_value(variant, "start_date", "schedule_start_date") or ""),
            ),
            identity=lambda variant: (
                str(_value(variant, "uid", "CIF_train_uid") or ""),
                str(_value(variant, "start_date", "schedule_start_date") or ""),
                str(_value(variant, "stp_indicator", "CIF_stp_indicator") or "P").upper(),
            ),
        )
        if match.candidate is not None and stp_precedence(match.candidate) != 0:
            active.append(match.candidate)
    return active


def resolve_ranked_candidates(
    candidates: Iterable[T],
    *,
    rank: Callable[[T], tuple],
    identity: Callable[[T], Hashable],
) -> ScheduleMatch[T]:
    """Choose one uniquely best candidate, returning ambiguity instead of a guess."""
    ranked = [(candidate, rank(candidate)) for candidate in candidates]
    if not ranked:
        return ScheduleMatch(status="unmatched", candidate=None)

    best_rank = max(candidate_rank for _, candidate_rank in ranked)
    best = [candidate for candidate, candidate_rank in ranked if candidate_rank == best_rank]
    best_by_identity: dict[Hashable, T] = {}
    for candidate in best:
        best_by_identity.setdefault(identity(candidate), candidate)
    alternatives = tuple(best_by_identity.values())

    if len(alternatives) != 1:
        return ScheduleMatch(status="ambiguous", candidate=None, alternatives=alternatives)
    return ScheduleMatch(status="matched", candidate=alternatives[0], alternatives=alternatives)
