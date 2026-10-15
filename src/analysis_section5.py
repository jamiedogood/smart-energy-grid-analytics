"""Sections 5.1-5.3 - demand patterns, regions/customer types, reporting quality.

Run:  docker compose exec app python src/analysis_section5.py
Prints every result table; saves charts to results/charts/ and tables to results/section5_*.csv
"""
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                     # no screen inside the container: draw straight to PNG
import matplotlib.pyplot as plt
import pandas as pd
import psycopg

OUT = Path("results")
CHARTS = OUT / "charts"
CHARTS.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", lambda v: f"{v:,.3f}")


def query(conn, sql: str) -> pd.DataFrame:
    cur = conn.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def region_order(df: pd.DataFrame) -> pd.DataFrame:
    """Sort 'Region 1'...'Region 10' numerically instead of alphabetically."""
    return df.assign(_n=df["region"].str.extract(r"(\d+)").astype(int)).sort_values("_n").drop(columns="_n")


def section(title: str) -> None:
    print("\n" + "=" * 90 + f"\n{title}\n" + "=" * 90)


def main() -> None:
    with psycopg.connect() as conn:
        # ------------------------------------------------------------------ 5.1
        section("5.1 Daily network energy (kWh), CAT days")
        daily = query(conn, """
            SELECT time_bucket('1 day', event_time, 'Africa/Kigali') AS day,
                   sum(energy_kwh) AS energy_kwh, count(*) AS readings
            FROM energy_readings GROUP BY 1 ORDER BY 1""")
        daily["date"] = daily["day"].dt.date
        daily["weekday"] = daily["day"].dt.strftime("%a")
        print(daily[["date", "weekday", "energy_kwh", "readings"]].to_string(index=False))

        section("5.1 Seven-day periods (kWh)")
        daily["period_start"] = daily["date"].map(
            lambda d: date(2026, 9, 1 + 7 * ((d - date(2026, 9, 1)).days // 7)))
        weekly = daily.groupby("period_start", as_index=False).agg(
            energy_kwh=("energy_kwh", "sum"), days=("date", "count"), readings=("readings", "sum"))
        weekly["period"] = weekly["period_start"].map(lambda d: f"{d:%d} - {d.day + 6:02d} Sep")
        weekly["change_vs_previous_pct"] = (100 * weekly["energy_kwh"].pct_change()).round(2)
        print(weekly[["period", "days", "energy_kwh", "readings", "change_vs_previous_pct"]].to_string(index=False))

        section("5.1 Three highest 15-minute network demand intervals (sum of power_kw across meters)")
        peaks = query(conn, """
            SELECT event_time, sum(power_kw) AS network_kw, count(*) AS meters_reporting
            FROM energy_readings GROUP BY event_time
            ORDER BY network_kw DESC, event_time ASC LIMIT 3""")
        peaks["weekday"] = peaks["event_time"].dt.strftime("%a")
        print(peaks.to_string(index=False))

        profile = query(conn, """
            WITH n AS (SELECT event_time, sum(power_kw) AS network_kw
                       FROM energy_readings GROUP BY event_time)
            SELECT to_char(event_time, 'HH24:MI') AS time_of_day,
                   CASE WHEN extract(isodow FROM event_time) >= 6 THEN 'weekend' ELSE 'weekday' END AS day_type,
                   avg(network_kw) AS avg_network_kw
            FROM n GROUP BY 1, 2 ORDER BY 1, 2""")
        prof = profile.pivot(index="time_of_day", columns="day_type", values="avg_network_kw")
        section("5.1 Average network demand profile (kW) - selected times")
        print(prof.loc[["03:00", "07:30", "13:00", "19:00", "23:00"]].to_string())

        # charts for 5.1
        fig, ax = plt.subplots(figsize=(12, 4.5))
        colours = ["#d62728" if w in ("Sat", "Sun") else "#1f77b4" for w in daily["weekday"]]
        ax.bar(daily["date"].astype(str), daily["energy_kwh"], color=colours)
        ax.set_title("Total network energy per CAT day, 1-28 Sept 2026 (red = weekend)")
        ax.set_ylabel("Energy (kWh)")
        ax.tick_params(axis="x", rotation=90)
        fig.tight_layout(); fig.savefig(CHARTS / "5_1_daily_energy.png", dpi=130); plt.close(fig)

        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.bar(weekly["period"], weekly["energy_kwh"], color="#2ca02c")
        for x, y in zip(weekly["period"], weekly["energy_kwh"]):
            ax.text(x, y, f"{y:,.0f}", ha="center", va="bottom", fontsize=9)
        ax.set_title("Network energy per seven-day period")
        ax.set_ylabel("Energy (kWh)")
        fig.tight_layout(); fig.savefig(CHARTS / "5_1_weekly_energy.png", dpi=130); plt.close(fig)

        fig, ax = plt.subplots(figsize=(12, 4.5))
        prof.plot(ax=ax)
        ticks = list(range(0, 96, 8))
        ax.set_xticks(ticks); ax.set_xticklabels([prof.index[i] for i in ticks])
        ax.set_title("Average network demand by time of day (sum of power_kw across meters)")
        ax.set_ylabel("Network demand (kW)"); ax.set_xlabel("CAT time of day")
        fig.tight_layout(); fig.savefig(CHARTS / "5_1_daily_profile.png", dpi=130); plt.close(fig)

        # ------------------------------------------------------------------ 5.2
        section("5.2 Energy by region (all 28 days)")
        by_region = region_order(query(conn, """
            WITH e AS (SELECT meter_id, sum(energy_kwh) AS kwh FROM energy_readings GROUP BY meter_id)
            SELECT m.region, count(*) AS registered_meters,
                   sum(e.kwh) AS total_energy_kwh, sum(e.kwh) / count(*) AS energy_per_meter_kwh,
                   count(*) FILTER (WHERE m.customer_type = 'residential') AS residential,
                   count(*) FILTER (WHERE m.customer_type = 'commercial')  AS commercial,
                   count(*) FILTER (WHERE m.customer_type = 'industrial')  AS industrial
            FROM meters m LEFT JOIN e USING (meter_id)
            GROUP BY m.region"""))
        print(by_region.to_string(index=False))

        section("5.2 Energy by customer type (all 28 days)")
        by_type = query(conn, """
            WITH e AS (SELECT meter_id, sum(energy_kwh) AS kwh FROM energy_readings GROUP BY meter_id)
            SELECT m.customer_type, count(*) AS registered_meters,
                   sum(e.kwh) AS total_energy_kwh, sum(e.kwh) / count(*) AS energy_per_meter_kwh
            FROM meters m LEFT JOIN e USING (meter_id)
            GROUP BY m.customer_type ORDER BY energy_per_meter_kwh DESC""")
        by_type["share_of_meters_pct"] = 100 * by_type["registered_meters"] / by_type["registered_meters"].sum()
        by_type["share_of_energy_pct"] = 100 * by_type["total_energy_kwh"] / by_type["total_energy_kwh"].sum()
        print(by_type.to_string(index=False))

        fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
        axes[0].bar(by_region["region"], by_region["energy_per_meter_kwh"], color="#9467bd")
        axes[0].set_title("Energy per registered meter by region"); axes[0].set_ylabel("kWh per meter")
        axes[0].tick_params(axis="x", rotation=45)
        axes[1].bar(by_type["customer_type"], by_type["energy_per_meter_kwh"], color="#8c564b")
        axes[1].set_title("Energy per registered meter by customer type"); axes[1].set_ylabel("kWh per meter")
        fig.tight_layout(); fig.savefig(CHARTS / "5_2_energy_per_meter.png", dpi=130); plt.close(fig)

        # ------------------------------------------------------------------ 5.3
        section("5.3 Completeness, network and regions (planned = registered meters x 28 x 96)")
        comp = region_order(query(conn, """
            WITH obs AS (SELECT m.region, count(*) AS observed
                         FROM energy_readings r JOIN meters m USING (meter_id) GROUP BY m.region),
                 reg AS (SELECT region, count(*) AS meters FROM meters GROUP BY region)
            SELECT reg.region, reg.meters, reg.meters * 28 * 96 AS planned, obs.observed,
                   100.0 * obs.observed / (reg.meters * 28 * 96) AS completeness_pct
            FROM reg JOIN obs USING (region)"""))
        network = pd.DataFrame([{"region": "NETWORK", "meters": comp["meters"].sum(),
                                 "planned": comp["planned"].sum(), "observed": comp["observed"].sum()}])
        network["completeness_pct"] = 100 * network["observed"] / network["planned"]
        comp_all = pd.concat([comp, network], ignore_index=True)
        print(comp_all.to_string(index=False, float_format=lambda v: f"{v:,.4f}"))

        fig, ax = plt.subplots(figsize=(9, 4.5))
        ax.bar(comp["region"], comp["completeness_pct"], color="#17becf")
        ax.axhline(network["completeness_pct"].iloc[0], color="black", linestyle="--", label="Network")
        ax.set_ylim(98.5, 99.5); ax.set_ylabel("Completeness (%)")
        ax.set_title("Reading completeness by region, 1-28 Sept 2026"); ax.legend()
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout(); fig.savefig(CHARTS / "5_3_completeness.png", dpi=130); plt.close(fig)

        section("5.3 Energy for event_time on 21 Sept: on-time cut-off vs all arrivals")
        late = query(conn, """
            SELECT count(*) FILTER (WHERE received_time <= TIMESTAMPTZ '2026-09-22 00:00+02') AS readings_by_cutoff,
                   sum(energy_kwh) FILTER (WHERE received_time <= TIMESTAMPTZ '2026-09-22 00:00+02') AS kwh_by_cutoff,
                   count(*) AS readings_all, sum(energy_kwh) AS kwh_all,
                   4000 * 96 AS planned_readings
            FROM energy_readings
            WHERE event_time >= TIMESTAMPTZ '2026-09-21 00:00+02' AND event_time < TIMESTAMPTZ '2026-09-22 00:00+02'""")
        late["difference_kwh"] = late["kwh_all"] - late["kwh_by_cutoff"]
        late["difference_pct_of_final"] = 100 * late["difference_kwh"] / late["kwh_all"]
        late["late_readings"] = late["readings_all"] - late["readings_by_cutoff"]
        late["missing_readings"] = late["planned_readings"] - late["readings_all"]
        print(late.T.to_string(header=False))

    for name, df in {"daily": daily, "weekly": weekly, "peaks": peaks, "profile": profile,
                     "by_region": by_region, "by_type": by_type, "completeness": comp_all,
                     "late_21sep": late}.items():
        df.to_csv(OUT / f"section5_{name}.csv", index=False)
    print(f"\nCharts saved to {CHARTS}/ and tables to results/section5_*.csv")


if __name__ == "__main__":
    main()