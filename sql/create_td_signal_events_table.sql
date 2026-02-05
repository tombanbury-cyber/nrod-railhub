create table main.td_signal_events
(
    id              INTEGER
        primary key autoincrement,
    msg_timestamp   INTEGER not null,
    received_at_utc TEXT    not null,
    msg_wrapper     TEXT,
    msg_type        TEXT,
    td_area         TEXT,
    address         TEXT,
    data            TEXT
);
