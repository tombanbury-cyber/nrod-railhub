# Per-TOC Schedule Downloads - Business Code Correction

## Problem Resolution

The implementation was using numeric **sector codes** (e.g., 84, 79, 71) instead of 2-letter **business codes** (e.g., HU, HY, HW) in schedule download URLs.

### Correct Format

**Schedule downloads use 2-letter business codes:**
- URL format: `CIF_{business_code}_TOC_FULL_DAILY` where business_code is 2 letters
- Example: `CIF_HU_TOC_FULL_DAILY` for Southeastern (NOT `CIF_84_TOC_FULL_DAILY`)

### Correct Code Mappings

Based on authoritative data from the user:

| Company Name | TOC Code | Business Code | Sector Code | ATOC Code |
|--------------|----------|---------------|-------------|-----------|
| Southeastern | SE | **HU** | 80 | SET |
| South Western Railway | SW | **HY** | 84 | SWR |
| Southern | SN | **HW** | 88 | SOU |
| ScotRail | SR | **HA** | 60 | SCO |
| East Midlands Railway | EM | **EM** | 28 | EMR |
| Avanti West Coast | VT | **HF** | 65 | AVC |

### Key Distinctions

1. **Business Code** (2-letter): Used in schedule download URLs (CIF_XX_TOC_FULL_DAILY)
2. **Sector Code** (numeric): Appears in TRUST messages for TOC identification
3. **TOC Code** (2-letter): Canonical identifier in our system
4. **ATOC Code** (3-letter): Association of Train Operating Companies standard code

### Changes Made

1. **TOC_DATA structure updated:**
   - `business_code`: Now contains 2-letter codes (HU, HY, HW, etc.)
   - `sector_code`: New field for numeric codes (80, 84, 88, etc.)

2. **Database schema updated:**
   - Added `sector_code` column to `toc_reference` table
   - Both business and sector codes stored separately

3. **TOCResolver updated:**
   - Added `sector_to_canonical` mapping for TRUST messages
   - `resolve_toc_code()` checks business codes, sector codes, and ATOC codes

4. **Schedule downloads now correct:**
   - `CIF_HU_TOC_FULL_DAILY` ✅ (Southeastern)
   - `CIF_HY_TOC_FULL_DAILY` ✅ (South Western Railway)
   - `CIF_HW_TOC_FULL_DAILY` ✅ (Southern)

### TRUST Message Compatibility

TRUST messages contain numeric sector codes for TOC identification. The system now:
1. Stores sector codes separately
2. Resolves sector codes to canonical TOC codes
3. Maintains backward compatibility with existing TRUST message handling

### Summary

✅ **Implementation corrected**
✅ Schedule URLs now use 2-letter business codes  
✅ Sector codes preserved for TRUST message handling
✅ All tests updated and passing
✅ Documentation corrected
