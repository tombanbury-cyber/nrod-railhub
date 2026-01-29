CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE meta_downloads (
            dataset        TEXT PRIMARY KEY,
            source_url     TEXT NOT NULL,
            downloaded_at  TEXT NOT NULL,
            file_path      TEXT NOT NULL,
            sha256         TEXT NOT NULL
        );
CREATE TABLE corpus_tiploc (
            tiploc     TEXT,
            stanox     TEXT,
            crs        TEXT,   -- 3ALPHA
            nlc        INTEGER,
            uic        TEXT,
            nlcdesc    TEXT,
            nlcdesc16  TEXT,
            raw_json   TEXT,
            PRIMARY KEY (tiploc, stanox, crs, nlc)
        );
CREATE INDEX idx_corpus_tiploc_stanox ON corpus_tiploc(stanox);
CREATE INDEX idx_corpus_tiploc_crs    ON corpus_tiploc(crs);
CREATE INDEX idx_corpus_tiploc_tiploc ON corpus_tiploc(tiploc);
CREATE TABLE corpus_stanox (
            stanox   TEXT PRIMARY KEY,
            name     TEXT,
            raw_json TEXT
        );
CREATE TABLE corpus_crs (
            crs      TEXT PRIMARY KEY,
            name     TEXT,
            raw_json TEXT
        );
CREATE TABLE IF NOT EXISTS "datasets" (
	"table_name"	TEXT,
	"source_shp"	TEXT,
	"feature_count"	INTEGER,
	"shape_type"	INTEGER,
	"prj_wkt"	TEXT,
	"source_crs"	TEXT,
	"imported_at"	TEXT,
	PRIMARY KEY("table_name")
);
CREATE TABLE IF NOT EXISTS "nwr_elrs" (id INTEGER PRIMARY KEY AUTOINCREMENT, "asset_id" TEXT, "elr" TEXT, "start" REAL, "end" REAL, "version" REAL, "extracted" TEXT, geom_type TEXT, geom_wkt TEXT, geom_wkt_4326 TEXT, minx REAL, miny REAL, maxx REAL, maxy REAL, minlon REAL, minlat REAL, maxlon REAL, maxlat REAL, num_points INTEGER, num_parts INTEGER);
CREATE INDEX idx_nwr_elrs_elr ON "nwr_elrs"("elr");
CREATE INDEX idx_nwr_elrs_asset_id ON "nwr_elrs"("asset_id");
CREATE INDEX idx_nwr_elrs_bbox ON "nwr_elrs"(minx, miny, maxx, maxy);
CREATE INDEX idx_nwr_elrs_bbox_4326 ON "nwr_elrs"(minlon, minlat, maxlon, maxlat);
CREATE TABLE IF NOT EXISTS "nwr_trackcentrelines" (id INTEGER PRIMARY KEY AUTOINCREMENT, "asset_id" TEXT, "elr" TEXT, "track_id" TEXT, "start" REAL, "end" REAL, "version" REAL, "owner" TEXT, "extracted" TEXT, geom_type TEXT, geom_wkt TEXT, geom_wkt_4326 TEXT, minx REAL, miny REAL, maxx REAL, maxy REAL, minlon REAL, minlat REAL, maxlon REAL, maxlat REAL, num_points INTEGER, num_parts INTEGER);
CREATE INDEX idx_nwr_trackcentrelines_elr ON "nwr_trackcentrelines"("elr");
CREATE INDEX idx_nwr_trackcentrelines_asset_id ON "nwr_trackcentrelines"("asset_id");
CREATE INDEX idx_nwr_trackcentrelines_track_id ON "nwr_trackcentrelines"("track_id");
CREATE INDEX idx_nwr_trackcentrelines_bbox ON "nwr_trackcentrelines"(minx, miny, maxx, maxy);
CREATE INDEX idx_nwr_trackcentrelines_bbox_4326 ON "nwr_trackcentrelines"(minlon, minlat, maxlon, maxlat);
CREATE TABLE IF NOT EXISTS "nwr_tracknodes" (id INTEGER PRIMARY KEY AUTOINCREMENT, "asset_id" TEXT, "valency" REAL, "version" REAL, "extracted" TEXT, geom_type TEXT, geom_wkt TEXT, geom_wkt_4326 TEXT, minx REAL, miny REAL, maxx REAL, maxy REAL, minlon REAL, minlat REAL, maxlon REAL, maxlat REAL, num_points INTEGER, num_parts INTEGER);
CREATE INDEX idx_nwr_tracknodes_asset_id ON "nwr_tracknodes"("asset_id");
CREATE INDEX idx_nwr_tracknodes_bbox ON "nwr_tracknodes"(minx, miny, maxx, maxy);
CREATE INDEX idx_nwr_tracknodes_bbox_4326 ON "nwr_tracknodes"(minlon, minlat, maxlon, maxlat);
CREATE TABLE IF NOT EXISTS "nwr_waymarks" (id INTEGER PRIMARY KEY AUTOINCREMENT, "asset_id" TEXT, "elr" TEXT, "unit" TEXT, "value" REAL, "version" REAL, "extracted" TEXT, geom_type TEXT, geom_wkt TEXT, geom_wkt_4326 TEXT, minx REAL, miny REAL, maxx REAL, maxy REAL, minlon REAL, minlat REAL, maxlon REAL, maxlat REAL, num_points INTEGER, num_parts INTEGER);
CREATE INDEX idx_nwr_waymarks_elr ON "nwr_waymarks"("elr");
CREATE INDEX idx_nwr_waymarks_asset_id ON "nwr_waymarks"("asset_id");
CREATE INDEX idx_nwr_waymarks_bbox ON "nwr_waymarks"(minx, miny, maxx, maxy);
CREATE INDEX idx_nwr_waymarks_bbox_4326 ON "nwr_waymarks"(minlon, minlat, maxlon, maxlat);
CREATE VIEW v_station AS
SELECT
  TIPLOC,
  STANOX,
  NLCDESC AS station_name,
  '3ALPHA' AS crs
FROM corpus_tiploc
WHERE STANOX IS NOT NULL
/* v_station(tiploc,stanox,station_name,crs) */;
CREATE VIEW v_berth_edges_distinct AS
SELECT td_area, from_berth, to_berth,
       MIN(stanox) AS stanox,
       MIN(platform) AS platform
FROM v_berth_edges
GROUP BY td_area, from_berth, to_berth
/* v_berth_edges_distinct(td_area,from_berth,to_berth,stanox,platform) */;
CREATE VIEW v_station_berths AS
SELECT DISTINCT
  e.td_area,
  s.TIPLOC,
  s.station_name,
  s.crs,
  s.STANOX,
  e.from_berth AS berth
FROM v_berth_edges e
JOIN v_station s ON s.STANOX = e.stanox

UNION

SELECT DISTINCT
  e.td_area,
  s.TIPLOC,
  s.station_name,
  s.crs,
  s.STANOX,
  e.to_berth AS berth
FROM v_berth_edges e
JOIN v_station s ON s.STANOX = e.stanox
/* v_station_berths(td_area,tiploc,station_name,crs,stanox,berth) */;
CREATE VIEW v_station_berth_seeds AS
SELECT DISTINCT
  sb.TIPLOC,
  sb.td_area,
  sb.berth,
  sb.STANOX,
  s.station_name,
  s.crs
FROM v_station_berths sb
JOIN v_station s ON s.TIPLOC = sb.TIPLOC
/* v_station_berth_seeds(tiploc,td_area,berth,stanox,station_name,crs) */;
CREATE VIEW v_station_neighbours AS
WITH station_edges AS (
  SELECT
    s1.TIPLOC AS from_station,
    s1.station_name AS from_name,
    e.td_area,
    e.from_berth,
    e.to_berth,
    e.stanox
  FROM v_berth_edges_distinct e
  JOIN v_station s1 ON s1.STANOX = e.stanox
)
SELECT DISTINCT
  a.from_station,
  a.from_name,
  b.TIPLOC AS to_station,
  b.station_name AS to_name,
  a.td_area,
  a.from_berth,
  a.to_berth
FROM station_edges a
JOIN v_station_berths sb2
  ON sb2.td_area = a.td_area
 AND (sb2.berth = a.from_berth OR sb2.berth = a.to_berth)
JOIN v_station b
  ON b.TIPLOC = sb2.TIPLOC
WHERE b.TIPLOC <> a.from_station
/* v_station_neighbours(from_station,from_name,to_station,to_name,td_area,from_berth,to_berth) */;
CREATE TABLE smart_data (
            td_area           TEXT NOT NULL,
            step_type    TEXT,
            event        TEXT,

            from_berth   TEXT,
            to_berth     TEXT,

            stanox       TEXT,
            stanme       TEXT,

            platform     TEXT,
            route        TEXT,
            from_line    TEXT,
            to_line      TEXT,
            berthoffset  TEXT,
            comment      TEXT,

            raw_json     TEXT,

            UNIQUE (td_area, step_type, event, from_berth, to_berth, stanox,
                    platform, route, from_line, to_line, berthoffset, comment)
        );
CREATE VIEW v_berth_edges AS
SELECT
  td_area          AS td_area,
  from_berth    AS from_berth,
  to_berth      AS to_berth,
  stanox       AS stanox,
  event       AS event_code,
  platform     AS platform,
  route        AS route,
  berthoffset  AS berth_offset
FROM smart_data
WHERE from_berth IS NOT NULL
  AND to_berth   IS NOT NULL
  AND from_berth <> ''
  AND to_berth   <> ''
/* v_berth_edges(td_area,from_berth,to_berth,stanox,event_code,platform,route,berth_offset) */;
CREATE VIEW v_berths AS
            SELECT DISTINCT td_area, from_berth AS berth
            FROM smart_data
            WHERE from_berth IS NOT NULL
            UNION
            SELECT DISTINCT td_area, to_berth AS berth
            FROM smart_data
            WHERE to_berth IS NOT NULL
/* v_berths(td_area,berth) */;
CREATE VIEW v_smart_steps_with_location AS
        SELECT
          s.td_area, s.from_berth, s.to_berth,
          s.stanox, s.stanme,
          COALESCE(st.name, tl.nlcdesc, s.stanme) AS location_name,
          tl.tiploc, tl.crs,
          s.platform, s.route, s.from_line, s.to_line, s.berthoffset, s.comment
        FROM smart_data s
        LEFT JOIN corpus_stanox st ON st.stanox = s.stanox
        LEFT JOIN corpus_tiploc tl ON tl.stanox = s.stanox
/* v_smart_steps_with_location(td_area,from_berth,to_berth,stanox,stanme,location_name,tiploc,crs,platform,route,from_line,to_line,berthoffset,comment) */;
CREATE INDEX idx_smart_from
  ON smart_data (td_area, from_berth);
CREATE INDEX idx_smart_to
  ON smart_data (td_area, to_berth);
CREATE VIEW v_observed_steps AS
SELECT
  td_area,
  descr,
  from_berth,
  to_berth,
  msg_type,
  msg_timestamp
FROM td_events
WHERE
  from_berth IS NOT NULL AND from_berth <> ''
  AND to_berth   IS NOT NULL AND to_berth   <> ''
  AND msg_type IN ('CA','CC','CB')
/* v_observed_steps(td_area,descr,from_berth,to_berth,msg_type,msg_timestamp) */;
CREATE VIEW v_step_frequencies AS
SELECT
  td_area,
  from_berth,
  to_berth,
  COUNT(*)                 AS step_count,
  COUNT(DISTINCT descr)    AS train_count,
  MIN(msg_timestamp)       AS first_seen,
  MAX(msg_timestamp)       AS last_seen
FROM v_observed_steps
GROUP BY
  td_area,
  from_berth,
  to_berth
/* v_step_frequencies(td_area,from_berth,to_berth,step_count,train_count,first_seen,last_seen) */;
CREATE TABLE inferred_berth_steps(
  td_area TEXT,
  from_berth TEXT,
  to_berth TEXT,
  step_count,
  train_count,
  first_seen,
  last_seen,
  avg_steps_per_train
, from_stanox TEXT, from_stanme TEXT, to_stanox   TEXT, to_stanme   TEXT, confidence REAL);
CREATE TABLE inferred_berth_signal (
  td_area           TEXT NOT NULL,
  berth             TEXT NOT NULL,
  address           TEXT NOT NULL,

  moves_from_n      INTEGER NOT NULL,
  hits_moves_n      INTEGER NOT NULL,

  pct_from          REAL NOT NULL,   -- % of CA moves from berth that have >=1 SF(address) in window
  pct_any           REAL NOT NULL,   -- baseline % of ALL CA moves with >=1 SF(address) in window
  lift              REAL NOT NULL,

  avg_min_delta_ms  REAL NOT NULL,   -- mean of per-CA min delta to first SF(address)
  pct_within_1s     REAL NOT NULL,   -- % of hits where first SF is within 1s

  confidence        REAL NOT NULL,   -- 0..100
  rank              INTEGER NOT NULL, -- per berth, 1 = best

  PRIMARY KEY (td_area, berth, address)
);
CREATE TABLE td_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_timestamp   INTEGER NOT NULL,
                received_at_utc TEXT NOT NULL,

                msg_wrapper     TEXT,
                msg_type        TEXT,
                td_area         TEXT,
                descr           TEXT,

                from_berth      TEXT,
                to_berth        TEXT,
                address         TEXT,
                data            TEXT,

                msg_json        TEXT
            );
CREATE INDEX idx_td_events_area ON td_events(td_area);
CREATE INDEX idx_td_events_type ON td_events(msg_type);
CREATE TABLE berth_signal_observations (
                  id INTEGER PRIMARY KEY,
                  td_area TEXT NOT NULL,
                  step_event_id INTEGER,
                  step_timestamp INTEGER NOT NULL,
                  from_berth TEXT,
                  to_berth TEXT,
                  descr TEXT,
                  signal_event_id INTEGER,
                  signal_timestamp INTEGER NOT NULL,
                  address TEXT NOT NULL,
                  data TEXT,
                  dt_ms INTEGER NOT NULL,
                  weight REAL NOT NULL,
                  created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                  created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
CREATE INDEX idx_bso_edge
                ON berth_signal_observations(td_area, from_berth, to_berth, step_timestamp);
CREATE INDEX idx_bso_addr
                ON berth_signal_observations(td_area, address, signal_timestamp);
CREATE TABLE berth_signal_scores (
                  td_area TEXT NOT NULL,
                  from_berth TEXT NOT NULL,
                  to_berth TEXT NOT NULL,
                  address TEXT NOT NULL,
                  score REAL NOT NULL,
                  obs_count INTEGER NOT NULL,
                  last_seen_ts INTEGER,
                  last_seen_utc TEXT NOT NULL,
                  last_data TEXT,
                  PRIMARY KEY (td_area, from_berth, to_berth, address)
                );
CREATE INDEX idx_bss_edge
                ON berth_signal_scores(td_area, from_berth, to_berth, score DESC);
