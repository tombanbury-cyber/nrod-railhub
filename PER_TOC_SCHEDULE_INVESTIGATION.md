# Per-TOC Schedule Downloads - Terminology Clarification

## Problem Statement

User reported: "Check that we're using the correct 'business code' as specified here, https://wiki.openraildata.com/index.php/TOC_Codes, this should not be confused with the 'sector code'"

## Resolution

### User Confirmation ✅

The repository owner confirmed that:

1. **`CIF_XX_TOC_FULL_DAILY` format is CORRECT**
   - URL: `https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_XX_TOC_FULL_DAILY&day=toc-full`
   - Downloads TOC daily schedule in **JSON format** (despite "CIF" in the name)
   - XX = business code (numeric, e.g., 84, 79, 71)

2. **`CIF_XX_TOC_UPDATE_DAILY` format is CORRECT**
   - URL: `https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_XX_TOC_UPDATE_DAILY&day=toc-update-mon`
   - Downloads schedule updates in **JSON format**
   - Replace `-mon` with day code (mon/tues/wed etc.)

3. **Only one format returns actual CIF:**
   - URL: `https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full.CIF.gz`
   - Note the `.CIF.gz` suffix - indicates gzipped CIF file
   - This is for ALL operators, not per-TOC

### Implementation Status ✅

**Current implementation is CORRECT** - No changes needed to core functionality!

## Terminology Clarification

### The Confusion: "Business Code" Has Multiple Meanings

The term "business code" is used differently in various contexts within UK rail systems:

#### Traditional Rail Industry Usage
In traditional UK rail documentation (e.g., railwaycodes.org.uk):
- **Business Code**: 2-letter alphanumeric (e.g., "HU", "EF", "HY")
- **Sector Code**: 2-digit numeric (e.g., "80", "25", "84")
- Used for operational categorization and franchise management

#### Network Rail Data Feeds Usage
In Network Rail's data feed URLs and documentation:
- **Business Code**: Numeric identifier used in feed URLs
- Format: `CIF_XX_TOC_FULL_DAILY` where XX is the numeric "business code"
- Examples: 84 (Southeastern), 79 (Great Western), 71 (South Western Railway)
- **These are the codes used for per-TOC schedule downloads**

#### TRUST Messages
- **Historic topic format**: `TRAIN_MVT_XX_TOC` where XX is business code
- **Message content**: Contains sector code to reference operator
- The numeric codes may be the same in both contexts

### Our Implementation

The field named `business_code` in our `TOC_DATA` structure:
```python
'SE': {'name': 'Southeastern', 'business_code': '84'}
'GW': {'name': 'Great Western Railway', 'business_code': '79'}
'SW': {'name': 'South Western Railway', 'business_code': '71'}
```

These numeric codes are:
- ✅ **Correct** for Network Rail schedule download URLs
- ✅ **Used correctly** in `CIF_XX_TOC_FULL_DAILY` format
- ℹ️ Called "business codes" in Network Rail's data feed context
- ℹ️ May also be "sector codes" in TRUST message context
- ℹ️ Different from traditional 2-letter "business codes" (HU, EF, HY)

### Recommendation

The implementation is correct. For clarity, we should:
1. Add code comments explaining the terminology nuance
2. Update documentation to distinguish Network Rail "business codes" from traditional codes
3. Note that the same numeric codes serve multiple purposes across different feeds

## Summary

✅ **No code changes required to core functionality**
✅ Format `CIF_XX_TOC_FULL_DAILY` is correct and confirmed working
✅ Numeric codes (84, 79, 71) are correct for schedule downloads
ℹ️ Terminology "business code" is context-dependent but correct in our usage

The confusion arose from conflicting definitions of "business code" in different rail industry contexts. In the context of Network Rail's data feeds, our implementation uses the correct terminology and codes.

## Actions Taken

1. ✅ Updated this document to reflect correct understanding
2. ⏭️ Add clarifying comments to code
3. ⏭️ Update ARCHITECTURE.md and README.md documentation
4. ⏭️ Ensure comments explain the terminology nuance
