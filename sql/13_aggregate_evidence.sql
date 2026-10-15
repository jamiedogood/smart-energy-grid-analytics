-- Section 6.1 evidence (read-only): views, sizes, row counts, integrity, samples
\set ECHO all
\pset footer off
SELECT view_name, materialized_only FROM timescaledb_information.continuous_aggregates ORDER BY view_name;
SELECT ca.view_name,
       round(s.table_bytes / 1024.0 ^ 2, 2) AS data_mb,
       round(s.index_bytes / 1024.0 ^ 2, 2) AS index_mb,
       round(s.total_bytes / 1024.0 ^ 2, 2) AS total_mb
FROM timescaledb_information.continuous_aggregates ca,
     LATERAL hypertable_detailed_size(format('%I.%I', ca.materialization_hypertable_schema,
                                             ca.materialization_hypertable_name)::regclass) s
WHERE ca.view_name IN ('energy_15m_region','energy_hourly_region','energy_daily_meter')
ORDER BY ca.view_name;
SELECT 'energy_15m_region' AS view_name, count(*) AS rows, sum(reading_count) AS readings, round(sum(energy_kwh_sum)::numeric, 6) AS energy_kwh FROM energy_15m_region
UNION ALL SELECT 'energy_hourly_region', count(*), sum(reading_count), round(sum(energy_kwh_sum)::numeric, 6) FROM energy_hourly_region
UNION ALL SELECT 'energy_daily_meter', count(*), sum(reading_count), round(sum(energy_kwh_sum)::numeric, 6) FROM energy_daily_meter;
SELECT * FROM energy_15m_region    ORDER BY bucket, region   LIMIT 5;
SELECT * FROM energy_hourly_region ORDER BY bucket, region   LIMIT 5;
SELECT * FROM energy_daily_meter   ORDER BY bucket, meter_id LIMIT 5;