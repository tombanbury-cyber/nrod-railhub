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
2. Uses geographic context (TIPLOC/STANOX from SMART, train UID from TRUST) to select the correct one
3. Falls back to time proximity if location matching isn't available

## Files Changed
- `nrod_railhub/views.py` - Core data structures and matching logic
- `tests/test_match_td_to_schedule.py` - Updated to use lists
- `tests/manual_verification_demo.py` - Updated to use lists

## Testing
All 14 unit tests pass. Verification script confirms multiple schedules with same headcode are stored and matched correctly.

## Migration Notes
For any code that directly accesses `hv.vstp_by_headcode[headcode]` or `hv.sched_by_headcode[headcode]`:
- These now return **lists** instead of single schedules
- Use `[0]` to get the first schedule if you don't need geographic matching
- Or use `match_td_to_schedule()` for proper geographic matching

Example:
```python
# Old code:
schedule = hv.vstp_by_headcode.get(headcode)

# New code (simple fallback):
schedules = hv.vstp_by_headcode.get(headcode, [])
schedule = schedules[0] if schedules else None

# New code (with geographic matching):
schedule, reason, info = hv.match_td_to_schedule(td_area, headcode)
```
