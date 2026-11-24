import datetime

from nrod_railhub.schedule_matching import (
    planned_datetime,
    resolve_ranked_candidates,
    schedule_runs_on,
    select_active_schedule_versions,
)


def test_schedule_runs_on_respects_validity_and_weekday():
    wednesday = datetime.date(2026, 3, 4)
    schedule = {
        "schedule_start_date": "2026-03-01",
        "schedule_end_date": "2026-03-31",
        "schedule_days_runs": "0010000",
    }

    assert schedule_runs_on(schedule, wednesday)
    assert not schedule_runs_on(schedule, datetime.date(2026, 3, 5))
    assert not schedule_runs_on(
        {**schedule, "schedule_end_date": "2026-03-03"}, wednesday
    )


def test_active_schedule_versions_prefer_overlay_and_honor_cancellation():
    permanent = {
        "uid": "A12345",
        "start_date": "2026-03-01",
        "stp_indicator": "P",
    }
    overlay = {
        "uid": "A12345",
        "start_date": "2026-03-01",
        "stp_indicator": "O",
    }
    cancellation = {
        "uid": "B12345",
        "start_date": "2026-03-01",
        "stp_indicator": "C",
    }

    assert select_active_schedule_versions([permanent, overlay]) == [overlay]
    assert select_active_schedule_versions([cancellation]) == []


def test_planned_datetime_handles_rail_times_after_midnight():
    schedule = {"start_date": "2026-02-01"}

    assert planned_datetime(
        schedule, "25:15", service_date=datetime.date(2026, 3, 4)
    ) == datetime.datetime(
        2026, 3, 5, 1, 15, tzinfo=datetime.timezone.utc
    )


def test_ranked_candidate_ties_are_ambiguous():
    candidates = [
        {"uid": "A12345", "score": (1, 0)},
        {"uid": "B12345", "score": (1, 0)},
    ]

    result = resolve_ranked_candidates(
        candidates,
        rank=lambda candidate: candidate["score"],
        identity=lambda candidate: candidate["uid"],
    )

    assert result.status == "ambiguous"
    assert result.candidate is None
    assert {candidate["uid"] for candidate in result.alternatives} == {"A12345", "B12345"}
