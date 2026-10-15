-- Section 2.1 step 5 - checks after the full load
\set ECHO all
\timing on

SELECT count(*)                 AS total_rows,
       count(DISTINCT meter_id) AS distinct_meters,
       min(event_time)          AS earliest_event,
       max(event_time)          AS latest_event
FROM energy_readings;

SELECT round(table_bytes / 1024.0 ^ 2, 1) AS data_mb,
       round(index_bytes / 1024.0 ^ 2, 1) AS index_mb,
       round(toast_bytes / 1024.0 ^ 2, 1) AS toast_mb,
       round(total_bytes / 1024.0 ^ 2, 1) AS total_mb,
       pg_size_pretty(total_bytes)        AS total_pretty
FROM hypertable_detailed_size('energy_readings');

SELECT count(*) AS chunk_count,
       min(range_start) AS first_chunk_start,
       max(range_end)   AS last_chunk_end
FROM timescaledb_information.chunks
WHERE hypertable_name = 'energy_readings';