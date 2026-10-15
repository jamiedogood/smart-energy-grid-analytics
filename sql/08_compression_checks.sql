-- Section 4.1 - verify both compressed configurations
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/08_compression_checks.sql
\set ECHO all
\pset footer off

-- 1. Compression / columnstore settings per table (segmentby and orderby)
SELECT * FROM timescaledb_information.hypertable_columnstore_settings
WHERE hypertable::text IN ('energy_time_compressed', 'energy_meter_compressed');

-- 2. Matching indexes
SELECT tablename, indexname, indexdef FROM pg_indexes
WHERE tablename IN ('energy_time_compressed', 'energy_meter_compressed')
ORDER BY tablename, indexname;

-- 3. Policies: 48-hour threshold, currently paused
SELECT job_id, hypertable_name, proc_name, scheduled, config
FROM timescaledb_information.jobs
WHERE hypertable_name IN ('energy_time_compressed', 'energy_meter_compressed')
ORDER BY hypertable_name;

-- 4. Compressed chunks per table
SELECT hypertable_name,
       count(*)                                   AS total_chunks,
       count(*) FILTER (WHERE is_compressed)      AS compressed_chunks,
       min(range_start) FILTER (WHERE is_compressed) AS first_compressed_start,
       max(range_end)   FILTER (WHERE is_compressed) AS last_compressed_end
FROM timescaledb_information.chunks
WHERE hypertable_name IN ('energy_time_compressed', 'energy_meter_compressed')
GROUP BY hypertable_name;

-- 5. Same chunk time ranges compressed in both tables? (expect 0 mismatches)
SELECT count(*) AS range_mismatches
FROM (SELECT range_start, range_end FROM timescaledb_information.chunks
      WHERE hypertable_name = 'energy_time_compressed' AND is_compressed) t
FULL JOIN
     (SELECT range_start, range_end FROM timescaledb_information.chunks
      WHERE hypertable_name = 'energy_meter_compressed' AND is_compressed) m
USING (range_start, range_end)
WHERE t.range_start IS NULL OR m.range_start IS NULL;

-- 6. Data checks repeated from 3.1, relative to energy_readings
WITH s AS (
    SELECT 'energy_readings' AS tbl, count(*) AS row_count, count(DISTINCT meter_id) AS meters,
           min(event_time) AS earliest, max(event_time) AS latest,
           sum(power_kw) AS sum_power_kw, sum(energy_kwh) AS sum_energy_kwh FROM energy_readings
    UNION ALL
    SELECT 'energy_time_compressed', count(*), count(DISTINCT meter_id), min(event_time), max(event_time),
           sum(power_kw), sum(energy_kwh) FROM energy_time_compressed
    UNION ALL
    SELECT 'energy_meter_compressed', count(*), count(DISTINCT meter_id), min(event_time), max(event_time),
           sum(power_kw), sum(energy_kwh) FROM energy_meter_compressed
), b AS (SELECT * FROM s WHERE tbl = 'energy_readings')
SELECT s.tbl, s.row_count, s.meters, s.earliest, s.latest,
       round(s.sum_power_kw::numeric, 6)        AS sum_power_kw,
       round(s.sum_energy_kwh::numeric, 6)      AS sum_energy_kwh,
       abs(s.sum_power_kw - b.sum_power_kw)     AS power_diff,
       abs(s.sum_energy_kwh - b.sum_energy_kwh) AS energy_diff,
       s.row_count = 10643735 AND s.meters = 4000
         AND s.earliest = b.earliest AND s.latest = b.latest
         AND abs(s.sum_power_kw - b.sum_power_kw) <= 0.001
         AND abs(s.sum_energy_kwh - b.sum_energy_kwh) <= 0.001 AS all_checks_pass
FROM s CROSS JOIN b
ORDER BY s.tbl;