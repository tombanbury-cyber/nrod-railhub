BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS "td_state" (
	"td_area"	TEXT NOT NULL,
	"headcode"	TEXT NOT NULL,
	"last_time_utc"	TEXT,
	"from_berth"	TEXT,
	"to_berth"	TEXT,
	"stanox"	TEXT,
	"location_name"	TEXT,
	"platform"	TEXT,
	"sched_dep"	TEXT,
	"sched_arr"	TEXT,
	"origin_name"	TEXT,
	"dest_name"	TEXT,
	"uid"	TEXT,
	PRIMARY KEY("td_area","headcode")
);
CREATE TABLE IF NOT EXISTS "td_event" (
	"id"	INTEGER,
	"ts_utc"	TEXT NOT NULL,
	"td_area"	TEXT,
	"headcode"	TEXT,
	"event_type"	TEXT NOT NULL,
	"from_berth"	TEXT,
	"to_berth"	TEXT,
	"raw_json"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "trust_state" (
	"train_id"	TEXT,
	"headcode"	TEXT,
	"uid"	TEXT,
	"toc_id"	TEXT,
	"last_event_time"	TEXT,
	"last_location"	TEXT,
	"last_delay_min"	INTEGER,
	"raw_json"	TEXT,
	PRIMARY KEY("train_id")
);
CREATE TABLE IF NOT EXISTS "vstp_state" (
	"uid"	TEXT,
	"headcode"	TEXT,
	"start_date"	TEXT,
	"end_date"	TEXT,
	"raw_json"	TEXT,
	PRIMARY KEY("uid","start_date")
);
CREATE INDEX IF NOT EXISTS "idx_td_event_ts" ON "td_event" (
	"ts_utc"
);
CREATE INDEX IF NOT EXISTS "idx_td_event_area_hc_ts" ON "td_event" (
	"td_area",
	"headcode",
	"ts_utc"
);
CREATE INDEX IF NOT EXISTS "idx_trust_state_headcode" ON "trust_state" (
	"headcode"
);
CREATE INDEX IF NOT EXISTS "idx_vstp_state_headcode" ON "vstp_state" (
	"headcode"
);
COMMIT;
