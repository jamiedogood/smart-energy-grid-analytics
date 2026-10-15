"""Section 5.4 - anomaly detection on cleaned readings, then evaluation against anomaly_events().

Run:  docker compose exec app python src/anomaly_detection.py

Rule (decided before looking at labels): flag a reading when power_kw is >= 1.8x or <= 0.4x the
median power of the SAME meter, SAME 15-minute time-of-day slot, SAME day type (weekday/weekend)
over the 28 days. Labels are loaded only after flagging, for evaluation.
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import smart_meter_simulator as sim  # noqa: E402

HIGH, LOW = 1.8, 0.4
pd.set_option("display.width", 200)


def query(conn, sql: str, params=None) -> pd.DataFrame:
    cur = conn.execute(sql, params)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def main() -> None:
    t0 = time.perf_counter()
    with psycopg.connect() as conn:
        conn.execute("SET work_mem = '256MB'")
        # ---------------- 1. DETECTION (no labels involved) ----------------
        print(f"Rule: flag if power_kw >= {HIGH} x or <= {LOW} x the median for the same meter, "
              f"time-of-day slot and day type (weekday/weekend)")
        conn.execute("""
            CREATE TEMP TABLE baseline AS
            SELECT meter_id,
                   extract(isodow FROM event_time) >= 6                          AS weekend,
                   extract(hour FROM event_time) * 4 + extract(minute FROM event_time) / 15 AS slot,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY power_kw)         AS median_kw,
                   count(*)                                                      AS n_days
            FROM energy_readings GROUP BY 1, 2, 3""")
        conn.execute("ANALYZE baseline")
        n_base = conn.execute("SELECT count(*), min(n_days), max(n_days) FROM baseline").fetchone()
        print(f"Baseline groups (meter x day type x slot): {n_base[0]:,}  (days per group {n_base[1]}-{n_base[2]})")

        flags = query(conn, f"""
            SELECT r.meter_id, r.event_time, r.power_kw, b.median_kw,
                   r.power_kw / NULLIF(b.median_kw, 0) AS ratio,
                   CASE WHEN r.power_kw >= {HIGH} * b.median_kw THEN 'high' ELSE 'low' END AS flag_type
            FROM energy_readings r
            JOIN baseline b
              ON b.meter_id = r.meter_id
             AND b.weekend  = (extract(isodow FROM r.event_time) >= 6)
             AND b.slot     = extract(hour FROM r.event_time) * 4 + extract(minute FROM r.event_time) / 15
            WHERE r.power_kw >= {HIGH} * b.median_kw OR r.power_kw <= {LOW} * b.median_kw
            ORDER BY r.meter_id, r.event_time""")
        total_rows = conn.execute("SELECT count(*) FROM energy_readings").fetchone()[0]
        print(f"Readings examined: {total_rows:,}   Flagged readings: {len(flags):,}   "
              f"Meters with a flag: {flags.meter_id.nunique()}")
        print("\nSample flagged readings:")
        print(flags.head(15).to_string(index=False))

        # ---------------- 2. EVALUATION (labels used only now) ----------------
        events = pd.DataFrame(sim.anomaly_events())
        events["start_time"] = events["start_time"].map(datetime.fromisoformat)
        events["end_time"] = events["end_time"].map(datetime.fromisoformat)

        def in_event(row) -> bool:
            ev = events[events.meter_id == row.meter_id]
            return bool(((ev.start_time <= row.event_time) & (row.event_time < ev.end_time)).any())

        flags["inside_injected_event"] = flags.apply(in_event, axis=1)

        results = []
        for ev in events.itertuples():
            hits = flags[(flags.meter_id == ev.meter_id) & (flags.event_time >= ev.start_time)
                         & (flags.event_time < ev.end_time)]
            obs = conn.execute("""SELECT count(*) FROM energy_readings
                                  WHERE meter_id = %s AND event_time >= %s AND event_time < %s""",
                               (ev.meter_id, ev.start_time, ev.end_time)).fetchone()[0]
            results.append({"meter_id": ev.meter_id, "event_type": ev.event_type,
                            "start_time": ev.start_time, "end_time": ev.end_time,
                            "observed_readings_in_event": obs, "flags_in_event": len(hits),
                            "detected": len(hits) > 0})
        ev_df = pd.DataFrame(results)

        readings_in_events = int(ev_df.observed_readings_in_event.sum())
        outside = total_rows - readings_in_events
        false_flags = flags[~flags.inside_injected_event]
        meters_false = false_flags.meter_id.nunique()

    detected = int(ev_df.detected.sum())
    print("\n" + "=" * 90)
    print(f"Injected events detected : {detected} / {len(ev_df)}")
    print(f"Injected events missed   : {len(ev_df) - detected} / {len(ev_df)}")
    print(ev_df.groupby("event_type")["detected"].agg(["sum", "count"]).rename(
        columns={"sum": "detected", "count": "events"}).to_string())
    print(f"\nFlags inside injected intervals : {int(flags.inside_injected_event.sum()):,}")
    print(f"False flags (outside every injected interval for that meter): {len(false_flags):,}")
    print(f"Observed readings outside injected intervals: {outside:,}")
    print(f"False flags as % of those readings: {100 * len(false_flags) / outside:.6f} %")
    print(f"Meters with >= 1 false flag: {meters_false}  ->  percentage of meters affected by false flags: "
          f"{100 * meters_false / 4000:.3f} %")
    print("=" * 90)
    print("\nPer-event comparison:")
    print(ev_df.to_string(index=False))
    if len(false_flags):
        print("\nFalse flags (first 20):")
        print(false_flags.head(20).to_string(index=False))

    Path("results").mkdir(exist_ok=True)
    flags.to_csv("results/section5_anomaly_flags.csv", index=False)
    ev_df.to_csv("results/section5_anomaly_events_evaluation.csv", index=False)
    print(f"\nElapsed {time.perf_counter() - t0:.1f} s; saved results/section5_anomaly_*.csv")


if __name__ == "__main__":
    main()