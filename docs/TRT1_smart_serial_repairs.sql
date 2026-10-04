-- Repairs verified against TRT1.SOP SMART 1 Serial Signalling Outputs.
-- These four omissions were caused by PDF page-number lines being mistaken
-- for blank bit rows during the original extraction.

BEGIN;

UPDATE smart_serial_bit_map
SET function = 'LSGSTONEAHB',
    section = 'SIGNAL_GROUP_REPLACEMENT',
    function_type = 'SIGNAL_GROUP_REPLACEMENT',
    interlocking = 1
WHERE byte_dec = 19 AND bit = 3;

UPDATE smart_serial_bit_map
SET function = 'P2180BN',
    section = 'POINTS',
    function_type = 'POINTS',
    interlocking = 2
WHERE byte_dec = 25 AND bit = 4;

UPDATE smart_serial_bit_map
SET function = 'R5060CSP',
    section = 'ROUTES',
    function_type = 'ROUTES',
    interlocking = 2
WHERE byte_dec = 32 AND bit = 5;

UPDATE smart_serial_bit_map
SET function = 'S5086',
    section = 'SIGNALS',
    function_type = 'SIGNALS',
    interlocking = 3
WHERE byte_dec = 38 AND bit = 6;

COMMIT;
