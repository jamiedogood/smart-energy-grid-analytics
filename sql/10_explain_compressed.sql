-- Section 4.3 - actual Q4 plans on both compressed tables
\set ECHO all
\pset footer off

EXPLAIN (ANALYZE, BUFFERS)
SELECT sum(energy_kwh) AS energy_kwh, avg(power_kw) AS avg_power_kw, max(power_kw) AS peak_power_kw
FROM energy_time_compressed
WHERE meter_id = 1000000000
  AND event_time >= TIMESTAMPTZ '2026-09-22 00:00+02' AND event_time < TIMESTAMPTZ '2026-09-29 00:00+02';

EXPLAIN (ANALYZE, BUFFERS)
SELECT sum(energy_kwh) AS energy_kwh, avg(power_kw) AS avg_power_kw, max(power_kw) AS peak_power_kw
FROM energy_meter_compressed
WHERE meter_id = 1000000000
  AND event_time >= TIMESTAMPTZ '2026-09-22 00:00+02' AND event_time < TIMESTAMPTZ '2026-09-29 00:00+02';