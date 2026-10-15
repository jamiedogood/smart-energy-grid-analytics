-- Section 6.1 - three continuous aggregates over energy_readings
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/11_continuous_aggregates.sql
\set ECHO all
\timing on
\pset footer off

-- 1. Create (initially unpopulated: WITH NO DATA), real-time aggregation disabled
CREATE MATERIALIZED VIEW energy_15m_region
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket('15 minutes', r.event_time) AS bucket,
       m.region,
       sum(r.energy_kwh) AS energy_kwh_sum,
       sum(r.power_kw)   AS power_kw_sum,
       count(*)          AS reading_count
FROM energy_readings r
JOIN meters m ON r.meter_id = m.meter_id
GROUP BY bucket, m.region
WITH NO DATA;

CREATE MATERIALIZED VIEW energy_hourly_region
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket('1 hour', r.event_time, 'Africa/Kigali') AS bucket,
       m.region,
       sum(r.energy_kwh) AS energy_kwh_sum,
       sum(r.power_kw)   AS power_kw_sum,
       count(*)          AS reading_count
FROM energy_readings r
JOIN meters m ON r.meter_id = m.meter_id
GROUP BY bucket, m.region
WITH NO DATA;

CREATE MATERIALIZED VIEW energy_daily_meter
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket('1 day', event_time, 'Africa/Kigali') AS bucket,
       meter_id,
       sum(energy_kwh) AS energy_kwh_sum,
       sum(power_kw)   AS power_kw_sum,
       count(*)        AS reading_count
FROM energy_readings
GROUP BY bucket, meter_id
WITH NO DATA;

-- 2. Confirm they start empty
SELECT (SELECT count(*) FROM energy_15m_region)    AS rows_15m,
       (SELECT count(*) FROM energy_hourly_region) AS rows_hourly,
       (SELECT count(*) FROM energy_daily_meter)   AS rows_daily;

-- 3. Manual refresh of the whole generated period, one view at a time (timed)
SELECT clock_timestamp() AS refresh_15m_start;
CALL refresh_continuous_aggregate('energy_15m_region', '2026-09-01 00:00+02', '2026-09-29 00:00+02');
SELECT clock_timestamp() AS refresh_15m_end;

SELECT clock_timestamp() AS refresh_hourly_start;
CALL refresh_continuous_aggregate('energy_hourly_region', '2026-09-01 00:00+02', '2026-09-29 00:00+02');
SELECT clock_timestamp() AS refresh_hourly_end;

SELECT clock_timestamp() AS refresh_daily_start;
CALL refresh_continuous_aggregate('energy_daily_meter', '2026-09-01 00:00+02', '2026-09-29 00:00+02');
SELECT clock_timestamp() AS refresh_daily_end;

-- 4. Materialized size (data + index) and row counts
SELECT ca.view_name,
       round(s.table_bytes / 1024.0 ^ 2, 2) AS data_mb,
       round(s.index_bytes / 1024.0 ^ 2, 2) AS index_mb,
       round(s.total_bytes / 1024.0 ^ 2, 2) AS total_mb,
       ca.materialized_only
FROM timescaledb_information.continuous_aggregates ca,
     LATERAL hypertable_detailed_size(
         format('%I.%I', ca.materialization_hypertable_schema, ca.materialization_hypertable_name)::regclass) s
ORDER BY ca.view_name;

SELECT (SELECT count(*) FROM energy_15m_region)    AS rows_15m,
       (SELECT count(*) FROM energy_hourly_region) AS rows_hourly,
       (SELECT count(*) FROM energy_daily_meter)   AS rows_daily;

-- 5. Integrity: every view must account for all cleaned readings and energy
SELECT 'energy_15m_region' AS view_name, sum(reading_count) AS readings, round(sum(energy_kwh_sum)::numeric, 6) AS energy_kwh FROM energy_15m_region
UNION ALL
SELECT 'energy_hourly_region', sum(reading_count), round(sum(energy_kwh_sum)::numeric, 6) FROM energy_hourly_region
UNION ALL
SELECT 'energy_daily_meter', sum(reading_count), round(sum(energy_kwh_sum)::numeric, 6) FROM energy_daily_meter;

-- 6. Sample populated rows
SELECT * FROM energy_15m_region    ORDER BY bucket, region LIMIT 5;
SELECT * FROM energy_hourly_region ORDER BY bucket, region LIMIT 5;
SELECT * FROM energy_daily_meter   ORDER BY bucket, meter_id LIMIT 5;

-- 7. Refresh policies (added only after the measurements above)
SELECT add_continuous_aggregate_policy('energy_15m_region',
       start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '15 minutes');
SELECT add_continuous_aggregate_policy('energy_hourly_region',
       start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '15 minutes');
SELECT add_continuous_aggregate_policy('energy_daily_meter',
       start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '15 minutes');

-- 8. Evidence: view definitions, policy settings, background job status
SELECT view_name, materialized_only, view_definition
FROM timescaledb_information.continuous_aggregates ORDER BY view_name;

SELECT j.job_id, j.hypertable_name, j.schedule_interval, j.config, j.scheduled,
       js.last_run_status, js.next_start
FROM timescaledb_information.jobs j
LEFT JOIN timescaledb_information.job_stats js USING (job_id)
WHERE j.proc_name = 'policy_refresh_continuous_aggregate'
ORDER BY j.job_id;