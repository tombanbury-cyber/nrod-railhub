-- SQLite schema for TRT1.SOP "SMART 1 Serial Signalling Outputs"
-- Static mapping extracted from the source document.

CREATE TABLE IF NOT EXISTS smart_serial_bit_map (
    byte_dec          INTEGER NOT NULL CHECK (byte_dec BETWEEN 0 AND 255),
    bit               INTEGER NOT NULL CHECK (bit BETWEEN 0 AND 7),
    interlocking      INTEGER,
    section           TEXT,
    function_type     TEXT,
    function          TEXT,
    location_context  TEXT,
    source_document   TEXT NOT NULL DEFAULT 'TRT1.SOP SMART 1 Serial Signalling Outputs',
    PRIMARY KEY (byte_dec, bit)
);

CREATE INDEX IF NOT EXISTS idx_smart_serial_bit_map_function
    ON smart_serial_bit_map(function);

CREATE INDEX IF NOT EXISTS idx_smart_serial_bit_map_interlocking_section
    ON smart_serial_bit_map(interlocking, section);

-- Optional: store each received SMART serial byte snapshot/event.
CREATE TABLE IF NOT EXISTS smart_serial_byte_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts          TEXT NOT NULL,              -- ISO-8601 UTC recommended
    byte_dec          INTEGER NOT NULL CHECK (byte_dec BETWEEN 0 AND 255),
    value_dec         INTEGER NOT NULL CHECK (value_dec BETWEEN 0 AND 255),
    source            TEXT,
    raw_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_smart_serial_byte_events_byte_ts
    ON smart_serial_byte_events(byte_dec, event_ts);

-- Optional: normalized transitions for individual mapped bits.
CREATE TABLE IF NOT EXISTS smart_serial_bit_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts          TEXT NOT NULL,
    byte_dec          INTEGER NOT NULL CHECK (byte_dec BETWEEN 0 AND 255),
    bit               INTEGER NOT NULL CHECK (bit BETWEEN 0 AND 7),
    state             INTEGER NOT NULL CHECK (state IN (0,1)),
    source_byte_value INTEGER CHECK (source_byte_value BETWEEN 0 AND 255),
    FOREIGN KEY (byte_dec, bit)
        REFERENCES smart_serial_bit_map(byte_dec, bit)
);

CREATE INDEX IF NOT EXISTS idx_smart_serial_bit_events_key_ts
    ON smart_serial_bit_events(byte_dec, bit, event_ts);

-- Current state cache, useful for joining against the dictionary.
CREATE TABLE IF NOT EXISTS smart_serial_bit_state (
    byte_dec          INTEGER NOT NULL CHECK (byte_dec BETWEEN 0 AND 255),
    bit               INTEGER NOT NULL CHECK (bit BETWEEN 0 AND 7),
    state             INTEGER NOT NULL CHECK (state IN (0,1)),
    updated_ts        TEXT NOT NULL,
    source_byte_value INTEGER CHECK (source_byte_value BETWEEN 0 AND 255),
    PRIMARY KEY (byte_dec, bit),
    FOREIGN KEY (byte_dec, bit)
        REFERENCES smart_serial_bit_map(byte_dec, bit)
);

-- Convenient decoded view.
CREATE VIEW IF NOT EXISTS v_smart_serial_current AS
SELECT
    s.updated_ts,
    m.interlocking,
    s.byte_dec,
    printf('%02X', s.byte_dec) AS byte_hex,
    s.bit,
    printf('%02X', (1 << s.bit)) AS bit_mask_hex,
    s.state,
    m.section,
    m.function_type,
    m.function,
    m.location_context,
    s.source_byte_value
FROM smart_serial_bit_state AS s
LEFT JOIN smart_serial_bit_map AS m
  ON m.byte_dec = s.byte_dec
 AND m.bit = s.bit;
