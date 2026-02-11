# Per-TOC Schedule Downloads Investigation

## Problem Statement

User reported: "Check that we're using the correct 'business code' as specified here, https://wiki.openraildata.com/index.php/TOC_Codes, this should not be confused with the 'sector code'"

## Investigation Summary

### Critical Findings

#### 1. **WRONG DOWNLOAD FORMAT**

**Current Implementation:**
```python
schedule_type = f"CIF_{business_code}_TOC_FULL_DAILY"
# Example: CIF_84_TOC_FULL_DAILY
```

**According to OpenRailData Wiki:**
- **CIF format**: Does NOT support per-TOC downloads
  - Only `CIF_ALL_FULL_DAILY` is available (all operators)
  - Quote: "The schedules in CIF format are only available for all operators"

- **JSON format**: DOES support per-TOC downloads
  - Format: `JSON_{code}_FULL_DAILY`
  - Available for individual operators

**Conclusion:** Our `CIF_{XX}_TOC_FULL_DAILY` format does not exist!

#### 2. **TERMINOLOGY CONFUSION**

The field name `business_code` with numeric values (84, 79, 71) is problematic.

**UK Rail Industry Code Systems:**

| Code Type | Format | Example | Usage |
|-----------|--------|---------|-------|
| **Business Code** | 2-letter | HU, EF, HY | Operational systems, TRUST internal |
| **Sector Code** | 2-digit | 80, 25, 84 | Service categorization |
| **TOC Code** | 2-letter | SE, GW, SW | Canonical identifier |
| **ATOC Code** | 3-letter | SET, GWR, SWR | Timetable standard |

**Research Findings:**
- Southeastern: Business Code = "HU", Sector Code = "80"
- Our code has: `'SE': {'business_code': '84'}`
- 84 is actually South Western Railway's sector code!

**Conflicting Information:**
- Some Network Rail TRUST documentation refers to numeric codes as "business codes"
- But traditional rail industry standards say business codes are 2-letter
- The numeric codes in TRUST may be sector codes called "business codes" in that context

#### 3. **WHAT CODE TO USE FOR JSON DOWNLOADS?**

Search results show conflicting information:
- Some say: `JSON_{atoc_code}_FULL_DAILY` using 2-letter codes (e.g., JSON_SN_FULL_DAILY)
- Others say: `JSON_{business_code}_FULL_DAILY` using "business codes" (not ATOC)
- Example given: `JSON_VTEC_FULL_DAILY` (4-letter, neither standard format)

**Most likely correct:**
- JSON downloads use **TOC/ATOC codes** (2-3 letter codes)
- NOT numeric codes
- Examples: SE, GW, SW, or SET, GWR, SWR

## Required Changes

### 1. Change Download Format

```python
# BEFORE (WRONG):
schedule_type = f"CIF_{business_code}_TOC_FULL_DAILY"

# AFTER (CORRECT):
schedule_type = f"JSON_{toc_code}_FULL_DAILY"
```

### 2. Use Correct Identifier

Instead of:
```python
'SE': {'business_code': '84'}  # WRONG
```

Use TOC or ATOC code directly:
```python
'SE': {'atoc_code': 'SET'}  # For downloads: JSON_SET_FULL_DAILY
# OR
# Use canonical code: JSON_SE_FULL_DAILY
```

### 3. Update URL Construction

```python
# Current implementation in download_toc_schedule():
def download_toc_schedule(self, toc_code: str, business_code: str, ...):
    schedule_type = f"CIF_{business_code}_TOC_UPDATE_DAILY" if update_mode else f"CIF_{business_code}_TOC_FULL_DAILY"

# Should be:
def download_toc_schedule(self, toc_code: str, ...):
    # Use the TOC code directly (SE, GW, SW) or ATOC code (SET, GWR, SWR)
    schedule_type = f"JSON_{toc_code}_UPDATE_DAILY" if update_mode else f"JSON_{toc_code}_FULL_DAILY"
```

### 4. Remove Numeric "business_code" Field

The numeric codes (84, 79, 71) should either:
- Be removed entirely if not needed
- Be renamed to accurately reflect what they are
- Only be used for TRUST message normalization (their original purpose)

## Testing Impact

### Tests to Update

1. `test_schedule_resolver_download_url_construction()` 
   - Change assertion from `CIF_84_TOC_FULL_DAILY` to `JSON_SE_FULL_DAILY` (or similar)

2. `test_download_multiple_toc_schedules()`
   - Update expectations for file format

3. Documentation examples
   - Update ARCHITECTURE.md
   - Update README.md

## Recommended Action Plan

### Phase 1: Verify Correct Format
1. Access OpenRailData wiki directly to confirm exact format
2. Test actual downloads with credentials to see what works
3. Document the working format

### Phase 2: Update Implementation
1. Change `download_toc_schedule()` to use JSON format
2. Update URL construction to use TOC/ATOC codes directly
3. Remove or clarify the `business_code` field usage
4. Update `download_multiple_toc_schedules()` accordingly

### Phase 3: Update Tests
1. Fix test assertions for new format
2. Add tests for both FULL_DAILY and UPDATE_DAILY JSON formats
3. Verify tests pass

### Phase 4: Update Documentation
1. Correct terminology throughout
2. Update examples to show JSON format
3. Clarify which codes are used where

## Questions for Repository Owner

1. **Do you have access to test actual downloads?**
   - Need to verify what format actually works with Network Rail API
   - Can test: `JSON_SE_FULL_DAILY` vs `CIF_84_TOC_FULL_DAILY`

2. **What are the numeric codes really for?**
   - Are they only for TRUST message normalization?
   - Should they be removed from schedule download logic?

3. **Which code format works?**
   - `JSON_{canonical_toc_code}_FULL_DAILY` (e.g., JSON_SE_FULL_DAILY)?
   - `JSON_{atoc_code}_FULL_DAILY` (e.g., JSON_SET_FULL_DAILY)?

## References

- OpenRailData Wiki SCHEDULE page: https://wiki.openraildata.com/index.php/SCHEDULE
- OpenRailData Wiki TOC Codes: https://wiki.openraildata.com/index.php/TOC_Codes
- Railway Codes Business Codes: http://www.railwaycodes.org.uk/operators/business.shtm

## Status

**BLOCKED**: Need to verify actual working format before implementing changes.

The current implementation (`CIF_{XX}_TOC_FULL_DAILY`) appears to be incorrect based on documentation research, but we need confirmation before making breaking changes.
