-- Migration: Add toc_code column to trust_messages and backfill from toc_reference
-- This migration normalizes TOC identifiers so that trust_messages stores both:
-- - toc_id: raw message-provided identifier (may be business_code, atoc_code, or canonical)
-- - toc_code: canonical 2-character TOC code resolved via toc_reference

-- Step 1: Add toc_code column (nullable)
ALTER TABLE trust_messages ADD COLUMN toc_code TEXT;

-- Step 2: Backfill toc_code using toc_reference lookup
-- Prioritize exact toc_code matches first
UPDATE trust_messages
SET toc_code = (
    SELECT toc_code 
    FROM toc_reference 
    WHERE toc_reference.toc_code = trust_messages.toc_id 
    LIMIT 1
)
WHERE toc_id IS NOT NULL AND toc_code IS NULL;

-- Then match on business_code
UPDATE trust_messages
SET toc_code = (
    SELECT toc_code 
    FROM toc_reference 
    WHERE toc_reference.business_code = trust_messages.toc_id
    LIMIT 1
)
WHERE toc_id IS NOT NULL AND toc_code IS NULL;

-- Finally match on atoc_code
UPDATE trust_messages
SET toc_code = (
    SELECT toc_code 
    FROM toc_reference 
    WHERE toc_reference.atoc_code = trust_messages.toc_id
    LIMIT 1
)
WHERE toc_id IS NOT NULL AND toc_code IS NULL;

-- Step 3: Create index for efficient filtering by canonical toc_code
CREATE INDEX IF NOT EXISTS idx_trust_messages_toc_code ON trust_messages(toc_code);
