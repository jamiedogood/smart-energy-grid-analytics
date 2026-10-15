-- Section 3.2/3.3 - chunk overlap evidence + one actual execution plan (Q1) per configuration
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/07_explain_chunks.sql
\set ECHO all
\pset footer off

-- 1. How many chunks overlap each query's time filter?
SELECT hypertable_name,
       count(*) FILTER (WHERE range_start < TIMESTAMPTZ '2026-09-22 00:00+02'
                          AND range_end   > TIMESTAMPTZ '2026-09-21 00:00+02') AS q1_chunks_21sep,
       count(*) FILTER (WHERE range_start < TIMESTAMPTZ '2026-09-29 00:00+02'
                          AND range_end   > TIMESTAMPTZ '2026-09-22 00:00+02') AS q2_q4_chunks_22_28sep,
       count(*)                                                                AS q3_q5_chunks_all
FROM timescaledb_information.chunks
WHERE hypertable_name IN ('energy_readings_3h', 'energy_readings', 'energy_readings_week')
GROUP BY hypertable_name
ORDER BY q3_q5_chunks_all DESC;

-- 2. Q1 actual plan on each configuration
EXPLAIN (ANALYZE, BUFFERS)
SELECT time_bucket('1 hour', event_time, 'Africa/Kigali') AS hour_cat, avg(power_kw) AS avg_power_kw
FROM energy_readings_3h
WHERE event_time >= TIMESTAMPTZ '2026-09-21 00:00+02' AND event_time < TIMESTAMPTZ '2026-09-22 00:00+02'
GROUP BY 1 ORDER BY 1;

EXPLAIN (ANALYZE, BUFFERS)
SELECT time_bucket('1 hour', event_time, 'Africa/Kigali') AS hour_cat, avg(power_kw) AS avg_power_kw
FROM energy_readings
WHERE event_time >= TIMESTAMPTZ '2026-09-21 00:00+02' AND event_time < TIMESTAMPTZ '2026-09-22 00:00+02'
GROUP BY 1 ORDER BY 1;

EXPLAIN (ANALYZE, BUFFERS)
SELECT time_bucket('1 hour', event_time, 'Africa/Kigali') AS hour_cat, avg(power_kw) AS avg_power_kw
FROM energy_readings_week
WHERE event_time >= TIMESTAMPTZ '2026-09-21 00:00+02' AND event_time < TIMESTAMPTZ '2026-09-22 00:00+02'
GROUP BY 1 ORDER BY 1;