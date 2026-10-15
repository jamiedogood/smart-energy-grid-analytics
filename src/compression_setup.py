"""Section 4.1 - build energy_time_compressed and energy_meter_compressed (1-day chunks).

Run:  docker compose exec app python src/compression_setup.py
      (add --reset to drop and rebuild both tables)

Steps: create both hypertables with matching indexes and compression settings,
add a 48-hour compression policy to each and pause it immediately, record one
cutoff = experiment start - 48 h, then copy the cleaned data chunk by chunk and
compress each chunk whose end precedes the cutoff, timing load and compression separately.
"""
import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg

BASE = "energy_readings"                       # cleaned, uncompressed, 1-day chunks
TARGETS = {"energy_time_compressed": "",       # no segmentation
           "energy_meter_compressed": "meter_id"}


def now_cat() -> datetime:
    return datetime.now().astimezone()


def has_proc(conn, name: str) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = %s)", (name,)).fetchone()[0]


def create_table(conn, name: str, segmentby: str) -> str:
    conn.execute(f"CREATE TABLE {name} (LIKE {BASE} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")
    conn.execute(f"SELECT create_hypertable('{name}', by_range('event_time', INTERVAL '1 day'), "
                 f"create_default_indexes => FALSE)")
    conn.execute(f"CREATE INDEX {name}_event_time_idx ON {name} (event_time DESC)")
    conn.execute(f"CREATE INDEX {name}_meter_time_idx ON {name} (meter_id, event_time DESC)")
    try:   # columnstore option names (TimescaleDB 2.18+)
        conn.execute(f"ALTER TABLE {name} SET (timescaledb.enable_columnstore = true, "
                     f"timescaledb.orderby = 'event_time DESC', timescaledb.segmentby = '{segmentby}')")
        return "columnstore settings (enable_columnstore / orderby / segmentby)"
    except psycopg.Error:   # older compression option names
        conn.execute(f"ALTER TABLE {name} SET (timescaledb.compress, "
                     f"timescaledb.compress_orderby = 'event_time DESC', "
                     f"timescaledb.compress_segmentby = '{segmentby}')")
        return "compression settings (compress / compress_orderby / compress_segmentby)"


def add_paused_policy(conn, name: str) -> int:
    if has_proc(conn, "add_columnstore_policy"):
        conn.execute(f"CALL add_columnstore_policy('{name}', after => INTERVAL '48 hours')")
    else:
        conn.execute(f"SELECT add_compression_policy('{name}', compress_after => INTERVAL '48 hours')")
    job_id = conn.execute("SELECT job_id FROM timescaledb_information.jobs WHERE hypertable_name = %s",
                          (name,)).fetchone()[0]
    conn.execute("SELECT alter_job(%s, scheduled => false)", (job_id,))   # paused until 4.3 is done
    return job_id


def compress_chunk(conn, chunk: str) -> None:
    if has_proc(conn, "convert_to_columnstore"):
        conn.execute(f"CALL convert_to_columnstore('{chunk}')")
    else:
        conn.execute(f"SELECT compress_chunk('{chunk}')")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop and rebuild both compressed tables")
    args = ap.parse_args()

    experiment_start = now_cat()
    cutoff = experiment_start - timedelta(hours=48)   # ONE cutoff for both tables, whole experiment
    print(f"Experiment start (CAT): {experiment_start.isoformat(timespec='seconds')}")
    print(f"Compression cutoff     : {cutoff.isoformat(timespec='seconds')}  (start - 48 h)")

    log_rows, meta = [], {}
    with psycopg.connect(autocommit=True) as conn:
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname='timescaledb'").fetchone()[0]
        api = "convert_to_columnstore" if has_proc(conn, "convert_to_columnstore") else "compress_chunk"
        print(f"TimescaleDB {version}; chunk compression via {api}()")

        if args.reset:
            for name in TARGETS:
                conn.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
        for name in TARGETS:
            exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0]
            if exists:
                raise SystemExit(f"{name} already exists - rerun with --reset to rebuild")

        base_chunks = conn.execute("""SELECT range_start, range_end FROM timescaledb_information.chunks
                                      WHERE hypertable_name = %s ORDER BY range_start""", (BASE,)).fetchall()
        print(f"{BASE} has {len(base_chunks)} chunks to copy\n")

        for name, segmentby in TARGETS.items():
            settings_api = create_table(conn, name, segmentby)
            job_id = add_paused_policy(conn, name)
            print(f"[{name}] created; segmentby='{segmentby or '(none)'}', orderby='event_time DESC'; "
                  f"{settings_api}; policy job {job_id} added and paused")
            meta[name] = {"segmentby": segmentby or None, "orderby": "event_time DESC",
                          "policy_job_id": job_id, "settings_api": settings_api}

            for range_start, range_end in base_chunks:
                t0, load_start = time.perf_counter(), now_cat()
                cur = conn.execute(f"INSERT INTO {name} SELECT * FROM {BASE} "
                                   f"WHERE event_time >= %s AND event_time < %s", (range_start, range_end))
                rows = cur.rowcount
                load_s = time.perf_counter() - t0

                chunk = conn.execute("""SELECT format('%%I.%%I', chunk_schema, chunk_name)
                                        FROM timescaledb_information.chunks
                                        WHERE hypertable_name = %s AND range_start = %s""",
                                     (name, range_start)).fetchone()[0]
                compressed, compress_s, c_start, c_end = False, None, None, None
                if range_end < cutoff:                     # chunk fully older than the cutoff
                    c_start, t1 = now_cat(), time.perf_counter()
                    compress_chunk(conn, chunk)
                    compress_s, c_end, compressed = time.perf_counter() - t1, now_cat(), True

                log_rows.append({"table": name, "chunk": chunk, "range_start": range_start.isoformat(),
                                 "range_end": range_end.isoformat(), "rows": rows,
                                 "load_start_cat": load_start.isoformat(timespec="milliseconds"),
                                 "load_s": round(load_s, 3), "compressed": compressed,
                                 "compress_start_cat": c_start.isoformat(timespec="milliseconds") if c_start else None,
                                 "compress_end_cat": c_end.isoformat(timespec="milliseconds") if c_end else None,
                                 "compress_s": round(compress_s, 3) if compress_s is not None else None})
                print(f"  {chunk:45s} {rows:8d} rows  load {load_s:6.2f} s  "
                      f"{'compress ' + format(compress_s, '6.2f') + ' s' if compressed else 'not compressed'}")

            conn.execute(f"ANALYZE {name}")
            print()

    df = pd.DataFrame(log_rows)
    Path("results").mkdir(exist_ok=True)
    df.to_csv("results/section4_compression_log.csv", index=False)
    summary = df.groupby("table").agg(chunks=("chunk", "count"), compressed_chunks=("compressed", "sum"),
                                      rows=("rows", "sum"), total_load_s=("load_s", "sum"),
                                      total_compress_s=("compress_s", "sum")).round(2)
    Path("results/section4_experiment.json").write_text(json.dumps({
        "timescaledb_version": version, "chunk_compression_call": api,
        "experiment_start_cat": experiment_start.isoformat(timespec="seconds"),
        "cutoff_cat": cutoff.isoformat(timespec="seconds"),
        "tables": meta, "summary": summary.reset_index().to_dict(orient="records")}, indent=2, default=str))

    print("=" * 78)
    print(summary.to_string())
    print(f"Cutoff used for both tables: {cutoff.isoformat(timespec='seconds')}")
    print("=" * 78)


if __name__ == "__main__":
    main()