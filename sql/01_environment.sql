-- Section 1.1 - environment check
-- Run inside the container:  psql -U postgres -f /sql/01_environment.sql
\set ECHO all
\timing on

-- 1. Create the assignment database (only if it does not exist yet)
SELECT 'CREATE DATABASE smartgrid'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'smartgrid') \gexec

-- 2. Make CAT the default session time zone for EVERY connection to this database
ALTER DATABASE smartgrid SET timezone TO 'Africa/Kigali';

-- 3. Reconnect so the new default applies, then enable TimescaleDB here
\c smartgrid
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 4. Evidence for the report
SELECT current_database();
SHOW timezone;
SELECT now() AS db_clock_cat;
SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';
SELECT version();

SELECT name, setting, unit
FROM pg_settings
WHERE name IN ('shared_buffers', 'work_mem', 'max_parallel_workers_per_gather', 'jit',
               'max_worker_processes', 'timescaledb.max_background_workers')
ORDER BY name;

-- 5. Why "align daily buckets to CAT midnight" matters.
--    01:30 CAT on 21 Sept is 23:30 UTC on 20 Sept.
SELECT time_bucket('1 day', TIMESTAMPTZ '2026-09-21 01:30+02', 'Africa/Kigali') AS cat_aligned_day,
       time_bucket('1 day', TIMESTAMPTZ '2026-09-21 01:30+02')                  AS default_utc_day;