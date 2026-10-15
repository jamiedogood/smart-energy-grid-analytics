-- Section 3.1 - three chunk-interval configurations of the cleaned data
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/06_chunk_tables.sql
\set ECHO all
\timing on
\pset footer off
SET maintenance_work_mem = '512MB';   -- faster index builds in this session only

-- 1. Two new hypertables with exactly the same eight columns and types
CREATE TABLE IF NOT EXISTS energy_readings_3h   (LIKE energy_readings INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
CREATE TABLE IF NOT EXISTS energy_readings_week (LIKE energy_readings INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

SELECT create_hypertable('energy_readings_3h',   by_range('event_time', INTERVAL '3 hours'),
                         create_default_indexes => FALSE, if_not_exists => TRUE);
SELECT create_hypertable('energy_readings_week', by_range('event_time', INTERVAL '1 week'),
                         create_default_indexes => FALSE, if_not_exists => TRUE);

-- 2. Copy the cleaned rows (only if the target is still empty, so reruns are safe)
INSERT INTO energy_readings_3h   SELECT * FROM energy_readings
WHERE NOT EXISTS (SELECT 1 FROM energy_readings_3h);
INSERT INTO energy_readings_week SELECT * FROM energy_readings
WHERE NOT EXISTS (SELECT 1 FROM energy_readings_week);

-- 3. Identical non-unique B-tree indexes on all three tables (built after loading = faster)
--    energy_readings already has energy_readings_event_time_idx ON (event_time DESC)
CREATE INDEX IF NOT EXISTS energy_readings_meter_time_idx      ON energy_readings      (meter_id, event_time DESC);
CREATE INDEX IF NOT EXISTS energy_readings_3h_event_time_idx   ON energy_readings_3h   (event_time DESC);
CREATE INDEX IF NOT EXISTS energy_readings_3h_meter_time_idx   ON energy_readings_3h   (meter_id, event_time DESC);
CREATE INDEX IF NOT EXISTS energy_readings_week_event_time_idx ON energy_readings_week (event_time DESC);
CREATE INDEX IF NOT EXISTS energy_readings_week_meter_time_idx ON energy_readings_week (meter_id, event_time DESC);

-- 4. Fresh statistics and visibility maps on all three, so the planner starts equal
VACUUM ANALYZE energy_readings;
VACUUM ANALYZE energy_readings_3h;
VACUUM ANALYZE energy_readings_week;

-- 5. Evidence: chunk interval, chunk count and range per table
SELECT d.hypertable_name, d.time_interval,
       count(c.chunk_name) AS chunk_count,
       min(c.range_start)  AS first_chunk_start,
       max(c.range_end)    AS last_chunk_end
FROM timescaledb_information.dimensions d
JOIN timescaledb_information.chunks c USING (hypertable_name)
WHERE d.hypertable_name IN ('energy_readings', 'energy_readings_3h', 'energy_readings_week')
GROUP BY d.hypertable_name, d.time_interval
ORDER BY d.time_interval;

-- 6. Evidence: indexes (method and sort order must match)
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE tablename IN ('energy_readings', 'energy_readings_3h', 'energy_readings_week')
ORDER BY tablename, indexname;

-- 7. Evidence: matching data checks (differences relative to energy_readings)
WITH s AS (
    SELECT 'energy_readings_3h' AS tbl, count(*) AS row_count, count(DISTINCT meter_id) AS meters,
           min(event_time) AS earliest, max(event_time) AS latest,
           sum(power_kw) AS sum_power_kw, sum(energy_kwh) AS sum_energy_kwh FROM energy_readings_3h
    UNION ALL
    SELECT 'energy_readings', count(*), count(DISTINCT meter_id), min(event_time), max(event_time),
           sum(power_kw), sum(energy_kwh) FROM energy_readings
    UNION ALL
    SELECT 'energy_readings_week', count(*), count(DISTINCT meter_id), min(event_time), max(event_time),
           sum(power_kw), sum(energy_kwh) FROM energy_readings_week
), b AS (SELECT * FROM s WHERE tbl = 'energy_readings')
SELECT s.tbl, s.row_count, s.meters, s.earliest, s.latest,
       round(s.sum_power_kw::numeric, 6)                         AS sum_power_kw,
       round(s.sum_energy_kwh::numeric, 6)                       AS sum_energy_kwh,
       abs(s.sum_power_kw - b.sum_power_kw)                      AS power_diff,
       abs(s.sum_energy_kwh - b.sum_energy_kwh)                  AS energy_diff,
       s.row_count = 10643735 AND s.meters = 4000
         AND s.earliest = b.earliest AND s.latest = b.latest
         AND abs(s.sum_power_kw - b.sum_power_kw) <= 0.001
         AND abs(s.sum_energy_kwh - b.sum_energy_kwh) <= 0.001   AS all_checks_pass
FROM s CROSS JOIN b
ORDER BY s.tbl;

-- 8. Sizes (useful later in 3.3)
SELECT h AS hypertable,
       round(table_bytes / 1024.0 ^ 2, 1) AS data_mb,
       round(index_bytes / 1024.0 ^ 2, 1) AS index_mb,
       round(total_bytes / 1024.0 ^ 2, 1) AS total_mb
FROM unnest(ARRAY['energy_readings_3h', 'energy_readings', 'energy_readings_week']) AS h,
     LATERAL hypertable_detailed_size(h::regclass);