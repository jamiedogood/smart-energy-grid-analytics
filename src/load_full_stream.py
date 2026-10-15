"""Section 2.1 - stream the complete generator output into energy_readings with COPY.

Run:  docker compose exec app python src/load_full_stream.py

Route: direct database writer. Each batch of rows is streamed with PostgreSQL COPY
and committed, so loading is progressive. Timings are logged and saved to
results/full_load_timings.json for the report.
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import smart_meter_simulator as sim  # noqa: E402

FIELDS = ("meter_id", "event_time", "received_time", "power_kw",
          "voltage_v", "current_a", "frequency_hz", "energy_kwh")
COPY_SQL = f"COPY energy_readings ({', '.join(FIELDS)}) FROM STDIN"


def get_logger() -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    log = logging.getLogger("full_load")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler("logs/full_load.log", mode="w")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log


def now_cat() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=100_000, help="rows per COPY + commit")
    parser.add_argument("--truncate", action="store_true", help="empty energy_readings first")
    args = parser.parse_args()
    log = get_logger()

    info = sim.dataset_info()
    expected = info["expected_counts"]["emitted_readings"]
    log.info("dataset_info(): %s", json.dumps(info))
    log.info("Expected emitted_readings: %d", expected)

    with psycopg.connect() as conn:
        if args.truncate:
            conn.execute("TRUNCATE energy_readings")
            conn.commit()
        existing = conn.execute("SELECT count(*) FROM energy_readings").fetchone()[0]
        if existing:
            raise SystemExit(f"energy_readings already has {existing} rows - rerun with --truncate")
        log.info("energy_readings is empty - starting fresh iter_readings()")

        consumed = committed = 0
        first_write = None
        gen_start_wall, gen_start = now_cat(), time.perf_counter()
        stream = sim.iter_readings()          # fresh iterator = full dataset from the start
        exhausted = False

        while not exhausted:
            rows_in_batch = 0
            with conn.cursor() as cur:
                with cur.copy(COPY_SQL) as copy:
                    for reading in stream:
                        if first_write is None:
                            first_write, first_write_wall = time.perf_counter(), now_cat()
                        copy.write_row([reading[f] for f in FIELDS])
                        consumed += 1
                        rows_in_batch += 1
                        if rows_in_batch >= args.batch:
                            break
                    else:
                        exhausted = True      # the for-loop ran out of readings
            conn.commit()
            committed += rows_in_batch

            if committed % 1_000_000 < args.batch or exhausted:
                elapsed = time.perf_counter() - gen_start
                rate = consumed / elapsed if elapsed else 0
                eta = (expected - consumed) / rate if rate else 0
                log.info("progress: consumed=%d committed=%d (%.1f%%) %.0f rows/s ETA %.0f s",
                         consumed, committed, 100 * committed / expected, rate, eta)

        final_commit, final_commit_wall = time.perf_counter(), now_cat()
        db_rows = conn.execute("SELECT count(*) FROM energy_readings").fetchone()[0]

    total_s = final_commit - gen_start
    load_s = final_commit - first_write
    log.info("=" * 64)
    log.info("Ingestion route                          : direct database writer (COPY, %d-row batches)", args.batch)
    log.info("Dictionaries consumed from generator     : %d", consumed)
    log.info("Committed rows in energy_readings (query): %d", db_rows)
    log.info("Expected emitted_readings                : %d", expected)
    log.info("All three agree                          : %s", consumed == db_rows == expected)
    log.info("Generation started (CAT)                 : %s", gen_start_wall)
    log.info("First database write (CAT)               : %s", first_write_wall)
    log.info("Final commit (CAT)                       : %s", final_commit_wall)
    log.info("Total elapsed seconds (start -> commit)  : %.1f", total_s)
    log.info("Load seconds (first write -> commit)     : %.1f", load_s)
    log.info("=" * 64)

    Path("results").mkdir(exist_ok=True)
    Path("results/full_load_timings.json").write_text(json.dumps({
        "route": "direct database writer, COPY",
        "batch_rows": args.batch,
        "consumed": consumed, "db_rows": db_rows, "expected_emitted": expected,
        "generation_start_cat": gen_start_wall, "first_write_cat": first_write_wall,
        "final_commit_cat": final_commit_wall,
        "total_elapsed_s": round(total_s, 1), "load_s": round(load_s, 1),
    }, indent=2))


if __name__ == "__main__":
    main()