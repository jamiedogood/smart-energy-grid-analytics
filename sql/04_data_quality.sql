-- Section 2.2 - data quality diagnostics on energy_readings BEFORE cleaning
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/04_data_quality.sql
\set ECHO all
\timing on
\pset footer off

-- More memory for this diagnostic session only (does not change server settings or benchmarks)
SET work_mem = '256MB';

-- 0. One row per observed (meter_id, event_time) pair: copies and arrival time.
--    Duplicate copies are identical in all fields, so min(received_time) = the arrival time.
CREATE TEMP TABLE pairs AS
SELECT meter_id, event_time,
       count(*)           AS copies,
       min(received_time) AS received_time,
       max(received_time) AS max_received_time
FROM energy_readings
GROUP BY meter_id, event_time;
ANALYZE pairs;

-- 1. Sanity: every observed pair lies on the planned grid (right meters, period, 15-min slots)
SELECT count(*) FILTER (WHERE event_time <  TIMESTAMPTZ '2026-09-01 00:00+02'
                           OR event_time >= TIMESTAMPTZ '2026-09-29 00:00+02') AS outside_period,
       count(*) FILTER (WHERE extract(epoch FROM event_time)::bigint % 900 <> 0)  AS off_15min_slot,
       count(*) FILTER (WHERE meter_id NOT IN (SELECT meter_id FROM meters))       AS unknown_meter,
       count(*) FILTER (WHERE received_time <> max_received_time)                  AS copies_with_different_arrival
FROM pairs;

-- 2. How many copies per pair? (expect only 1 and 2)
SELECT copies, count(*) AS pairs FROM pairs GROUP BY copies ORDER BY copies;

-- 3. Arrival delay profile (normal = exactly 15 min; delayed = 30 to 180 min)
SELECT CASE WHEN received_time - event_time = INTERVAL '15 minutes' THEN 'normal (15 min)'
            WHEN received_time - event_time BETWEEN INTERVAL '30 minutes' AND INTERVAL '180 minutes'
                 THEN 'delayed (30-180 min)'
            ELSE 'other' END                           AS arrival_class,
       count(*)                                        AS unique_pairs,
       min(received_time - event_time)                 AS min_delay,
       max(received_time - event_time)                 AS max_delay
FROM pairs
GROUP BY 1 ORDER BY 1;

-- 4. Main measured counts and percentages
WITH m AS (
    SELECT 4000 * 28 * 96                                              AS planned,
           (SELECT count(*) FROM energy_readings)                      AS emitted,
           count(*)                                                    AS unique_pairs,
           sum(copies - 1)                                             AS extra_copies,
           count(*) FILTER (WHERE received_time > event_time + INTERVAL '15 minutes') AS delayed
    FROM pairs
)
SELECT planned, emitted, unique_pairs, extra_copies, delayed,
       planned - unique_pairs                                     AS missing,
       round(100.0 * (planned - unique_pairs) / planned, 4)       AS missing_pct,
       round(100.0 * extra_copies / unique_pairs, 4)              AS duplicate_pct,
       round(100.0 * delayed / unique_pairs, 4)                   AS delayed_pct,
       round(100.0 * unique_pairs / planned, 4)                   AS completeness_pct,
       unique_pairs + extra_copies = emitted                      AS check_unique_plus_extra_eq_emitted,
       planned - unique_pairs = 108265                            AS check_planned_minus_unique_eq_missing
FROM m;

-- 5. Missing counted independently: build the full planned grid and find empty cells (anti-join)
SELECT count(*) AS missing_pairs_from_grid
FROM meters m
CROSS JOIN generate_series(TIMESTAMPTZ '2026-09-01 00:00+02',
                           TIMESTAMPTZ '2026-09-28 23:45+02',
                           INTERVAL '15 minutes') AS g(event_time)
WHERE NOT EXISTS (SELECT 1 FROM pairs p
                  WHERE p.meter_id = m.meter_id AND p.event_time = g.event_time);

-- 6. Measured vs reference
WITH m AS (
    SELECT (SELECT count(*) FROM energy_readings) AS emitted,
           count(*) AS unique_pairs, sum(copies - 1) AS extra_copies,
           count(*) FILTER (WHERE received_time > event_time + INTERVAL '15 minutes') AS delayed
    FROM pairs
)
SELECT entry, measured, expected, measured = expected AS matches
FROM m, LATERAL (VALUES
    ('planned_readings',        4000 * 28 * 96,            10752000),
    ('emitted_readings',        emitted,                   10696848),
    ('unique_readings',         unique_pairs,              10643735),
    ('duplicate_readings',      extra_copies,              53113),
    ('delayed_unique_readings', delayed,                   213090),
    ('missing_readings',        4000 * 28 * 96 - unique_pairs, 108265)
) AS v(entry, measured, expected);

-- 7. Five meters with the most missing readings (2,688 planned per meter; ties broken by meter_id)
SELECT m.meter_id, m.region, m.customer_type,
       count(p.event_time)        AS observed_pairs,
       2688 - count(p.event_time) AS missing_readings
FROM meters m
LEFT JOIN pairs p USING (meter_id)
GROUP BY m.meter_id, m.region, m.customer_type
ORDER BY missing_readings DESC, m.meter_id
LIMIT 5;