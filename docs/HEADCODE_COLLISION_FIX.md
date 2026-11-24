# Fix for Headcode Collision Issue

## Problem
When filtering by TD area (e.g., `--td-area EK`), trains from other geographic regions were appearing with incorrect schedule information. A train observed in the EK (East Kent) area might show schedule details from a completely different service running hundreds of miles away.

## Root Cause
**Headcodes are not unique in UK rail.** The same headcode (e.g., "1P58", "2C90") is reused for multiple distinct services throughout the day and across different regions.

The code previously stored only one schedule per headcode:
```python
vstp_by_headcode: Dict[str, VstpSchedule] = {}  # Only stores last schedule!
```

When multiple schedules with the same headcode were loaded, only the last one processed would be kept, overwriting previous ones.

## Solution
Changed to store **all** schedules per headcode:
```python
vstp_by_headcode: Dict[str, List[VstpSchedule]] = {}  # Stores all schedules
```

The `match_td_to_schedule()` function now:
1. Considers all schedules with the matching headcode
2. Restricts candidates to schedules valid on the TD observation date and applies CIF STP-version precedence
3. Prefers TRUST train UID, then TD-area route/location evidence and planned-stop time
4. Returns an explicit ambiguous result instead of selecting the first candidate when evidence ties

## Files Changed
- `nrod_railhub/schedule_matching.py` - Shared date, STP precedence, and ambiguity-aware selection
- `nrod_railhub/views.py` - Live schedule matching, provenance, and safe timetable rendering
- `nrod_railhub/corridor.py` - Historical direction and live schedule matching
- `tests/test_match_td_to_schedule.py`, `tests/test_schedule_matching.py`, and `tests/test_corridor.py`

Matches expose their source, UID, evidence, confidence, and service date through `matched_info`.
Unresolved matches return `None` for the schedule plus `status="ambiguous"` and candidate UIDs.
`get_timetable_fields()` accepts `td_area` so console/database consumers can use the same matcher;
without area context it reports ambiguity rather than returning an arbitrary schedule.
“Unique” means unique among schedules currently loaded; per-TOC downloads can omit services from
operators not included in the configured filter, so a headcode alone is not proof of global uniqueness.

## Migration Notes
For any code that directly accesses `hv.vstp_by_headcode[headcode]` or `hv.sched_by_headcode[headcode]`:
- These now return **lists** instead of single schedules
- Use `[0]` to get the first schedule if you don't need geographic matching
- Or use `match_td_to_schedule()` for proper geographic matching

Example:
```python
# Old code:
schedule = hv.vstp_by_headcode.get(headcode)

# New code (only safe when a unique schedule exists):
schedules = hv.vstp_by_headcode.get(headcode, [])
schedule = schedules[0] if len(schedules) == 1 else None

# New code (with geographic matching):
schedule, reason, info = hv.match_td_to_schedule(td_area, headcode)
```
