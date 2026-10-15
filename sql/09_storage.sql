-- Section 4.2 - storage and compression cost (baseline = energy_readings, 1-day chunks)
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/09_storage.sql
\set ECHO all
\pset footer off

-- 1. Sizes, ratio and saving per configuration
WITH sizes AS (
    SELECT h AS hypertable, s.table_bytes, s.index_bytes, s.toast_bytes, s.total_bytes
    FROM unnest(ARRAY['energy_readings', 'energy_time_compressed', 'energy_meter_compressed']) AS h,
         LATERAL hypertable_detailed_size(h::regclass) s
), base AS (SELECT total_bytes FROM sizes WHERE hypertable = 'energy_readings')
SELECT hypertable,
       round(table_bytes / 1024.0 ^ 2, 1)                               AS data_mb,
       round(index_bytes / 1024.0 ^ 2, 1)                               AS index_mb,
       round(toast_bytes / 1024.0 ^ 2, 1)                               AS toast_mb,
       round(sizes.total_bytes / 1024.0 ^ 2, 1)                         AS total_mb,
       round(base.total_bytes::numeric / sizes.total_bytes, 2)          AS compression_ratio,
       round(100.0 * (base.total_bytes - sizes.total_bytes) / base.total_bytes, 1) AS pct_saved
FROM sizes CROSS JOIN base
ORDER BY sizes.total_bytes DESC;

-- 2. Compressed chunk count and % of rows held in compressed chunks
--    (original row counts of those chunk ranges, counted in the uncompressed baseline)
SELECT c.hypertable_name,
       count(*) FILTER (WHERE c.is_compressed)                     AS compressed_chunks,
       count(*)                                                    AS total_chunks,
       sum(r.original_rows) FILTER (WHERE c.is_compressed)          AS rows_in_compressed_chunks,
       round(100.0 * sum(r.original_rows) FILTER (WHERE c.is_compressed) / 10643735, 2)
                                                                   AS pct_rows_compressed
FROM timescaledb_information.chunks c
CROSS JOIN LATERAL (SELECT count(*) AS original_rows FROM energy_readings e
                    WHERE e.event_time >= c.range_start AND e.event_time < c.range_end) r
WHERE c.hypertable_name IN ('energy_time_compressed', 'energy_meter_compressed')
GROUP BY c.hypertable_name
ORDER BY c.hypertable_name;

-- 3. Cross-check from TimescaleDB's own statistics (before vs after compression)
SELECT * FROM hypertable_columnstore_stats('energy_time_compressed');
SELECT * FROM hypertable_columnstore_stats('energy_meter_compressed');