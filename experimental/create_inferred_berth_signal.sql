CREATE TABLE IF NOT EXISTS inferred_berth_signal (
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
