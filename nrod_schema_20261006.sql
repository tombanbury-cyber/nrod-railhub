CREATE TABLE td_state (
                    td_area TEXT NOT NULL,
                    headcode TEXT NOT NULL,
                    last_time_ms INTEGER NOT NULL,
                    last_time_iso TEXT,
                    from_berth TEXT,
                    to_berth TEXT,
                    stanox TEXT,
                    location_name TEXT,
                    platform TEXT,
                    sched_dep TEXT,
                    sched_arr TEXT,
                    origin_name TEXT,
                    dest_name TEXT,
                    uid TEXT,
                    PRIMARY KEY (td_area, headcode)
                );
CREATE TABLE td_berth_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT,
                    headcode TEXT,
                    msg_type TEXT NOT NULL,
                    from_berth TEXT,
                    to_berth TEXT,
                    descr TEXT
                );
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE td_berth_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_event_id INTEGER UNIQUE,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    headcode TEXT NOT NULL,
                    from_berth TEXT NOT NULL,
                    to_berth TEXT NOT NULL,
                    source_msg_type TEXT NOT NULL,
                    evidence_json TEXT
                );
CREATE TABLE td_sclass_state (
                    td_area TEXT NOT NULL,
                    address TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    last_seen_ts INTEGER NOT NULL,
                    last_seen_iso TEXT NOT NULL,
                    raw_data TEXT NOT NULL,
                    byte_length INTEGER NOT NULL,
                    PRIMARY KEY (td_area, address)
                );
CREATE TABLE td_sclass_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    address TEXT NOT NULL,
                    byte_offset INTEGER NOT NULL DEFAULT 0,
                    bit INTEGER NOT NULL,
                    old_state INTEGER NOT NULL,
                    new_state INTEGER NOT NULL,
                    raw_old TEXT NOT NULL,
                    raw_new TEXT NOT NULL
                );
CREATE TABLE trust_state (
                    train_id TEXT PRIMARY KEY,
                    headcode TEXT,
                    uid TEXT,
                    toc_id TEXT,
                    last_event_time TEXT,
                    last_location TEXT,
                    last_delay_min INTEGER,
                    raw_json TEXT
                );
CREATE TABLE vstp_state (
                    uid TEXT,
                    headcode TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    raw_json TEXT,
                    PRIMARY KEY (uid, start_date)
                );
CREATE TABLE trust_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    train_id TEXT,
                    actual_timestamp_ms INTEGER,
                    gbtt_timestamp_ms INTEGER,
                    planned_timestamp_ms INTEGER,
                    planned_event_type TEXT,
                    event_type TEXT,
                    event_source TEXT,
                    correction_ind INTEGER,
                    offroute_ind INTEGER,
                    direction_ind TEXT,
                    line_ind TEXT,
                    platform TEXT,
                    route TEXT,
                    train_service_code TEXT,
                    division_code TEXT,
                    toc_id TEXT,
                    toc_code TEXT,
                    timetable_variation INTEGER,
                    variation_status TEXT,
                    next_report_stanox TEXT,
                    next_report_run_time INTEGER,
                    train_terminated INTEGER,
                    delay_monitoring_point INTEGER,
                    reporting_stanox TEXT,
                    auto_expected INTEGER,
                    raw_json TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    UNIQUE(train_id, actual_timestamp_ms)
                );
CREATE TABLE vstp_schedules (
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    schedule_end_date TEXT,
                    transaction_type TEXT,
                    train_status TEXT,
                    schedule_days_runs TEXT,
                    applicable_timetable TEXT,
                    CIF_train_uid TEXT,
                    CIF_stp_indicator TEXT,
                    signalling_id TEXT,
                    CIF_train_service_code TEXT,
                    CIF_train_category TEXT,
                    CIF_power_type TEXT,
                    sender_organisation TEXT,
                    raw_json TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    PRIMARY KEY (uid, schedule_start_date)
                );
CREATE TABLE vstp_schedule_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    segment_index INTEGER NOT NULL,
                    location_index INTEGER NOT NULL,
                    tiploc TEXT,
                    scheduled_pass_time TEXT,
                    scheduled_departure_time TEXT,
                    scheduled_arrival_time TEXT,
                    public_departure_time TEXT,
                    public_arrival_time TEXT,
                    CIF_pathing_allowance TEXT,
                    CIF_activity TEXT,
                    CIF_line TEXT,
                    CIF_engineering_allowance TEXT,
                    CIF_performance_allowance TEXT,
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
CREATE TABLE cif_schedules (
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    schedule_end_date TEXT,
                    toc_code TEXT,
                    transaction_type TEXT,
                    train_status TEXT,
                    schedule_days_runs TEXT,
                    applicable_timetable TEXT,
                    CIF_train_uid TEXT,
                    CIF_stp_indicator TEXT,
                    signalling_id TEXT,
                    CIF_train_service_code TEXT,
                    CIF_train_category TEXT,
                    CIF_power_type TEXT,
                    CIF_headcode TEXT,
                    raw_json TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    PRIMARY KEY (uid, schedule_start_date, CIF_stp_indicator)
                );
CREATE TABLE cif_schedule_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    segment_index INTEGER NOT NULL,
                    location_index INTEGER NOT NULL,
                    tiploc TEXT,
                    scheduled_pass_time TEXT,
                    scheduled_departure_time TEXT,
                    scheduled_arrival_time TEXT,
                    public_departure_time TEXT,
                    public_arrival_time TEXT,
                    platform TEXT,
                    CIF_pathing_allowance TEXT,
                    CIF_activity TEXT,
                    CIF_line TEXT,
                    CIF_path TEXT,
                    CIF_engineering_allowance TEXT,
                    CIF_performance_allowance TEXT,
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
CREATE TABLE toc_reference (
                    toc_code TEXT PRIMARY KEY,
                    toc_name TEXT NOT NULL,
                    business_code TEXT,
                    sector_code TEXT,
                    atoc_code TEXT,
                    sector TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
CREATE TABLE toc_td_areas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    toc_code TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    confidence REAL,
                    effective_from TEXT,
                    effective_to TEXT,
                    created_by TEXT,
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    notes TEXT,
                    UNIQUE(toc_code, td_area)
                );
CREATE TABLE corpus_locations (
                    tiploc TEXT,
                    stanox TEXT,
                    crs TEXT,
                    nlc TEXT,
                    name TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (tiploc, stanox, crs)
                );
CREATE TABLE smart_berths (
                    td_area TEXT NOT NULL,
                    berth TEXT NOT NULL,
                    stanox TEXT,
                    platform TEXT,
                    event TEXT,
                    stanme TEXT,
                    step_type TEXT,
                    from_line TEXT,
                    to_line TEXT,
                    berthoffset INTEGER,
                    comment TEXT,
                    raw_json TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (td_area, berth)
                );
CREATE TABLE sclass_correlation_config (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
CREATE TABLE td_sclass_movement_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    td_area TEXT NOT NULL,
                    movement_event_id INTEGER NOT NULL,
                    movement_ts_ms INTEGER NOT NULL,
                    movement_ts_iso TEXT NOT NULL,
                    headcode TEXT NOT NULL,
                    from_berth TEXT NOT NULL,
                    to_berth TEXT NOT NULL,
                    source_msg_type TEXT NOT NULL,
                    change_event_id INTEGER NOT NULL,
                    change_ts_ms INTEGER NOT NULL,
                    change_ts_iso TEXT NOT NULL,
                    address TEXT NOT NULL,
                    byte_offset INTEGER NOT NULL DEFAULT 0,
                    bit INTEGER NOT NULL,
                    old_state INTEGER NOT NULL,
                    new_state INTEGER NOT NULL,
                    dt_ms INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
CREATE TABLE td_sclass_movement_scores (
                    td_area TEXT NOT NULL,
                    from_berth TEXT NOT NULL,
                    to_berth TEXT NOT NULL,
                    observation_count INTEGER NOT NULL DEFAULT 0,
                    matching_count INTEGER NOT NULL DEFAULT 0,
                    movement_count INTEGER NOT NULL DEFAULT 0,
                    correlation_pct REAL NOT NULL DEFAULT 0.0,
                    mean_dt_ms REAL,
                    median_dt_ms REAL,
                    variance_dt_ms REAL,
                    min_dt_ms INTEGER,
                    max_dt_ms INTEGER,
                    lead_count INTEGER NOT NULL DEFAULT 0,
                    lag_count INTEGER NOT NULL DEFAULT 0,
                    on_count INTEGER NOT NULL DEFAULT 0,
                    off_count INTEGER NOT NULL DEFAULT 0,
                    associated_bits_json TEXT NOT NULL,
                    last_seen_ts_ms INTEGER NOT NULL,
                    last_seen_iso TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (td_area, from_berth, to_berth)
                );
CREATE TABLE td_sclass_lab_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relation_type TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    address TEXT,
                    byte_offset INTEGER,
                    bit INTEGER,
                    from_berth TEXT,
                    to_berth TEXT,
                    state TEXT NOT NULL DEFAULT 'inferred',
                    source TEXT,
                    confidence REAL,
                    notes TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
CREATE TABLE physical_signal_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    td_area TEXT NOT NULL,
                    address TEXT NOT NULL,
                    byte_offset INTEGER NOT NULL DEFAULT 0,
                    bit INTEGER NOT NULL,
                    from_berth TEXT,
                    to_berth TEXT,
                    physical_signal_number TEXT,
                    physical_signal_location TEXT,
                    mapping_confidence REAL,
                    correlation_confidence REAL,
                    verification_status TEXT NOT NULL DEFAULT 'unknown',
                    source TEXT,
                    reviewer TEXT,
                    evidence_json TEXT,
                    notes TEXT,
                    supersedes_id INTEGER,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
CREATE TABLE topology_nodes (
                    node_id TEXT PRIMARY KEY,
                    td_area TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    attributes_json TEXT NOT NULL DEFAULT '{}',
                    verification_status TEXT NOT NULL DEFAULT 'inferred',
                    provenance TEXT NOT NULL DEFAULT 'manual',
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
CREATE TABLE topology_edges (
                    edge_id TEXT PRIMARY KEY,
                    td_area TEXT NOT NULL,
                    from_node_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    hypothesis TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL,
                    verification_status TEXT NOT NULL DEFAULT 'inferred',
                    provenance TEXT NOT NULL DEFAULT 'manual',
                    reviewed_by TEXT,
                    review_notes TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE(td_area, from_node_id, relationship, to_node_id, hypothesis)
                );
CREATE TABLE topology_edge_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    edge_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    UNIQUE(edge_id, source_type, source_id)
                );
CREATE TABLE berth_signal_observations (
                    id INTEGER PRIMARY KEY,
                    td_area TEXT NOT NULL,
                    step_event_id INTEGER,
                    step_timestamp INTEGER,
                    from_berth TEXT,
                    to_berth TEXT,
                    descr TEXT,
                    signal_event_id INTEGER,
                    signal_timestamp INTEGER,
                    address TEXT NOT NULL,
                    data TEXT,
                    dt_ms INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
CREATE TABLE berth_signal_scores (
                    td_area TEXT NOT NULL,
                    from_berth TEXT NOT NULL,
                    to_berth TEXT NOT NULL,
                    address TEXT NOT NULL,
                    score REAL NOT NULL,
                    obs_count INTEGER NOT NULL DEFAULT 1,
                    last_seen_ts INTEGER,
                    last_seen_utc TEXT NOT NULL,
                    last_data TEXT,
                    PRIMARY KEY (td_area, from_berth, to_berth, address)
                );
CREATE TABLE mapper_config (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
CREATE TABLE td_signal_bytes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    area_id TEXT NOT NULL,
                    address_int INTEGER NOT NULL,
                    value_int INTEGER NOT NULL,
                    address TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    raw_message_id INTEGER REFERENCES td_signal_events(id)
                );
CREATE TABLE smart_serial_bit_map (
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
CREATE TABLE smart_serial_byte_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts          TEXT NOT NULL,              -- ISO-8601 UTC recommended
    byte_dec          INTEGER NOT NULL CHECK (byte_dec BETWEEN 0 AND 255),
    value_dec         INTEGER NOT NULL CHECK (value_dec BETWEEN 0 AND 255),
    source            TEXT,
    raw_message       TEXT
);
CREATE TABLE smart_serial_bit_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts          TEXT NOT NULL,
    byte_dec          INTEGER NOT NULL CHECK (byte_dec BETWEEN 0 AND 255),
    bit               INTEGER NOT NULL CHECK (bit BETWEEN 0 AND 7),
    state             INTEGER NOT NULL CHECK (state IN (0,1)),
    source_byte_value INTEGER CHECK (source_byte_value BETWEEN 0 AND 255),
    FOREIGN KEY (byte_dec, bit)
        REFERENCES smart_serial_bit_map(byte_dec, bit)
);
CREATE TABLE smart_serial_bit_state (
    byte_dec          INTEGER NOT NULL CHECK (byte_dec BETWEEN 0 AND 255),
    bit               INTEGER NOT NULL CHECK (bit BETWEEN 0 AND 7),
    state             INTEGER NOT NULL CHECK (state IN (0,1)),
    updated_ts        TEXT NOT NULL,
    source_byte_value INTEGER CHECK (source_byte_value BETWEEN 0 AND 255),
    PRIMARY KEY (byte_dec, bit),
    FOREIGN KEY (byte_dec, bit)
        REFERENCES smart_serial_bit_map(byte_dec, bit)
);
CREATE TABLE td_smart_signal_map (
    td_area          TEXT NOT NULL,
    td_subarea       TEXT NOT NULL,
    td_address       TEXT NOT NULL,

    smart_byte_dec   INTEGER NOT NULL,
    smart_bit        INTEGER NOT NULL,

    signal           TEXT,

    relationship     TEXT,
    evidence_count   INTEGER NOT NULL DEFAULT 0,
    confidence       REAL,
    status           TEXT NOT NULL DEFAULT 'candidate',
    notes            TEXT,

    PRIMARY KEY (
        td_area,
        td_subarea,
        td_address,
        smart_byte_dec,
        smart_bit
    ),

    FOREIGN KEY (smart_byte_dec, smart_bit)
        REFERENCES smart_serial_bit_map(byte_dec, bit)
);
CREATE TABLE td_signal_state (
    area_id      TEXT NOT NULL,
    address      INTEGER NOT NULL,
    value        INTEGER NOT NULL,
    updated_at   TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    PRIMARY KEY (area_id, address)
);
CREATE TABLE hex_byte (
    hex TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE smart_serial_observed_exceptions (
    byte_dec      INTEGER NOT NULL,
    bit           INTEGER NOT NULL,
    td_address    TEXT NOT NULL,
    observed      INTEGER NOT NULL DEFAULT 1,
    first_note    TEXT,
    status        TEXT NOT NULL DEFAULT 'undocumented',
    PRIMARY KEY (byte_dec, bit)
);
CREATE TABLE IF NOT EXISTS "td_events" (
	"id"	INTEGER,
	"ts_ms"	INTEGER NOT NULL,
	"ts_iso"	TEXT NOT NULL,
	"area"	TEXT NOT NULL,
	"msg_type"	TEXT NOT NULL,
	"headcode"	TEXT,
	"from_berth"	TEXT,
	"to_berth"	TEXT,
	"address"	TEXT,
	"data"	TEXT,
	"address_int"	INTEGER,
	"data_int"	INTEGER,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "td_signal_events" (
	"id"	INTEGER,
	"ts_ms"	INTEGER NOT NULL,
	"ts_iso"	TEXT NOT NULL,
	"td_area"	TEXT,
	"msg_type"	TEXT NOT NULL,
	"address"	TEXT,
	"data"	TEXT,
	"address_int"	INTEGER,
	"data_int"	INTEGER,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE VIEW v_smart_serial_current AS
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
 AND m.bit = s.bit
/* v_smart_serial_current(updated_ts,interlocking,byte_dec,byte_hex,bit,bit_mask_hex,state,section,function_type,function,location_context,source_byte_value) */;
CREATE VIEW v_smart_signals AS
SELECT
    interlocking,
    byte_dec,
    printf('%02X', byte_dec) AS byte_hex,
    bit,
    (1 << bit) AS bit_mask,
    printf('%02X', (1 << bit)) AS bit_mask_hex,
    function AS signal
FROM smart_serial_bit_map
WHERE function_type = 'SIGNALS'
  AND function IS NOT NULL
/* v_smart_signals(interlocking,byte_dec,byte_hex,bit,bit_mask,bit_mask_hex,signal) */;
CREATE VIEW v_td_signal_transitions AS

WITH ordered AS (
    SELECT
        id,
        timestamp,
        area_id,
        address,
        address_int,
        value,
        value_int,
        source_type,
        raw_message_id,

        LAG(value_int) OVER (
            PARTITION BY area_id, address_int
            ORDER BY timestamp, id
        ) AS previous_value

    FROM td_signal_bytes
)

SELECT
    id,
    timestamp,
    area_id,
    address,
    address_int,

    previous_value,
    value_int AS new_value,

    printf('%02X', previous_value) AS previous_hex,
    printf('%02X', value_int) AS new_hex,

    ((previous_value | value_int)
      - (previous_value & value_int)) AS changed_bits,

    source_type,
    raw_message_id

FROM ordered

WHERE previous_value IS NOT NULL
  AND previous_value <> value_int
/* v_td_signal_transitions(id,timestamp,area_id,address,address_int,previous_value,new_value,previous_hex,new_hex,changed_bits,source_type,raw_message_id) */;
CREATE VIEW v_td_signal_decoded AS
SELECT
    b.timestamp,
    b.area_id,
    b.address,
    b.address_int,
    b.value,
    b.value_int,
    b.source_type,

    m.bit,
    m.interlocking,
    m.section,
    m.function_type,
    m.function,

    CASE
        WHEN (b.value_int & (1 << m.bit)) <> 0
        THEN 1
        ELSE 0
    END AS state

FROM td_signal_bytes b

JOIN smart_serial_bit_map m
  ON m.byte_dec = b.address_int

WHERE b.area_id = 'EK'
  AND m.function IS NOT NULL
  AND m.function <> ''
/* v_td_signal_decoded(timestamp,area_id,address,address_int,value,value_int,source_type,bit,interlocking,section,function_type,function,state) */;
CREATE VIEW v_td_signal_function_state AS
SELECT
    s.area_id,

    printf('%02X', s.address) AS td_address,
    s.address                AS address_int,

    m.bit,
    printf('%02X', (1 << m.bit)) AS bit_mask,

    m.interlocking,
    m.section,
    m.function_type,
    m.function,
    m.location_context,

    CASE
        WHEN (s.value & (1 << m.bit)) <> 0
        THEN 1
        ELSE 0
    END AS state,

    s.value                  AS byte_value_int,
    printf('%02X', s.value) AS byte_value_hex,

    s.updated_at,
    s.source_type

FROM td_signal_state s

JOIN smart_serial_bit_map m
  ON m.byte_dec = s.address

WHERE s.area_id = 'EK'
  AND m.function IS NOT NULL
  AND m.function <> ''
/* v_td_signal_function_state(area_id,td_address,address_int,bit,bit_mask,interlocking,section,function_type,function,location_context,state,byte_value_int,byte_value_hex,updated_at,source_type) */;
CREATE VIEW v_td_signal_function_transitions AS
WITH ordered AS (
    SELECT
        id,
        timestamp,
        area_id,
        address,
        address_int,
        value,
        value_int,
        source_type,
        raw_message_id,

        LAG(value_int) OVER (
            PARTITION BY area_id, address_int
            ORDER BY timestamp, id
        ) AS previous_value

    FROM td_signal_bytes
),

byte_changes AS (
    SELECT
        id,
        timestamp,
        area_id,
        address,
        address_int,

        previous_value,
        value_int AS new_value,

        ((previous_value | value_int)
         - (previous_value & value_int)) AS changed_bits,

        source_type,
        raw_message_id

    FROM ordered

    WHERE previous_value IS NOT NULL
      AND previous_value <> value_int
)

SELECT
    c.id,
    c.timestamp,
    c.area_id,

    c.address AS td_address,
    c.address_int,

    m.bit,
    printf('%02X', (1 << m.bit)) AS bit_mask,

    m.interlocking,
    m.section,
    m.function_type,
    m.function,
    m.location_context,

    CASE
        WHEN (c.previous_value & (1 << m.bit)) <> 0
        THEN 1
        ELSE 0
    END AS previous_state,

    CASE
        WHEN (c.new_value & (1 << m.bit)) <> 0
        THEN 1
        ELSE 0
    END AS new_state,

    printf('%02X', c.previous_value) AS previous_byte_hex,
    printf('%02X', c.new_value)      AS new_byte_hex,

    c.source_type,
    c.raw_message_id

FROM byte_changes c

JOIN smart_serial_bit_map m
  ON m.byte_dec = c.address_int

WHERE c.area_id = 'EK'

  AND m.function IS NOT NULL
  AND m.function <> ''

  AND (c.changed_bits & (1 << m.bit)) <> 0
/* v_td_signal_function_transitions(id,timestamp,area_id,td_address,address_int,bit,bit_mask,interlocking,section,function_type,function,location_context,previous_state,new_state,previous_byte_hex,new_byte_hex,source_type,raw_message_id) */;
CREATE VIEW v_td_signal_undocumented_transitions AS
WITH ordered AS (
    SELECT
        id,
        timestamp,
        area_id,
        address,
        address_int,
        value_int,

        LAG(value_int) OVER (
            PARTITION BY area_id, address_int
            ORDER BY timestamp, id
        ) AS previous_value,

        source_type,
        raw_message_id

    FROM td_signal_bytes
),

changes AS (
    SELECT
        *,
        ((previous_value | value_int)
         - (previous_value & value_int)) AS changed_bits
    FROM ordered
    WHERE previous_value IS NOT NULL
      AND previous_value <> value_int
),

bits(bit, mask) AS (
    VALUES
        (0,1),(1,2),(2,4),(3,8),
        (4,16),(5,32),(6,64),(7,128)
)

SELECT
    c.timestamp,
    c.area_id,
    c.address AS td_address,
    c.address_int,
    b.bit,

    CASE
        WHEN (c.previous_value & b.mask) <> 0
        THEN 1 ELSE 0
    END AS previous_state,

    CASE
        WHEN (c.value_int & b.mask) <> 0
        THEN 1 ELSE 0
    END AS new_state,

    c.source_type,
    c.raw_message_id

FROM changes c

CROSS JOIN bits b

LEFT JOIN smart_serial_bit_map m
  ON m.byte_dec = c.address_int
 AND m.bit      = b.bit

WHERE c.area_id = 'EK'
  AND (c.changed_bits & b.mask) <> 0
  AND (
      m.function IS NULL
      OR m.function = ''
  )
/* v_td_signal_undocumented_transitions(timestamp,area_id,td_address,address_int,bit,previous_state,new_state,source_type,raw_message_id) */;
CREATE INDEX idx_td_berth_ts ON td_berth_events(ts_ms);
CREATE INDEX idx_td_berth_area_hc_ts ON td_berth_events(td_area, headcode, ts_ms);
CREATE INDEX idx_td_berth_movements_ts ON td_berth_movements(ts_ms);
CREATE INDEX idx_td_berth_movements_area_ts ON td_berth_movements(td_area, ts_ms);
CREATE INDEX idx_td_berth_movements_area_hc_ts ON td_berth_movements(td_area, headcode, ts_ms);
CREATE UNIQUE INDEX idx_td_berth_movements_dedupe
                    ON td_berth_movements(td_area, headcode, ts_ms, from_berth, to_berth);
CREATE INDEX idx_td_sclass_state_area_ts ON td_sclass_state(td_area, last_seen_ts);
CREATE INDEX idx_td_sclass_changes_ts ON td_sclass_changes(ts_ms);
CREATE INDEX idx_td_sclass_changes_area_ts ON td_sclass_changes(td_area, ts_ms);
CREATE INDEX idx_td_sclass_changes_area_addr_ts ON td_sclass_changes(td_area, address, ts_ms);
CREATE INDEX idx_trust_state_headcode ON trust_state(headcode);
CREATE INDEX idx_vstp_state_headcode ON vstp_state(headcode);
CREATE INDEX idx_trust_messages_train_id ON trust_messages(train_id);
CREATE INDEX idx_trust_messages_actual_ts ON trust_messages(actual_timestamp_ms);
CREATE INDEX idx_trust_messages_toc_code ON trust_messages(toc_code);
CREATE INDEX idx_vstp_schedules_uid ON vstp_schedules(uid);
CREATE INDEX idx_vstp_loc_uid ON vstp_schedule_locations(uid);
CREATE INDEX idx_cif_schedules_uid ON cif_schedules(uid);
CREATE INDEX idx_cif_schedules_toc ON cif_schedules(toc_code);
CREATE INDEX idx_cif_schedules_headcode ON cif_schedules(CIF_headcode);
CREATE INDEX idx_cif_schedules_created_ts ON cif_schedules(created_at_ts);
CREATE INDEX idx_cif_loc_uid ON cif_schedule_locations(uid);
CREATE INDEX idx_cif_loc_tiploc ON cif_schedule_locations(tiploc);
CREATE INDEX idx_cif_loc_created_ts ON cif_schedule_locations(created_at_ts);
CREATE INDEX idx_toc_td_areas_toc_code ON toc_td_areas(toc_code);
CREATE INDEX idx_toc_td_areas_td_area ON toc_td_areas(td_area);
CREATE INDEX idx_corpus_tiploc ON corpus_locations(tiploc) WHERE tiploc IS NOT NULL;
CREATE INDEX idx_corpus_stanox ON corpus_locations(stanox) WHERE stanox IS NOT NULL;
CREATE INDEX idx_corpus_crs ON corpus_locations(crs) WHERE crs IS NOT NULL;
CREATE INDEX idx_smart_stanox ON smart_berths(stanox) WHERE stanox IS NOT NULL;
CREATE UNIQUE INDEX idx_td_sclass_movement_obs_unique
                    ON td_sclass_movement_observations(movement_event_id, change_event_id);
CREATE INDEX idx_td_sclass_movement_obs_area_ts
                    ON td_sclass_movement_observations(td_area, movement_ts_ms);
CREATE INDEX idx_td_sclass_movement_obs_bit
                    ON td_sclass_movement_observations(td_area, address, byte_offset, bit, change_ts_ms);
CREATE INDEX idx_td_sclass_movement_obs_mov_ts
                    ON td_sclass_movement_observations(td_area, from_berth, to_berth, movement_ts_ms);
CREATE INDEX idx_td_sclass_movement_scores_area_ts
                    ON td_sclass_movement_scores(td_area, last_seen_ts_ms);
CREATE UNIQUE INDEX idx_td_sclass_lab_annotations_unique
                    ON td_sclass_lab_annotations(
                        relation_type, td_area, address, byte_offset, bit, from_berth, to_berth
                    );
CREATE INDEX idx_td_sclass_lab_annotations_area_state
                    ON td_sclass_lab_annotations(td_area, relation_type, state);
CREATE INDEX idx_physical_signal_mappings_area_status
                    ON physical_signal_mappings(td_area, verification_status, updated_at_utc DESC);
CREATE INDEX idx_physical_signal_mappings_bit
                    ON physical_signal_mappings(td_area, address, byte_offset, bit, updated_at_utc DESC);
CREATE INDEX idx_physical_signal_mappings_signal
                    ON physical_signal_mappings(physical_signal_number, td_area);
CREATE INDEX idx_topology_nodes_area_type
                    ON topology_nodes(td_area, node_type, verification_status);
CREATE INDEX idx_topology_edges_area_from
                    ON topology_edges(td_area, from_node_id, verification_status);
CREATE INDEX idx_topology_edges_area_to
                    ON topology_edges(td_area, to_node_id, verification_status);
CREATE INDEX idx_topology_edge_evidence_edge
                    ON topology_edge_evidence(edge_id);
CREATE INDEX idx_bso_edge
                ON berth_signal_observations(td_area, from_berth, to_berth, step_timestamp);
CREATE INDEX idx_bso_addr
                ON berth_signal_observations(td_area, address, signal_timestamp);
CREATE UNIQUE INDEX idx_bso_unique
                ON berth_signal_observations(td_area, step_timestamp, signal_timestamp, address);
CREATE INDEX idx_bss_edge
                ON berth_signal_scores(td_area, from_berth, to_berth, score DESC);
CREATE INDEX idx_td_signal_bytes_lookup
                    ON td_signal_bytes(area_id, address, timestamp);
CREATE INDEX idx_smart_serial_bit_map_function
    ON smart_serial_bit_map(function);
CREATE INDEX idx_smart_serial_bit_map_interlocking_section
    ON smart_serial_bit_map(interlocking, section);
CREATE INDEX idx_smart_serial_byte_events_byte_ts
    ON smart_serial_byte_events(byte_dec, event_ts);
CREATE INDEX idx_smart_serial_bit_events_key_ts
    ON smart_serial_bit_events(byte_dec, bit, event_ts);
CREATE INDEX idx_td_events_area_headcode_ts
                    ON td_events(area, headcode, ts_ms);
CREATE INDEX idx_td_events_area_ts ON td_events(area, ts_ms);
CREATE INDEX idx_td_events_ts ON td_events(ts_ms);
CREATE INDEX idx_td_signal_area_ts ON td_signal_events(td_area, ts_ms);
CREATE INDEX idx_td_signal_ts ON td_signal_events(ts_ms);
CREATE TABLE berths (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id         TEXT NOT NULL,
    berth_code      TEXT NOT NULL,
    display_order   INTEGER NOT NULL,
    label           TEXT NOT NULL,
    description     TEXT,
    previous_berth_id INTEGER,
    next_berth_id     INTEGER,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),

    UNIQUE (area_id, berth_code),
    UNIQUE (area_id, display_order),

    FOREIGN KEY (previous_berth_id) REFERENCES berths(id),
    FOREIGN KEY (next_berth_id) REFERENCES berths(id)
);
CREATE TABLE signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id         TEXT NOT NULL,
    signal_code     TEXT NOT NULL,
    display_name    TEXT,
    smart_byte_dec  INTEGER,
    smart_bit       INTEGER,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),

    UNIQUE (area_id, signal_code)
);
CREATE TABLE berth_signals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    berth_id          INTEGER NOT NULL,
    signal_id         INTEGER NOT NULL,
    relationship_type TEXT NOT NULL,   -- protecting, starting, advance, shunt, etc.
    priority          INTEGER NOT NULL DEFAULT 0,

    UNIQUE (berth_id, signal_id, relationship_type),

    FOREIGN KEY (berth_id) REFERENCES berths(id) ON DELETE CASCADE,
    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
);
CREATE TABLE smart_function_map (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    interlocking      INTEGER NOT NULL,
    byte_dec          INTEGER NOT NULL,
    bit               INTEGER NOT NULL CHECK (bit BETWEEN 0 AND 7),
    bit_mask          INTEGER NOT NULL,
    section           TEXT,
    function_type     TEXT,
    function          TEXT,
    location_context  TEXT,

    UNIQUE (interlocking, byte_dec, bit)
);
CREATE TABLE signal_state_live (
    signal_id       INTEGER PRIMARY KEY,
    state           INTEGER NOT NULL CHECK (state IN (0, 1)),
    aspect          TEXT,
    last_change_at  TEXT,
    source_timestamp TEXT,
    raw_value_int   INTEGER,

    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
);
CREATE TABLE berth_occupancy_live (
    berth_id        INTEGER PRIMARY KEY,
    headcode        TEXT,
    occupied        INTEGER NOT NULL DEFAULT 0 CHECK (occupied IN (0, 1)),
    last_seen_at    TEXT,
    entered_at      TEXT,
    exited_at       TEXT,
    source_timestamp TEXT,

    FOREIGN KEY (berth_id) REFERENCES berths(id) ON DELETE CASCADE
);
CREATE TABLE berth_live_state (
    berth_id        INTEGER PRIMARY KEY,
    headcode        TEXT,
    signal_code     TEXT,
    signal_state    INTEGER,
    updated_at      TEXT,

    FOREIGN KEY (berth_id) REFERENCES berths(id) ON DELETE CASCADE
);
CREATE TABLE berth_transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    headcode        TEXT NOT NULL,
    from_berth_id   INTEGER,
    to_berth_id     INTEGER,
    transitioned_at  TEXT NOT NULL,
    confidence      REAL,
    trigger_type    TEXT,   -- TD, route, stepping, inferred, etc.

    FOREIGN KEY (from_berth_id) REFERENCES berths(id),
    FOREIGN KEY (to_berth_id) REFERENCES berths(id)
);
CREATE TABLE signal_state_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER NOT NULL,
    state           INTEGER NOT NULL CHECK (state IN (0, 1)),
    aspect          TEXT,
    changed_at      TEXT NOT NULL,
    source_timestamp TEXT,
    raw_value_int   INTEGER,

    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
);
CREATE TABLE berth_occupancy_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    berth_id        INTEGER NOT NULL,
    headcode        TEXT,
    occupied        INTEGER NOT NULL CHECK (occupied IN (0, 1)),
    changed_at      TEXT NOT NULL,
    source_timestamp TEXT,

    FOREIGN KEY (berth_id) REFERENCES berths(id) ON DELETE CASCADE
);
CREATE INDEX idx_berths_area_order
    ON berths(area_id, display_order);
CREATE INDEX idx_berth_signals_berth
    ON berth_signals(berth_id);
CREATE INDEX idx_berth_signals_signal
    ON berth_signals(signal_id);
CREATE INDEX idx_signals_area_code
    ON signals(area_id, signal_code);
CREATE INDEX idx_smart_function_map_lookup
    ON smart_function_map(interlocking, byte_dec, bit);
CREATE INDEX idx_signal_state_history_signal_time
    ON signal_state_history(signal_id, changed_at);
CREATE INDEX idx_berth_occupancy_history_berth_time
    ON berth_occupancy_history(berth_id, changed_at);
CREATE INDEX idx_berth_transitions_headcode_time
    ON berth_transitions(headcode, transitioned_at);
CREATE VIEW v_berth_board AS
WITH berth_signal_ranked AS (
    SELECT
        bs.berth_id,
        bs.signal_id,
        bs.relationship_type,
        bs.priority,
        s.signal_code,
        s.display_name,
        s.smart_byte_dec,
        s.smart_bit,
        ssl.state AS signal_state,
        ssl.aspect AS signal_aspect,
        ssl.last_change_at AS signal_last_change_at,
        ssl.source_timestamp AS signal_source_timestamp,
        ROW_NUMBER() OVER (
            PARTITION BY bs.berth_id
            ORDER BY bs.priority ASC, bs.id ASC
        ) AS rn
    FROM berth_signals bs
    JOIN signals s
      ON s.id = bs.signal_id
    LEFT JOIN signal_state_live ssl
      ON ssl.signal_id = s.id
    WHERE s.active = 1
),
berth_signal_agg AS (
    SELECT
        berth_id,
        GROUP_CONCAT(signal_code, ', ') AS associated_signals,
        GROUP_CONCAT(
            CASE
                WHEN signal_state IS NULL THEN signal_code || ':?'
                WHEN signal_state = 1 THEN signal_code || ':1'
                ELSE signal_code || ':0'
            END,
            ' | '
        ) AS signal_state_summary,
        MAX(signal_last_change_at) AS last_signal_change_at
    FROM berth_signal_ranked
    GROUP BY berth_id
),
primary_signal AS (
    SELECT *
    FROM berth_signal_ranked
    WHERE rn = 1
)
SELECT
    b.id AS berth_id,
    b.area_id,
    b.berth_code,
    b.display_order,
    b.label,
    b.description,
    b.active,

    bss.associated_signals,
    bss.signal_state_summary,
    bss.last_signal_change_at,

    ps.signal_id AS primary_signal_id,
    ps.signal_code AS primary_signal_code,
    ps.display_name AS primary_signal_name,
    ps.relationship_type AS primary_relationship_type,
    ps.priority AS primary_priority,
    ps.signal_state AS primary_signal_state,
    ps.signal_aspect AS primary_signal_aspect,
    ps.signal_last_change_at AS primary_signal_last_change_at,

    bhs.headcode,
    bhs.occupied,
    bhs.last_seen_at,
    bhs.entered_at,
    bhs.exited_at,
    bhs.source_timestamp AS berth_source_timestamp,

    CASE
        WHEN bhs.occupied = 1 THEN 'occupied'
        ELSE 'empty'
    END AS berth_status

FROM berths b
LEFT JOIN berth_signal_agg bss
  ON bss.berth_id = b.id
LEFT JOIN primary_signal ps
  ON ps.berth_id = b.id
LEFT JOIN berth_headcode_state bhs
  ON bhs.berth_id = b.id
WHERE b.area_id = 'EK'
  AND b.active = 1
ORDER BY b.display_order;
CREATE VIEW v_berth_board_detailed AS
WITH live_signals AS (
    SELECT
        ssl.signal_id,
        ssl.state AS signal_state,
        ssl.aspect AS signal_aspect,
        ssl.last_change_at AS signal_last_change_at,
        ssl.source_timestamp AS signal_source_timestamp,
        ssl.raw_value_int AS signal_raw_value_int
    FROM signal_state_live ssl
),
ranked_berth_signals AS (
    SELECT
        bs.id AS berth_signal_id,
        bs.berth_id,
        bs.signal_id,
        bs.relationship_type,
        bs.priority,

        s.signal_code,
        s.display_name,
        s.smart_byte_dec,
        s.smart_bit,
        s.active AS signal_active,

        ls.signal_state,
        ls.signal_aspect,
        ls.signal_last_change_at,
        ls.signal_source_timestamp,
        ls.signal_raw_value_int,

        ROW_NUMBER() OVER (
            PARTITION BY bs.berth_id
            ORDER BY bs.priority ASC, bs.id ASC
        ) AS berth_signal_rank
    FROM berth_signals bs
    JOIN signals s
      ON s.id = bs.signal_id
    LEFT JOIN live_signals ls
      ON ls.signal_id = s.id
),
berth_headcode AS (
    SELECT
        bhs.berth_id,
        bhs.headcode,
        bhs.occupied,
        bhs.last_seen_at,
        bhs.entered_at,
        bhs.exited_at,
        bhs.source_timestamp AS berth_source_timestamp
    FROM berth_headcode_state bhs
)
SELECT
    b.id AS berth_id,
    b.area_id,
    b.berth_code,
    b.display_order,
    b.label AS berth_label,
    b.description AS berth_description,
    b.active AS berth_active,

    bh.headcode,
    bh.occupied,
    bh.last_seen_at,
    bh.entered_at,
    bh.exited_at,
    bh.berth_source_timestamp,

    rbs.berth_signal_id,
    rbs.relationship_type,
    rbs.priority AS signal_priority,
    rbs.berth_signal_rank,

    rbs.signal_id,
    rbs.signal_code,
    rbs.display_name AS signal_display_name,
    rbs.smart_byte_dec,
    printf('%02X', rbs.smart_byte_dec) AS smart_byte_hex,
    rbs.smart_bit,
    printf('%02X', (1 << rbs.smart_bit)) AS smart_bit_mask_hex,
    rbs.signal_active,

    rbs.signal_state,
    CASE
        WHEN rbs.signal_state = 1 THEN 'ON'
        WHEN rbs.signal_state = 0 THEN 'OFF'
        ELSE 'UNKNOWN'
    END AS signal_state_text,
    rbs.signal_aspect,
    rbs.signal_last_change_at,
    rbs.signal_source_timestamp,
    rbs.signal_raw_value_int,

    m.interlocking,
    m.byte_dec AS mapped_byte_dec,
    printf('%02X', m.byte_dec) AS mapped_byte_hex,
    m.bit AS mapped_bit,
    printf('%02X', (1 << m.bit)) AS mapped_bit_mask_hex,
    m.section,
    m.function_type,
    m.function,
    m.location_context,

    CASE
        WHEN m.function IS NOT NULL AND m.function <> '' THEN 1
        ELSE 0
    END AS has_smart_mapping,

    CASE
        WHEN bh.occupied = 1 THEN 'occupied'
        ELSE 'empty'
    END AS berth_status

FROM berths b
LEFT JOIN berth_headcode bh
  ON bh.berth_id = b.id
LEFT JOIN ranked_berth_signals rbs
  ON rbs.berth_id = b.id
LEFT JOIN smart_serial_bit_map m
  ON m.byte_dec = rbs.smart_byte_dec
 AND m.bit = rbs.smart_bit
WHERE b.area_id = 'EK'
  AND b.active = 1
ORDER BY b.display_order, rbs.berth_signal_rank;
