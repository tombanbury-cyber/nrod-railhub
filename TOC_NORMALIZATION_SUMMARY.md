# TOC Identifier Normalization Implementation Summary

## Overview
This implementation adds the ability to normalize non-canonical TOC (Train Operating Company) identifiers in TRUST messages to canonical 2-character TOC codes, enabling proper database joins and UI display of operator names.

## Problem Statement
TRUST messages sometimes contain non-canonical TOC identifiers:
- **ATOC codes**: 3-letter codes like "SWR", "GWR", "XCT"
- **Business codes**: Numeric codes like "71", "79", "27"

The application previously assumed all toc_id values were canonical 2-character codes (e.g., "SW", "GW"), causing database joins to fail and the UI to not display human-friendly TOC names.

## Solution

### 1. Enhanced TOC Data Structure (`nrod_railhub/resolvers.py`)
Extended `TOC_DATA` to include alternative identifier formats:
```python
'SW': {
    'name': 'South Western Railway',
    'sector': 'Passenger',
    'atoc_code': 'SWR',      # ATOC 3-letter code
    'business_code': '71'     # Numeric business code
}
```

### 2. Mapping Indices
Built two lookup dictionaries in TOCResolver:
- `atoc_to_canonical`: Maps ATOC codes → canonical codes (e.g., 'SWR' → 'SW')
- `business_to_canonical`: Maps business codes → canonical codes (e.g., '71' → 'SW')

### 3. Resolution Method
Implemented `resolve_toc_code(incoming: Optional[str]) -> Optional[str]`:
1. Normalizes and upper-cases input
2. Returns input if already canonical
3. Checks ATOC mapping
4. Checks business code mapping
5. Returns None if no mapping found

### 4. TRUST Message Processing
Modified `HumanView.upsert_trust()` to normalize toc_id:
```python
raw_toc_id = body.get("toc_id")
if raw_toc_id and self.toc_resolver:
    canonical_toc = self.toc_resolver.resolve_toc_code(raw_toc_id)
    st.toc_id = canonical_toc if canonical_toc else raw_toc_id
```

### 5. Database Helper
Added `RailDB.get_canonical_toc_code()` for defensive lookups when resolver unavailable:
```sql
SELECT toc_code FROM toc_reference 
WHERE toc_code=? OR atoc_code=? OR business_code=?
```

## Data Source
TOC mappings based on Network Rail Open Data standards:
- **ATOC codes**: Association of Train Operating Companies standard 3-letter codes
- **Business codes**: Numeric codes observed in TRUST feeds
- Only authoritative mappings included to avoid incorrect associations

## Example Mappings

| Incoming Code | Type | Canonical | Operator Name |
|---------------|------|-----------|---------------|
| SW | Canonical | SW | South Western Railway |
| SWR | ATOC | SW | South Western Railway |
| 71 | Business | SW | South Western Railway |
| GW | Canonical | GW | Great Western Railway |
| GWR | ATOC | GW | Great Western Railway |
| 79 | Business | GW | Great Western Railway |
| XCT | ATOC | XC | CrossCountry |
| 27 | Business | XC | CrossCountry |

## Testing

### Unit Tests (`test_toc_normalization.py`)
14 comprehensive tests covering:
- ✅ Canonical code preservation
- ✅ ATOC code mapping
- ✅ Business code mapping
- ✅ Unknown code handling
- ✅ Database joins with normalized codes
- ✅ Edge cases (None, whitespace, case sensitivity)

### Manual Verification (`demo_toc_normalization.py`)
Demonstration script showing:
- ✅ TOC code resolution for all scenarios
- ✅ TRUST message processing with normalization
- ✅ Database joins returning correct operator names

### Test Results
- **New tests**: 14/14 passing
- **Existing tests**: 91/91 passing
- **Total**: 105/105 tests passing
- **No regressions introduced**

## Benefits

### Before
```sql
-- TRUST message has toc_id='SWR' (ATOC code)
SELECT tm.*, tr.toc_name
FROM trust_messages tm
LEFT JOIN toc_reference tr ON tm.toc_id = tr.toc_code
-- Result: toc_name is NULL (no match)
```

### After
```sql
-- TRUST message normalized: toc_id='SWR' → 'SW'
SELECT tm.*, tr.toc_name
FROM trust_messages tm
LEFT JOIN toc_reference tr ON tm.toc_id = tr.toc_code
-- Result: toc_name='South Western Railway' (successful join)
```

## Backward Compatibility
- ✅ TOCResolver parameter is optional in HumanView
- ✅ Existing code without resolver continues to work
- ✅ Unknown codes are preserved (not discarded)
- ✅ Canonical codes pass through unchanged

## Usage

### In Application Code
```python
# Create resolver
toc_resolver = TOCResolver()

# Pass to HumanView
hv = HumanView(resolver=resolver, smart=smart, toc_resolver=toc_resolver)

# Process TRUST message - normalization happens automatically
trust_msg = {'body': {'train_id': '123', 'toc_id': 'SWR', ...}}
st = hv.upsert_trust(trust_msg)
# st.toc_id is now 'SW' (canonical)
```

### Direct Resolution
```python
resolver = TOCResolver()

# Resolve various formats
resolver.resolve_toc_code('SW')   # 'SW' (canonical)
resolver.resolve_toc_code('SWR')  # 'SW' (ATOC)
resolver.resolve_toc_code('71')   # 'SW' (business)
resolver.resolve_toc_code('ZZZ')  # None (unknown)
```

## Files Changed
1. `nrod_railhub/resolvers.py` - Added mapping data and resolution logic
2. `nrod_railhub/views.py` - Integrated normalization in TRUST processing
3. `nrod_railhub/database.py` - Added defensive lookup method
4. `nrod_railhub/cli.py` - Wired up resolver to HumanView
5. `tests/test_toc_normalization.py` - Comprehensive test suite
6. `tests/demo_toc_normalization.py` - Manual verification script

## Future Enhancements
- Load mappings from external CSV/JSON if more flexibility needed
- Add periodic updates from OpenRailData API
- Support for historical TOC code changes
- Logging of unmapped codes for analysis

## Notes
Business codes included are based on commonly observed patterns in TRUST feeds. If additional mappings are discovered, they can be easily added to the `TOC_DATA` structure following the same pattern.
