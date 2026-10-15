"""Reproduce the BEFORE-cleaning state for Sections 2.2 and 2.3 evidence.

energy_readings was cleaned in 2.3, so its diagnostics now show 0 duplicates. The generator is
deterministic, so this script reloads the identical stream into a separate hypertable,
energy_readings_raw (1-day chunks), using the same COPY route as load_full_stream.py.
energy_readings and every other table are not touched.

Run:   docker compose exec app python src/load_raw_copy.py
Drop:  docker compose exec app psql -c "DROP TABLE energy_readings_raw;"
"""
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import smart_meter_simulator as sim  # noqa: E402

TABLE = "energy_readings_raw"
FIELDS = ("meter_id", "event_time", "received_time", "power_kw",
          "voltage_v", "current_a", "frequency_hz", "energy_kwh")


def main() -> None:
    expected = sim.dataset_info()["expected_counts"]["emitted_readings"]
    with psycopg.connect(autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        conn.execute(f"CREATE TABLE {TABLE} (LIKE energy_readings INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")
        conn.execute(f"SELECT create_hypertable('{TABLE}', by_range('event_time', INTERVAL '1 day'))")
        t0, n = time.perf_counter(), 0
        with conn.cursor() as cur, cur.copy(f"COPY {TABLE} ({', '.join(FIELDS)}) FROM STDIN") as copy:
            for reading in sim.iter_readings():
                copy.write_row([reading[f] for f in FIELDS])
                n += 1
                if n % 2_000_000 == 0:
                    print(f"progress: {n:,} rows")
        rows = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
    print(f"Loaded {TABLE}: consumed {n:,}, stored {rows:,}, expected emitted {expected:,}, "
          f"match = {n == rows == expected}, {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()