-- Section 2.3 - remove exact duplicate copies from energy_readings
-- Run: docker exec -it timescaledb psql -U postgres -d smartgrid -f /sql/05_clean.sql
\set ECHO all
\timing on
\pset footer off
SET work_mem = '256MB';

-- 1. BEFORE: row count and total energy
SELECT count(*)                          AS rows_before,
       round(sum(energy_kwh)::numeric, 6) AS energy_kwh_before
FROM energy_readings;

-- 2. Duplicate groups: do all six values match?
SELECT count(*)                                                  AS duplicate_groups,
       count(*) FILTER (WHERE distinct_versions = 1)             AS identical_groups,
       count(*) FILTER (WHERE distinct_versions > 1)             AS conflicting_groups,
       sum(copies - 1)                                           AS extra_copies,
       round(sum(extra_energy)::numeric, 6)                      AS energy_in_extra_copies_kwh
FROM (
    SELECT meter_id, event_time,
           count(*) AS copies,
           count(DISTINCT (received_time, power_kw, voltage_v,
                           current_a, frequency_hz, energy_kwh)) AS distinct_versions,
           (count(*) - 1) * min(energy_kwh) AS extra_energy
    FROM energy_readings
    GROUP BY meter_id, event_time
    HAVING count(*) > 1
) g;

-- 3. List any conflicting groups for investigation (expected: none)
SELECT meter_id, event_time, received_time, power_kw, voltage_v, current_a, frequency_hz, energy_kwh
FROM energy_readings
WHERE (meter_id, event_time) IN (
    SELECT meter_id, event_time FROM energy_readings
    GROUP BY meter_id, event_time
    HAVING count(DISTINCT (received_time, power_kw, voltage_v,
                           current_a, frequency_hz, energy_kwh)) > 1)
ORDER BY meter_id, event_time
LIMIT 20;

-- 4. Delete extra copies of IDENTICAL groups only, in one transaction
BEGIN;

WITH identical_dups AS (
    SELECT meter_id, event_time
    FROM energy_readings
    GROUP BY meter_id, event_time
    HAVING count(*) > 1
       AND count(DISTINCT (received_time, power_kw, voltage_v,
                           current_a, frequency_hz, energy_kwh)) = 1
),
ranked AS (
    SELECT e.tableoid AS chunk_oid, e.ctid AS row_addr,
           row_number() OVER (PARTITION BY e.meter_id, e.event_time ORDER BY e.ctid) AS copy_no
    FROM energy_readings e
    JOIN identical_dups d USING (meter_id, event_time)
)
DELETE FROM energy_readings e
USING ranked r
WHERE e.tableoid = r.chunk_oid
  AND e.ctid     = r.row_addr
  AND r.copy_no  > 1;

-- checks inside the transaction
SELECT count(*) AS duplicate_pairs_remaining
FROM (SELECT 1 FROM energy_readings GROUP BY meter_id, event_time HAVING count(*) > 1) x;

COMMIT;

-- 5. AFTER: row count, total energy, verification against expected_counts.unique_readings
SELECT count(*)                          AS rows_after,
       round(sum(energy_kwh)::numeric, 6) AS energy_kwh_after,
       count(*) = 10643735               AS matches_unique_readings
FROM energy_readings;

-- 6. Rewrite the table without the deleted rows' dead space and refresh planner statistics,
--    so later size and timing comparisons in Sections 3-4 are fair.
VACUUM (FULL, ANALYZE) energy_readings;

SELECT round(table_bytes / 1024.0 ^ 2, 1) AS data_mb,
       round(index_bytes / 1024.0 ^ 2, 1) AS index_mb,
       round(total_bytes / 1024.0 ^ 2, 1) AS total_mb
FROM hypertable_detailed_size('energy_readings');