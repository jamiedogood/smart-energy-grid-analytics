-- Section 1.2 - database structures
-- Run:  docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/02_tables.sql
\set ECHO all
\timing on

-- 1. Pilot table: an ordinary PostgreSQL table (no partitioning).
--    No primary key on purpose: duplicates are allowed at this stage.
CREATE TABLE IF NOT EXISTS energy_live (
    meter_id      BIGINT           NOT NULL,
    event_time    TIMESTAMPTZ      NOT NULL,
    received_time TIMESTAMPTZ      NOT NULL,
    power_kw      DOUBLE PRECISION,
    voltage_v     DOUBLE PRECISION,
    current_a     DOUBLE PRECISION,
    frequency_hz  DOUBLE PRECISION,
    energy_kwh    DOUBLE PRECISION
);

-- 2. Historical table: same eight columns, then turned into a hypertable.
CREATE TABLE IF NOT EXISTS energy_readings (
    meter_id      BIGINT           NOT NULL,
    event_time    TIMESTAMPTZ      NOT NULL,
    received_time TIMESTAMPTZ      NOT NULL,
    power_kw      DOUBLE PRECISION,
    voltage_v     DOUBLE PRECISION,
    current_a     DOUBLE PRECISION,
    frequency_hz  DOUBLE PRECISION,
    energy_kwh    DOUBLE PRECISION
);

-- Partition by event_time into one-day chunks.
-- TimescaleDB also creates a default index on (event_time DESC).
SELECT create_hypertable('energy_readings',
                         by_range('event_time', INTERVAL '1 day'),
                         if_not_exists => TRUE);

-- 3. Meter metadata (reference data, one row per meter).
CREATE TABLE IF NOT EXISTS meters (
    meter_id      BIGINT PRIMARY KEY,
    region        TEXT,
    customer_type TEXT,
    base_power_kw DOUBLE PRECISION,
    power_factor  DOUBLE PRECISION
);

-- 4. Evidence for the report
\d energy_live
\d energy_readings
\d meters

SELECT hypertable_name, column_name, column_type, time_interval
FROM timescaledb_information.dimensions
WHERE hypertable_name = 'energy_readings';
