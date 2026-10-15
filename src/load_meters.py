"""Section 1.2 step 4 - load meter metadata from the unchanged generator into `meters`.

Run:  docker compose exec app python src/load_meters.py

Connection details come from the PG* environment variables set in docker-compose.yml
(PGHOST=timescaledb, PGUSER, PGPASSWORD, PGDATABASE=smartgrid), so no secrets live in code.
"""
import sys
from datetime import datetime
from pathlib import Path

import psycopg

# smart_meter_simulator.py sits in the project root (/app), one level above src/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import smart_meter_simulator as sim  # noqa: E402

INSERT_SQL = """
    INSERT INTO meters (meter_id, region, customer_type, base_power_kw, power_factor)
    VALUES (%(meter_id)s, %(region)s, %(customer_type)s, %(base_power_kw)s, %(power_factor)s)
"""


def main() -> None:
    print(f"Started  {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}")

    meters = sim.meter_metadata()
    print(f"meter_metadata() returned {len(meters)} dictionaries")
    print(f"Keys of first dictionary: {list(meters[0].keys())}")
    print(f"First dictionary: {meters[0]}")

    # One transaction: either all 4,000 rows are stored or none are.
    with psycopg.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE meters")          # makes the script safe to rerun
            cur.executemany(INSERT_SQL, meters)     # one row per returned dictionary
            cur.execute("SELECT count(*) FROM meters")
            stored = cur.fetchone()[0]
        conn.commit()

    print(f"Rows in meters after commit: {stored}")
    print("OK - 4,000 rows" if stored == 4000 else "WARNING - expected 4,000 rows")
    print(f"Finished {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}")


if __name__ == "__main__":
    main()
