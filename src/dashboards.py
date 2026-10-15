"""Section 7 - Raw and Aggregate dashboards in Grafana: build (7.1/7.2), verify (7.3), time (7.4).

  docker compose exec app python src/dashboards.py build   # writes grafana/dashboards/raw.json + aggregate.json
  docker compose exec app python src/dashboards.py check   # 7.3: compare the data behind all five panels
  docker compose exec app python src/dashboards.py time    # 7.4: 1 warm-up + 10 measured refreshes per mode

All panel SQL lives in PANELS, so dashboards, checks and timings use identical queries.
Time filters use [$__timeFrom(), $__timeTo()) - inclusive start, exclusive end.
"""
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg

FROM_CAT, TO_CAT = "2026-09-22 00:00:00+02", "2026-09-29 00:00:00+02"
FROM_MS, TO_MS = 1790028000000, 1790632800000            # the same two instants in epoch milliseconds
METER = 1000000000
GRAFANA = "http://grafana:3000"
DS = {"type": "grafana-postgresql-datasource", "uid": "smartgrid-pg"}
PLANNED = "(extract(epoch FROM ($__timeTo()::timestamptz - $__timeFrom()::timestamptz)) / 900)"

COMPLETENESS = """WITH reg AS (SELECT region, count(*) AS meters FROM meters GROUP BY region),
     obs AS (OBS_SQL)
SELECT reg.region,
       (100.0 * coalesce(obs.observed, 0) / (reg.meters * PLANNED))::double precision AS completeness_pct,
       coalesce(obs.observed, 0)::bigint AS readings,
       (reg.meters * PLANNED)::bigint AS planned
FROM reg LEFT JOIN obs USING (region)
ORDER BY substring(reg.region FROM 8)::int""".replace("PLANNED", PLANNED)

PANELS = [
    {"id": "P1", "title": "Hourly energy by region (kWh)", "kind": "line", "unit": "kwatth",
     "keys": ["time", "metric"], "value": "energy_kwh",
     "raw": """SELECT time_bucket('1 hour', r.event_time, 'Africa/Kigali') AS time, m.region AS metric,
       sum(r.energy_kwh) AS energy_kwh{cnt}
FROM energy_readings r JOIN meters m USING (meter_id)
WHERE r.event_time >= $__timeFrom() AND r.event_time < $__timeTo()
GROUP BY 1, 2 ORDER BY 1, 2""", "raw_cnt": ", count(*) AS readings",
     "agg": """SELECT bucket AS time, region AS metric, energy_kwh_sum AS energy_kwh{cnt}
FROM energy_hourly_region
WHERE bucket >= $__timeFrom() AND bucket < $__timeTo()
ORDER BY 1, 2""", "agg_cnt": ", reading_count AS readings"},

    {"id": "P2", "title": "Daily network energy (kWh)", "kind": "bars", "unit": "kwatth", "display": "Network energy",
     "keys": ["time"], "value": "energy_kwh",
     "raw": """SELECT time_bucket('1 day', event_time, 'Africa/Kigali') AS time, sum(energy_kwh) AS energy_kwh{cnt}
FROM energy_readings
WHERE event_time >= $__timeFrom() AND event_time < $__timeTo()
GROUP BY 1 ORDER BY 1""", "raw_cnt": ", count(*) AS readings",
     "agg": """SELECT time_bucket('1 day', bucket, 'Africa/Kigali') AS time, sum(energy_kwh_sum) AS energy_kwh{cnt}
FROM energy_15m_region
WHERE bucket >= $__timeFrom() AND bucket < $__timeTo()
GROUP BY 1 ORDER BY 1""", "agg_cnt": ", sum(reading_count)::bigint AS readings"},

    {"id": "P3", "title": "Network energy per seven-day period from 1 Sep (kWh)", "kind": "bars", "unit": "kwatth",
     "display": "Network energy", "keys": ["time"], "value": "energy_kwh",
     "raw": """SELECT time_bucket('7 days', event_time, TIMESTAMPTZ '2026-09-01 00:00+02') AS time,
       sum(energy_kwh) AS energy_kwh{cnt}
FROM energy_readings
WHERE event_time >= $__timeFrom() AND event_time < $__timeTo()
GROUP BY 1 ORDER BY 1""", "raw_cnt": ", count(*) AS readings",
     "agg": """SELECT time_bucket('7 days', bucket, TIMESTAMPTZ '2026-09-01 00:00+02') AS time,
       sum(energy_kwh_sum) AS energy_kwh{cnt}
FROM energy_15m_region
WHERE bucket >= $__timeFrom() AND bucket < $__timeTo()
GROUP BY 1 ORDER BY 1""", "agg_cnt": ", sum(reading_count)::bigint AS readings"},

    {"id": "P4", "title": "Daily energy for meter $meter (kWh)", "kind": "bars", "unit": "kwatth",
     "display": "Meter energy", "keys": ["time"], "value": "energy_kwh",
     "raw": """SELECT time_bucket('1 day', event_time, 'Africa/Kigali') AS time, sum(energy_kwh) AS energy_kwh{cnt}
FROM energy_readings
WHERE meter_id = $meter AND event_time >= $__timeFrom() AND event_time < $__timeTo()
GROUP BY 1 ORDER BY 1""", "raw_cnt": ", count(*) AS readings",
     "agg": """SELECT bucket AS time, energy_kwh_sum AS energy_kwh{cnt}
FROM energy_daily_meter
WHERE meter_id = $meter AND bucket >= $__timeFrom() AND bucket < $__timeTo()
ORDER BY 1""", "agg_cnt": ", reading_count AS readings"},

    {"id": "P5", "title": "Completeness by region (%)", "kind": "table", "unit": "percent", "decimals": 4,
     "keys": ["region"], "value": "completeness_pct",
     "raw": COMPLETENESS.replace("OBS_SQL", """SELECT m.region, count(*) AS observed
             FROM energy_readings r JOIN meters m USING (meter_id)
             WHERE r.event_time >= $__timeFrom() AND r.event_time < $__timeTo()
             GROUP BY m.region"""), "raw_cnt": "",
     "agg": COMPLETENESS.replace("OBS_SQL", """SELECT region, sum(reading_count) AS observed
             FROM energy_15m_region
             WHERE bucket >= $__timeFrom() AND bucket < $__timeTo()
             GROUP BY region"""), "agg_cnt": ""},
]
LAYOUT = [(0, 0, 24, 9), (0, 9, 12, 8), (12, 9, 12, 8), (0, 17, 12, 8), (12, 17, 12, 8)]
MODES = {"raw": ("smartgrid-raw", "Raw"), "agg": ("smartgrid-aggregate", "Aggregate")}


def panel_sql(p: dict, mode: str, with_counts: bool) -> str:
    return p[mode].replace("{cnt}", p[f"{mode}_cnt"] if with_counts else "")


def now_cat() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# ----------------------------------------------------------------------------- build
def panel_json(i: int, p: dict, mode: str, grid: tuple) -> dict:
    table = p["kind"] == "table"
    defaults = {"unit": p["unit"], "decimals": p.get("decimals", 1)}
    if not table:
        defaults["custom"] = {"drawStyle": "bars" if p["kind"] == "bars" else "line",
                              "fillOpacity": 70 if p["kind"] == "bars" else 10,
                              "lineWidth": 1, "showPoints": "never", "spanNulls": False}
    if p.get("display"):
        defaults["displayName"] = p["display"]
    overrides = [{"matcher": {"id": "byName", "options": n},
                  "properties": [{"id": "unit", "value": "none"}, {"id": "decimals", "value": 0}]}
                 for n in ("readings", "planned")] if table else []
    return {"id": i, "type": "table" if table else "timeseries", "title": f"{p['id']} - {p['title']}",
            "datasource": DS, "gridPos": dict(zip("xywh", grid)),
            "targets": [{"refId": "A", "datasource": DS, "editorMode": "code", "rawQuery": True,
                         "format": "table" if table else "time_series",
                         "rawSql": f"-- panel: {p['id']} ({mode})\n" + panel_sql(p, mode, False)}],
            "fieldConfig": {"defaults": defaults, "overrides": overrides},
            "options": {"showHeader": True} if table else
                       {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi"}}}


def build() -> None:
    out = Path("grafana/dashboards"); out.mkdir(parents=True, exist_ok=True)
    for mode, (uid, title) in MODES.items():
        dash = {"uid": uid, "title": title, "timezone": "Africa/Kigali", "editable": False, "schemaVersion": 39,
                "time": {"from": "2026-09-21T22:00:00.000Z", "to": "2026-09-28T22:00:00.000Z"},
                "refresh": "", "tags": ["section7"],
                "templating": {"list": [{"name": "meter", "label": "Meter", "type": "query", "datasource": DS,
                                         "query": "SELECT meter_id FROM meters ORDER BY meter_id",
                                         "definition": "SELECT meter_id FROM meters ORDER BY meter_id",
                                         "refresh": 1, "current": {"text": str(METER), "value": str(METER)}}]},
                "panels": [panel_json(i, p, mode, g) for i, (p, g) in enumerate(zip(PANELS, LAYOUT), start=1)]}
        path = out / f"{'raw' if mode == 'raw' else 'aggregate'}.json"
        path.write_text(json.dumps(dash, indent=2))
        print(f"wrote {path}  ({title}: {len(dash['panels'])} panels)")


# ----------------------------------------------------------------------------- check (7.3)
def expand(sql: str) -> str:
    return (sql.replace("$__timeFrom()", f"'{FROM_CAT}'::timestamptz")
               .replace("$__timeTo()", f"'{TO_CAT}'::timestamptz").replace("$meter", str(METER)))


def check() -> None:
    print(f"Range [{FROM_CAT}, {TO_CAT}), all regions, meter {METER}")
    rows = []
    with psycopg.connect() as conn:
        for p in PANELS:
            frames = {}
            for mode in ("raw", "agg"):
                cur = conn.execute(expand(panel_sql(p, mode, True)))
                df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
                df[p["value"]] = df[p["value"]].astype(float)
                df["readings"] = df["readings"].astype(int)
                frames[mode] = df.sort_values(p["keys"]).reset_index(drop=True)
            r, a = frames["raw"], frames["agg"]
            same_keys = len(r) == len(a) and r[p["keys"]].equals(a[p["keys"]])
            same_counts = same_keys and r["readings"].equals(a["readings"])
            diff = float((r[p["value"]] - a[p["value"]]).abs().max()) if same_keys else float("inf")
            tol = 0.000001 if p["value"] == "completeness_pct" else 0.001
            rows.append({"panel": f"{p['id']} {p['title']}", "rows_compared": len(r), "keys_identical": same_keys,
                         "reading_counts_identical": same_counts, "max_abs_difference": diff,
                         "tolerance": tol, "pass": same_keys and same_counts and diff <= tol})
            frames["raw"].merge(frames["agg"], on=p["keys"], suffixes=("_raw", "_agg")).to_csv(
                f"results/section7_check_{p['id']}.csv", index=False)
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(res.to_string(index=False))
    res.to_csv("results/section7_checks.csv", index=False)
    print(f"\nAll panels match: {bool(res['pass'].all())}  (paired data saved as results/section7_check_P*.csv)")


# ----------------------------------------------------------------------------- time (7.4)
def time_dashboards(runs: int) -> None:
    from playwright.sync_api import sync_playwright
    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path="/usr/bin/chromium")   # Debian's Chromium package
        page = browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        print(f"Browser: Chromium {browser.version} (Playwright, headless) in the app container; "
              f"timer: browser Resource Timing per panel + Python perf_counter for the whole dashboard")
        for mode, (uid, title) in MODES.items():
            url = (f"{GRAFANA}/d/{uid}/{title.lower()}?orgId=1&from={FROM_MS}&to={TO_MS}"
                   f"&var-meter={METER}&timezone=Africa%2FKigali")
            page.goto(url, wait_until="load")
            page.wait_for_timeout(3000)                       # let the first load settle
            for run in range(runs + 1):                       # run 0 = warm-up
                finished: dict = {}

                def on_finished(req, store=finished):
                    if "/api/ds/query" in req.url:
                        m = re.search(r"panel: (P\d)", req.post_data or "")
                        if m:
                            store[m.group(1)] = req.timing["responseEnd"]   # ms from request start to last byte

                page.on("requestfinished", on_finished)
                start = now_cat()
                t0 = time.perf_counter()
                page.keyboard.press("d"); page.keyboard.press("r")     # Grafana shortcut: refresh all panels
                method = "d r shortcut"
                while not finished and time.perf_counter() - t0 < 3:
                    page.wait_for_timeout(5)
                if not finished:                                        # fallback if the shortcut did not fire
                    method, t0 = "page reload", time.perf_counter()
                    page.reload(wait_until="commit")
                while len(finished) < len(PANELS) and time.perf_counter() - t0 < 120:
                    page.wait_for_timeout(5)
                page.wait_for_function("() => !document.querySelector('[aria-label=\"Panel loading bar\"],"
                                       " [data-testid*=\"loading-bar\"]')", timeout=60000)
                page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                total_ms = (time.perf_counter() - t0) * 1000
                page.remove_listener("requestfinished", on_finished)
                rec = {"mode": title, "run": run, "run_type": "warm-up" if run == 0 else "measured",
                       "method": method, "start_cat": start, "end_cat": now_cat(),
                       **{p["id"]: round(finished.get(p["id"], float("nan")), 1) for p in PANELS},
                       "dashboard_ms": round(total_ms, 1)}
                records.append(rec)
                print(f"{title:9s} run {run:2d} ({rec['run_type']:8s}) " +
                      " ".join(f"{p['id']}={rec[p['id']]:8.1f}" for p in PANELS) + f"  total={total_ms:8.1f} ms")
                page.wait_for_timeout(500)
        browser.close()

    df = pd.DataFrame(records)
    df.to_csv("results/section7_timing_runs.csv", index=False)
    long = df[df.run_type == "measured"].melt(id_vars=["mode", "run"], value_vars=[p["id"] for p in PANELS] +
                                              ["dashboard_ms"], var_name="measure", value_name="ms")
    summary = long.groupby(["measure", "mode"])["ms"].agg(median_ms="median", min_ms="min", max_ms="max").round(1)
    summary.to_csv("results/section7_timing_summary.csv")
    pd.set_option("display.width", 200)
    print("\n" + summary.unstack("mode").to_string())
    med = summary["median_ms"].unstack("mode")
    print("\nSpeed-up (Raw median / Aggregate median):")
    print((med["Raw"] / med["Aggregate"]).round(1).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["build", "check", "time"])
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()
    {"build": build, "check": check, "time": lambda: time_dashboards(args.runs)}[args.command]()