DELETE FROM inferred_berth_signal WHERE td_area = 'EK' ;
-- Tune these
WITH
    params AS (
        SELECT
            'EK'  AS td_area,
            0000  AS pre_ms,         -- look BEFORE CA (ms)
            4000  AS post_ms,        -- look AFTER CA (ms)
            5    AS min_moves_from, -- lower initially
            5     AS min_hit_moves   -- lower initially
    ),

    moves AS (
        SELECT
            id            AS ca_id,
            msg_timestamp AS ca_ts,
            from_berth    AS berth
        FROM td_events, params
        WHERE td_events.td_area = params.td_area
          AND msg_type = 'CA'
          AND from_berth IS NOT NULL
    ),

    sf AS (
        SELECT
            id            AS sf_id,
            msg_timestamp AS sf_ts,
            address
        FROM td_events, params
        WHERE td_events.td_area = params.td_area
          AND msg_type = 'SF'
          AND address IS NOT NULL
    ),

    from_totals AS (
        SELECT berth, COUNT(*) AS moves_from_n
        FROM moves
        GROUP BY berth
    ),

-- All SF candidates within the +/- window, with signed delta
    sf_candidates AS (
        SELECT
            m.ca_id,
            m.berth,
            s.address,
            (s.sf_ts - m.ca_ts) AS delta_ms,                 -- signed: negative = before CA
            ABS(s.sf_ts - m.ca_ts) AS abs_delta_ms
        FROM moves m
                 JOIN sf s
                      ON s.sf_ts >= m.ca_ts - (SELECT pre_ms FROM params)
                          AND s.sf_ts <  m.ca_ts + (SELECT post_ms FROM params)
    ),

-- Pick the closest SF per (CA, address) to avoid chatty addresses dominating
    closest_sf_per_ca AS (
        SELECT
            ca_id,
            berth,
            address,
            MIN(abs_delta_ms) AS min_abs_delta_ms,
            MIN(delta_ms) FILTER (WHERE abs_delta_ms = (SELECT MIN(abs_delta_ms) FROM sf_candidates sc2
                                                        WHERE sc2.ca_id = sf_candidates.ca_id
                                                          AND sc2.address = sf_candidates.address)) AS closest_signed_delta_ms
        FROM sf_candidates
        GROUP BY ca_id, berth, address
    ),

-- Per (berth, address) stats
    berth_addr AS (
        SELECT
            berth,
            address,
            COUNT(*) AS hits_moves_n,                         -- number of CA moves with at least one SF hit
            AVG(min_abs_delta_ms) AS avg_min_abs_delta_ms,
            SUM(CASE WHEN min_abs_delta_ms <= 1000 THEN 1 ELSE 0 END) AS hits_within_1s,
            SUM(CASE WHEN closest_signed_delta_ms < 0 THEN 1 ELSE 0 END) AS hits_before,
            SUM(CASE WHEN closest_signed_delta_ms >= 0 THEN 1 ELSE 0 END) AS hits_after
        FROM closest_sf_per_ca
        GROUP BY berth, address
    ),

-- Baseline per address: how often does address appear near ANY CA?
    baseline AS (
        SELECT
            address,
            COUNT(*) AS ca_with_addr,
            (SELECT COUNT(*) FROM moves) AS total_ca
        FROM (
                 SELECT
                     ca_id,
                     address,
                     MIN(abs_delta_ms) AS min_abs_delta_ms
                 FROM sf_candidates
                 GROUP BY ca_id, address
             )
        GROUP BY address
    ),

    scored AS (
        SELECT
            (SELECT td_area FROM params) AS td_area,
            ba.berth,
            ba.address,
            ft.moves_from_n,
            ba.hits_moves_n,

            (CAST(ba.hits_moves_n AS REAL) / ft.moves_from_n) AS p_from,
            (CAST(b.ca_with_addr AS REAL) / b.total_ca)       AS p_any,

            (CAST(ba.hits_moves_n AS REAL) / ft.moves_from_n)
                / NULLIF((CAST(b.ca_with_addr AS REAL) / b.total_ca), 0.0) AS lift,

            ba.avg_min_abs_delta_ms,
            (100.0 * ba.hits_within_1s / ba.hits_moves_n) AS pct_within_1s,
            (100.0 * ba.hits_before / ba.hits_moves_n)    AS pct_before,
            (100.0 * ba.hits_after  / ba.hits_moves_n)    AS pct_after,

            -- confidence 0..100, no ln() required
            MIN(100.0,
                -- lift (capped)
                25.0 * MIN(4.0, ((CAST(ba.hits_moves_n AS REAL) / ft.moves_from_n)
                    / NULLIF((CAST(b.ca_with_addr AS REAL) / b.total_ca), 0.0)))  -- 0..~4

                -- support
                    + 20.0 * MIN(1.0, CAST(ba.hits_moves_n AS REAL) / 30.0)

                    -- hit rate
                    + 35.0 * (CAST(ba.hits_moves_n AS REAL) / ft.moves_from_n)

                    -- timing tightness: best at 0ms, fades to 0 by edge of window
                    + 20.0 * MAX(0.0, 1.0 - (ba.avg_min_abs_delta_ms / ( (SELECT pre_ms FROM params) + (SELECT post_ms FROM params) )))
            ) AS confidence
        FROM berth_addr ba
                 JOIN from_totals ft ON ft.berth = ba.berth
                 JOIN baseline b     ON b.address = ba.address
        WHERE ft.moves_from_n >= (SELECT min_moves_from FROM params)
          AND ba.hits_moves_n >= (SELECT min_hit_moves FROM params)
    ),

    ranked AS (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY td_area, berth
                ORDER BY confidence DESC, lift DESC, hits_moves_n DESC
                ) AS rank
        FROM scored
    )

-- materialise to your table (adds extra columns too)
INSERT OR REPLACE INTO inferred_berth_signal (
    td_area, berth, address,
    moves_from_n, hits_moves_n,
    pct_from, pct_any, lift,
    avg_min_delta_ms, pct_within_1s,
    confidence, rank
)
SELECT
    td_area,
    berth,
    address,
    moves_from_n,
    hits_moves_n,
    ROUND(100.0 * p_from, 2) AS pct_from,
    ROUND(100.0 * p_any,  2) AS pct_any,
    ROUND(lift, 2)           AS lift,
    ROUND(avg_min_abs_delta_ms, 1) AS avg_min_delta_ms,
    ROUND(pct_within_1s, 1)        AS pct_within_1s,
    ROUND(confidence, 1)           AS confidence,
    rank
FROM ranked;
SELECT * FROM inferred_berth_signal WHERE td_area = 'EK'
