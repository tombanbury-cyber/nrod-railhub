create table main.td_berth_events
(
    id              INTEGER
        primary key autoincrement,
    msg_timestamp   INTEGER not null,
    received_at_utc TEXT    not null,
    msg_wrapper     TEXT,
    msg_type        TEXT,
    td_area         TEXT,
    headcode        TEXT,
    from_berth      TEXT,
    to_berth        TEXT
);
