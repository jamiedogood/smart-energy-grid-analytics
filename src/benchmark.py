"""Sections 3.2 and 4.3 - warm-cache benchmark of Q1-Q5 across hypertables.

Run (Section 3):
    docker compose exec app python src/benchmark.py --label section3 \
        --tables energy_readings_3h energy_readings energy_readings_week

For each query and table: 1 warm-up + N measured executions, sequentially, on one
connection. Duration = statement submission until the last result row is received
(time.perf_counter). Results must match the first table's within 0.001.
Outputs: results/<label>_runs.csv, <label>_summary.csv, <label>_session.json
"""
import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg

QUERIES = {
    # Q1: hourly average power on 21 Sept (CAT hours)
    "Q1": """SELECT time_bucket('1 hour', event_time, 'Africa/Kigali') AS hour_cat,
                    avg(power_kw) AS avg_power_kw
             FROM {t}
             WHERE event_time >= TIMESTAMPTZ '2026-09-21 00:00+02'
               AND event_time <  TIMESTAMPTZ '2026-09-22 00:00+02'
             GROUP BY 1 ORDER BY 1""",
    # Q2: ten highest 15-minute network totals, 22-28 Sept, tie-break by earliest time
    "Q2": """SELECT event_time, sum(power_kw) AS total_power_kw
             FROM {t}
             WHERE event_time >= TIMESTAMPTZ '2026-09-22 00:00+02'
               AND event_time <  TIMESTAMPTZ '2026-09-29 00:00+02'
             GROUP BY event_time
             ORDER BY total_power_kw DESC, event_time ASC
             LIMIT 10""",
    # Q3: total energy per meter, full period
    "Q3": """SELECT meter_id, sum(energy_kwh) AS total_energy_kwh
             FROM {t} GROUP BY meter_id ORDER BY meter_id""",
    # Q4: one meter, 22-28 Sept
    "Q4": """SELECT sum(energy_kwh) AS energy_kwh, avg(power_kw) AS avg_power_kw,
                    max(power_kw) AS peak_power_kw
             FROM {t}
             WHERE meter_id = 1000000000
               AND event_time >= TIMESTAMPTZ '2026-09-22 00:00+02'
               AND event_time <  TIMESTAMPTZ '2026-09-29 00:00+02'""",
    # Q5: full scan summary
    "Q5": """SELECT count(*) AS row_count, sum(energy_kwh) AS total_energy_kwh,
                    avg(power_kw) AS avg_power_kw, min(power_kw) AS min_power_kw,
                    max(power_kw) AS max_power_kw
             FROM {t}""",
}
TOLERANCE = 0.001


def now_cat() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def results_match(a: list[tuple], b: list[tuple]) -> tuple[bool, float]:
    """Same keys/row order exactly; numeric values within TOLERANCE. Returns (ok, max abs diff)."""
    if len(a) != len(b):
        return False, float("inf")
    max_diff = 0.0
    for row_a, row_b in zip(a, b):
        for x, y in zip(row_a, row_b):
            if isinstance(x, float) or isinstance(y, float):
                diff = abs(float(x) - float(y))
                max_diff = max(max_diff, diff)
                if diff > TOLERANCE:
                    return False, diff
            elif x != y:
                return False, float("inf")
    return True, max_diff


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    p.add_argument("--tables", nargs="+", required=True, help="first table = reference for result checks")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--machine", default="laptop-i7-1165G7-16GB")
    args = p.parse_args()

    Path("results").mkdir(exist_ok=True)
    runs, checks = [], []

    with psycopg.connect(autocommit=True) as conn:
        client = f"psycopg {psycopg.__version__} (Python, app container)"
        print(f"Client: {client} | timer: time.perf_counter | machine: {args.machine}")
        others = conn.execute("""SELECT count(*) FROM pg_stat_activity
                                 WHERE state = 'active' AND pid <> pg_backend_pid()
                                   AND backend_type = 'client backend'""").fetchone()[0]
        print(f"Other active client queries right now: {others}")
        jobs = conn.execute("SELECT count(*) FILTER (WHERE scheduled) FROM timescaledb_information.jobs").fetchone()[0]
        print(f"Scheduled TimescaleDB jobs: {jobs} (should be 0 during measurement)")

        for t in args.tables:                       # current planner statistics, outside timing
            conn.execute(f"ANALYZE {t}")
        print(f"ANALYZE completed on: {', '.join(args.tables)}")

        session_start_wall, session_start = now_cat(), time.perf_counter()

        for qid, sql_template in QUERIES.items():
            reference = None
            for t in args.tables:
                sql = sql_template.format(t=t)
                for run_no in range(0, args.runs + 1):       # run 0 = warm-up
                    start_wall = now_cat()
                    t0 = time.perf_counter()
                    cur = conn.execute(sql)
                    rows = cur.fetchall()                    # last result row received
                    duration_ms = (time.perf_counter() - t0) * 1000
                    end_wall = now_cat()
                    runs.append({"query": qid, "table": t,
                                 "run_type": "warm-up" if run_no == 0 else "measured",
                                 "run_no": run_no, "start_cat": start_wall, "end_cat": end_wall,
                                 "duration_ms": round(duration_ms, 3), "rows_returned": len(rows),
                                 "machine": args.machine, "client": client})
                    if run_no == 0:
                        if reference is None:
                            reference, ok, diff = rows, True, 0.0
                        else:
                            ok, diff = results_match(reference, rows)
                        checks.append({"query": qid, "table": t, "rows": len(rows),
                                       "matches_reference": ok, "max_abs_diff": diff})
                meds = [r["duration_ms"] for r in runs
                        if r["query"] == qid and r["table"] == t and r["run_type"] == "measured"]
                print(f"{qid} {t:28s} median {statistics.median(meds):10.2f} ms")

        session_s = time.perf_counter() - session_start
        session_end_wall = now_cat()

    df = pd.DataFrame(runs)
    df.to_csv(f"results/{args.label}_runs.csv", index=False)
    summary = (df[df.run_type == "measured"]
               .groupby(["query", "table"], sort=False)["duration_ms"]
               .agg(median_ms="median", min_ms="min", max_ms="max").round(2).reset_index())
    summary.to_csv(f"results/{args.label}_summary.csv", index=False)
    checks_df = pd.DataFrame(checks)
    checks_df.to_csv(f"results/{args.label}_checks.csv", index=False)
    Path(f"results/{args.label}_session.json").write_text(json.dumps({
        "label": args.label, "tables": args.tables, "machine": args.machine, "client": client,
        "timer": "time.perf_counter", "cache_state": "warm (1 warm-up before 5 measured runs)",
        "session_start_cat": session_start_wall, "session_end_cat": session_end_wall,
        "session_seconds": round(session_s, 2)}, indent=2))

    pd.set_option("display.width", 200)
    print("\nResult checks (warm-up results vs first table, tolerance 0.001):")
    print(checks_df.to_string(index=False))
    print("\nMedian ms (min-max) per query and table, warm cache:")
    pivot = summary.assign(cell=summary.apply(
        lambda r: f"{r.median_ms:.1f} ({r.min_ms:.1f}-{r.max_ms:.1f})", axis=1))
    print(pivot.pivot(index="query", columns="table", values="cell")[args.tables].to_string())
    print(f"\nSession: {session_start_wall} -> {session_end_wall} = {session_s:.1f} s")
    print(f"All results match: {bool(checks_df.matches_reference.all())}")


if __name__ == "__main__":
    main()