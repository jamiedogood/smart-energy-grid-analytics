-- Section 6.2 - freshness and delayed arrivals in a separate test hypertable
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/12_freshness_test.sql
\set ECHO all
\pset footer off
\pset null '(no bucket)'

-- 0. Clean slate (the test is self-contained and rerunnable)
DROP MATERIALIZED VIEW IF EXISTS freshness_hourly;
DROP TABLE IF EXISTS freshness_test;

-- 1. Test hypertable + initially unpopulated hourly continuous aggregate, real-time OFF, no policy
CREATE TABLE freshness_test (
    event_time TIMESTAMPTZ      NOT NULL,
    energy_kwh DOUBLE PRECISION
);
SELECT create_hypertable('freshness_test', by_range('event_time'));

CREATE MATERIALIZED VIEW freshness_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket('1 hour', event_time, 'Africa/Kigali') AS bucket,
       sum(energy_kwh) AS energy_kwh_sum,
       count(*)        AS reading_count
FROM freshness_test
GROUP BY bucket
WITH NO DATA;

\d+ freshness_test
SELECT view_name, materialized_only, view_definition
FROM timescaledb_information.continuous_aggregates WHERE view_name = 'freshness_hourly';
SELECT count(*) AS policies_on_test_view FROM timescaledb_information.jobs
WHERE hypertable_name = (SELECT materialization_hypertable_name FROM timescaledb_information.continuous_aggregates
                         WHERE view_name = 'freshness_hourly');

-- 2. Record the actual test time, H and H - 48 h (session time zone = Africa/Kigali)
SELECT now()                                                       AS test_time,
       date_trunc('hour', now()) - INTERVAL '1 hour'               AS h,
       date_trunc('hour', now())                                   AS h_end,
       date_trunc('hour', now()) - INTERVAL '49 hours'             AS older_start,
       date_trunc('hour', now()) - INTERVAL '48 hours'             AS older_end,
       date_trunc('hour', now()) - INTERVAL '49 hours' + INTERVAL '10 minutes' AS ev_older_1kwh,
       date_trunc('hour', now()) - INTERVAL '49 hours' + INTERVAL '25 minutes' AS ev_older_05kwh,
       date_trunc('hour', now()) - INTERVAL '1 hour'  + INTERVAL '10 minutes' AS ev_h_2kwh
\gset
SELECT :'test_time'::timestamptz AS test_time, :'h'::timestamptz AS h, :'older_start'::timestamptz AS older_hour_start,
       :'ev_older_1kwh'::timestamptz AS insert1_time, :'ev_older_05kwh'::timestamptz AS insert2_time,
       :'ev_h_2kwh'::timestamptz AS insert3_time;

-- ===== STAGE 1: insert 1 kWh into the older hour, refresh ONLY that hour (real-time off) =====
INSERT INTO freshness_test VALUES (:'ev_older_1kwh'::timestamptz, 1.0);
CALL refresh_continuous_aggregate('freshness_hourly', :'older_start'::timestamptz, :'older_end'::timestamptz);

SELECT 'STAGE 1 aggregate' AS stage, v.label, v.hour, a.energy_kwh_sum, a.reading_count
FROM (VALUES ('older hour', :'older_start'::timestamptz), ('hour H', :'h'::timestamptz)) v(label, hour)
LEFT JOIN freshness_hourly a ON a.bucket = v.hour ORDER BY v.hour;

-- ===== STAGE 2: late 0.5 kWh into the older hour + 2 kWh into hour H, NO refresh (real-time off) =====
INSERT INTO freshness_test VALUES (:'ev_older_05kwh'::timestamptz, 0.5);
INSERT INTO freshness_test VALUES (:'ev_h_2kwh'::timestamptz, 2.0);

SELECT 'STAGE 2 raw table' AS stage, v.label, v.hour, sum(t.energy_kwh) AS energy_kwh_sum, count(t.event_time) AS reading_count
FROM (VALUES ('older hour', :'older_start'::timestamptz), ('hour H', :'h'::timestamptz)) v(label, hour)
LEFT JOIN freshness_test t ON t.event_time >= v.hour AND t.event_time < v.hour + INTERVAL '1 hour'
GROUP BY v.label, v.hour ORDER BY v.hour;

SELECT 'STAGE 2 aggregate (real-time off)' AS stage, v.label, v.hour, a.energy_kwh_sum, a.reading_count
FROM (VALUES ('older hour', :'older_start'::timestamptz), ('hour H', :'h'::timestamptz)) v(label, hour)
LEFT JOIN freshness_hourly a ON a.bucket = v.hour ORDER BY v.hour;

-- ===== STAGE 3: enable real-time aggregation, still no refresh =====
ALTER MATERIALIZED VIEW freshness_hourly SET (timescaledb.materialized_only = false);
SELECT view_name, materialized_only FROM timescaledb_information.continuous_aggregates WHERE view_name = 'freshness_hourly';

SELECT 'STAGE 3 aggregate (real-time on)' AS stage, v.label, v.hour, a.energy_kwh_sum, a.reading_count
FROM (VALUES ('older hour', :'older_start'::timestamptz), ('hour H', :'h'::timestamptz)) v(label, hour)
LEFT JOIN freshness_hourly a ON a.bucket = v.hour ORDER BY v.hour;

-- ===== STAGE 4: disable real-time again, manually refresh both complete hours =====
ALTER MATERIALIZED VIEW freshness_hourly SET (timescaledb.materialized_only = true);
CALL refresh_continuous_aggregate('freshness_hourly', :'older_start'::timestamptz, :'older_end'::timestamptz);
CALL refresh_continuous_aggregate('freshness_hourly', :'h'::timestamptz, :'h_end'::timestamptz);

SELECT 'STAGE 4 raw table' AS stage, v.label, v.hour, sum(t.energy_kwh) AS energy_kwh_sum, count(t.event_time) AS reading_count
FROM (VALUES ('older hour', :'older_start'::timestamptz), ('hour H', :'h'::timestamptz)) v(label, hour)
LEFT JOIN freshness_test t ON t.event_time >= v.hour AND t.event_time < v.hour + INTERVAL '1 hour'
GROUP BY v.label, v.hour ORDER BY v.hour;

SELECT 'STAGE 4 aggregate (after refresh, real-time off)' AS stage, v.label, v.hour, a.energy_kwh_sum, a.reading_count
FROM (VALUES ('older hour', :'older_start'::timestamptz), ('hour H', :'h'::timestamptz)) v(label, hour)
LEFT JOIN freshness_hourly a ON a.bucket = v.hour ORDER BY v.hour;

-- 5. Which hours could the 6.1 policy (start 3 days, end 1 hour) refresh at the test time?
SELECT :'test_time'::timestamptz - INTERVAL '3 days' AS policy_window_start,
       :'test_time'::timestamptz - INTERVAL '1 hour'  AS policy_window_end,
       :'older_start'::timestamptz >= :'test_time'::timestamptz - INTERVAL '3 days'
         AND :'older_end'::timestamptz <= :'test_time'::timestamptz - INTERVAL '1 hour' AS older_hour_in_window,
       :'h'::timestamptz >= :'test_time'::timestamptz - INTERVAL '3 days'
         AND :'h_end'::timestamptz <= :'test_time'::timestamptz - INTERVAL '1 hour'     AS hour_h_in_window;