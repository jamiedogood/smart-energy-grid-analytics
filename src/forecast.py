"""Section 8 - next-day hourly regional consumption forecast (offline, observed consumption).

Run:  docker compose exec app python src/forecast.py

8.1 dataset + chronological split + leakage-safe features (origin = 00:00 CAT of the target day)
8.2 previous-day baseline on the test week
8.3 Ridge regression: several alpha configurations, select by validation MAE, freeze, predict test
8.4 overall + per-region MAE/RMSE, peak-hour errors, forecast plot
Error = predicted - actual (kWh).
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

TZ = "Africa/Kigali"
PLOT_REGION = "Region 8"
OUT = Path("results"); CHARTS = OUT / "charts"; CHARTS.mkdir(parents=True, exist_ok=True)
SPLITS = {"train":      ("2026-09-01", "2026-09-15"),
          "validation": ("2026-09-15", "2026-09-22"),
          "test":       ("2026-09-22", "2026-09-29")}
NUMERIC = ["lag_24h", "lag_48h", "lag_168h", "mean_same_hour_7d", "prev_day_total",
           "lag_24h_same_daytype", "is_weekend"]
CATEGORICAL = ["region", "hour_of_day", "day_of_week"]
CONFIGS = {"R1": 0.1, "R2": 1.0, "R3": 10.0, "R4": 100.0}       # Ridge alpha
pd.set_option("display.width", 220)


def section(t: str) -> None:
    print("\n" + "=" * 100 + f"\n{t}\n" + "=" * 100)


def mae_rmse(pred, actual) -> tuple[float, float]:
    err = np.asarray(pred) - np.asarray(actual)                  # predicted - actual
    return float(np.mean(np.abs(err))), float(np.sqrt(np.mean(err ** 2)))


def make_model(alpha: float) -> Pipeline:
    prep = ColumnTransformer([("num", StandardScaler(), NUMERIC),
                              ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL)])
    return Pipeline([("prep", prep), ("ridge", Ridge(alpha=alpha))])


def main() -> None:
    # ------------------------------------------------------------------ 8.1 dataset
    section("8.1 Dataset: one row per region and CAT hour (sum of energy_kwh, cleaned readings)")
    with psycopg.connect() as conn:
        cur = conn.execute("""
            SELECT m.region, time_bucket('1 hour', r.event_time, 'Africa/Kigali') AS target_time,
                   sum(r.energy_kwh) AS energy_kwh, count(*) AS readings
            FROM energy_readings r JOIN meters m USING (meter_id)
            GROUP BY 1, 2 ORDER BY 1, 2""")
        raw = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    raw["target_time"] = pd.to_datetime(raw["target_time"], utc=True).dt.tz_convert(TZ)

    regions = sorted(raw["region"].unique(), key=lambda r: int(r.split()[1]))
    hours = pd.date_range("2026-09-01 00:00", "2026-09-28 23:00", freq="h", tz=TZ)
    grid = pd.MultiIndex.from_product([regions, hours], names=["region", "target_time"])
    full = raw.set_index(["region", "target_time"]).reindex(grid)
    print(f"Expected region-hours: {len(grid):,} (10 regions x 672 hours); present: {full.energy_kwh.notna().sum():,}; "
          f"absent: {full.energy_kwh.isna().sum()}")
    print(f"Readings per region-hour: min {int(full.readings.min())}, mean {full.readings.mean():.1f}, "
          f"max {int(full.readings.max())} (planned 1,600 = 400 meters x 4 readings); total {int(full.readings.sum()):,}")
    print("Handling: observed totals are kept unchanged as targets (no imputation); a missing predictor "
          "value (none expected beyond the initial lookback) excludes the row.")

    df = full.reset_index()
    s = full["energy_kwh"]
    df["origin"] = df["target_time"].dt.floor("D")                       # 00:00 CAT of target day
    df["hour_of_day"] = df["target_time"].dt.hour
    df["day_of_week"] = df["target_time"].dt.dayofweek                   # 0 = Monday
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)

    def lag(hours_back: int) -> np.ndarray:
        keys = pd.MultiIndex.from_arrays([df["region"], df["target_time"] - pd.Timedelta(hours=hours_back)])
        return s.reindex(keys).to_numpy()

    df["lag_24h"], df["lag_48h"], df["lag_168h"] = lag(24), lag(48), lag(168)
    df["mean_same_hour_7d"] = np.mean(np.vstack([lag(24 * k) for k in range(1, 8)]), axis=0)
    daily = (full.reset_index().assign(day=lambda d: d.target_time.dt.floor("D"))
             .groupby(["region", "day"])["energy_kwh"].sum(min_count=24))
    df["prev_day_total"] = daily.reindex(pd.MultiIndex.from_arrays(
        [df["region"], df["origin"] - pd.Timedelta(days=1)])).to_numpy()
    yesterday_weekend = ((df["target_time"] - pd.Timedelta(days=1)).dt.dayofweek >= 5).astype(int)
    df["lag_24h_same_daytype"] = df["lag_24h"] * (df["is_weekend"] == yesterday_weekend)

    # leakage check: latest source timestamp of every feature must precede the forecast origin
    latest_source = {
        "lag_24h": (df.target_time - pd.Timedelta(hours=24), "lag 24 h"),
        "lag_48h": (df.target_time - pd.Timedelta(hours=48), "lag 48 h"),
        "lag_168h": (df.target_time - pd.Timedelta(hours=168), "lag 168 h"),
        "mean_same_hour_7d": (df.target_time - pd.Timedelta(hours=24), "window: lags 24,48,...,168 h"),
        "prev_day_total": (df.origin - pd.Timedelta(hours=1), "window: 24 h ending at origin"),
        "lag_24h_same_daytype": (df.target_time - pd.Timedelta(hours=24), "lag 24 h x same day type flag"),
    }
    section("8.1 Features: lag/window, earliest usable target, leakage check (source must precede origin)")
    rows = []
    for feat, (src, desc) in latest_source.items():
        usable = df[df[feat].notna()]
        rows.append({"feature": feat, "lag_or_window": desc,
                     "earliest_usable_target": usable.target_time.min(),
                     "rows_where_source_not_before_origin": int((src >= df.origin).sum())})
    for feat in ["hour_of_day", "day_of_week", "is_weekend", "region"]:
        rows.append({"feature": feat, "lag_or_window": "calendar / static (known in advance)",
                     "earliest_usable_target": df.target_time.min(), "rows_where_source_not_before_origin": 0})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\nFeature examples (Region 1, target 22 Sep 14:00 and 26 Sep (Sat) 14:00):")
    ex = df[(df.region == "Region 1") & (df.target_time.isin(
        [pd.Timestamp("2026-09-22 14:00", tz=TZ), pd.Timestamp("2026-09-26 14:00", tz=TZ)]))]
    print(ex[["target_time", "origin", "energy_kwh"] + NUMERIC].to_string(index=False))

    # ------------------------------------------------------------------ 8.1 splits
    section("8.1 Chronological split by target timestamp")
    for name, (a, b) in SPLITS.items():
        df.loc[(df.target_time >= pd.Timestamp(a, tz=TZ)) & (df.target_time < pd.Timestamp(b, tz=TZ)), "split"] = name
    complete = df[NUMERIC].notna().all(axis=1)
    train_all = df[df.split == "train"]
    train = df[(df.split == "train") & complete]
    val = df[(df.split == "validation") & complete]
    test = df[(df.split == "test") & complete]
    summary = pd.DataFrame([
        {"split": n, "rows": len(d), "first_target": d.target_time.min(), "last_target": d.target_time.max()}
        for n, d in [("train (retained)", train), ("validation", val), ("test", test)]])
    print(summary.to_string(index=False))
    print(f"\nTraining rows before lookback exclusion: {len(train_all):,}; excluded (lacking 168 h lookback): "
          f"{len(train_all) - len(train):,}; retained: {len(train):,}")
    print(f"Validation rows lacking features: {(df.split == 'validation').sum() - len(val)}; "
          f"test rows lacking features: {(df.split == 'test').sum() - len(test)}")

    # ------------------------------------------------------------------ 8.2 baseline
    section("8.2 Previous-day baseline (prediction = same hour on the previous day)")
    base_mae, base_rmse = mae_rmse(test.lag_24h, test.energy_kwh)
    print(f"Test predictions: {test.lag_24h.notna().sum():,} (7 days x 24 hours x 10 regions)")
    print(f"Baseline test MAE = {base_mae:.3f} kWh   RMSE = {base_rmse:.3f} kWh")
    vb = mae_rmse(val.lag_24h, val.energy_kwh)
    print(f"(for reference, baseline on validation: MAE = {vb[0]:.3f} kWh, RMSE = {vb[1]:.3f} kWh)")

    # ------------------------------------------------------------------ 8.3 model
    section("8.3 Ridge regression: configurations trained on the training split, compared on validation")
    print(f"Shared predictors: numeric {NUMERIC}; categorical (one-hot) {CATEGORICAL}")
    X_cols = NUMERIC + CATEGORICAL
    vrows, fitted = [], {}
    for cid, alpha in CONFIGS.items():
        model = make_model(alpha).fit(train[X_cols], train.energy_kwh)    # preprocessing fitted on train only
        fitted[cid] = model
        m, r = mae_rmse(model.predict(val[X_cols]), val.energy_kwh)
        vrows.append({"config_id": cid, "model_settings": f"Ridge(alpha={alpha}), StandardScaler + OneHotEncoder",
                      "training_rows": len(train), "validation_rows": len(val),
                      "validation_MAE_kWh": round(m, 3), "validation_RMSE_kWh": round(r, 3)})
        print(f"  trained {cid} (alpha={alpha}) on {len(train)} rows - validation MAE {m:.3f} kWh")
    vtab = pd.DataFrame(vrows)
    print("\n" + vtab.to_string(index=False))
    best = vtab.sort_values(["validation_MAE_kWh", "config_id"]).iloc[0]["config_id"]
    frozen = fitted[best]
    print(f"\nSelected configuration: {best} (lowest validation MAE); fitted model and preprocessing frozen "
          f"- no refitting on validation or test data.")

    test = test.copy()
    test["baseline_pred"] = test["lag_24h"]
    test["model_pred"] = frozen.predict(test[X_cols])
    print(f"Model test predictions: {len(test):,}")
    print("\nSample test predictions:")
    print(test[["region", "target_time", "origin", "energy_kwh", "baseline_pred", "model_pred"]]
          .head(10).round(3).to_string(index=False))

    # ------------------------------------------------------------------ 8.4 evaluation
    section("8.4 Test metrics (kWh): all 1,680 observations")
    mod_mae, mod_rmse = mae_rmse(test.model_pred, test.energy_kwh)
    overall = pd.DataFrame([{"method": "previous-day baseline", "MAE_kWh": base_mae, "RMSE_kWh": base_rmse},
                            {"method": f"Ridge {best}", "MAE_kWh": mod_mae, "RMSE_kWh": mod_rmse}]).round(3)
    print(overall.to_string(index=False))
    print(f"MAE improvement vs baseline: {100 * (base_mae - mod_mae) / base_mae:.1f} %; "
          f"RMSE improvement: {100 * (base_rmse - mod_rmse) / base_rmse:.1f} %")

    section("8.4 Test metrics per region (kWh)")
    per_region = []
    for reg in regions:
        t = test[test.region == reg]
        bm, br = mae_rmse(t.baseline_pred, t.energy_kwh)
        mm, mr = mae_rmse(t.model_pred, t.energy_kwh)
        per_region.append({"region": reg, "n": len(t), "baseline_MAE": bm, "baseline_RMSE": br,
                           "model_MAE": mm, "model_RMSE": mr, "MAE_improvement_pct": 100 * (bm - mm) / bm})
    per_region = pd.DataFrame(per_region).round(3)
    print(per_region.to_string(index=False))

    section("8.4 Error by test day (MAE, kWh) - where does the baseline fail?")
    test["date"] = test.target_time.dt.strftime("%a %d %b")
    by_day = test.groupby("date", sort=False).apply(lambda t: pd.Series({
        "baseline_MAE": mae_rmse(t.baseline_pred, t.energy_kwh)[0],
        "model_MAE": mae_rmse(t.model_pred, t.energy_kwh)[0]}), include_groups=False).round(3)
    print(by_day.to_string())

    section("8.4 Highest actual test hour per region (ties -> earliest)")
    peaks = []
    for reg in regions:
        t = test[test.region == reg].sort_values(["energy_kwh", "target_time"], ascending=[False, True]).iloc[0]
        peaks.append({"region": reg, "peak_hour": t.target_time, "actual_kWh": t.energy_kwh,
                      "baseline_pred": t.baseline_pred, "baseline_abs_err": abs(t.baseline_pred - t.energy_kwh),
                      "model_pred": t.model_pred, "model_abs_err": abs(t.model_pred - t.energy_kwh)})
    peaks = pd.DataFrame(peaks).round(3)
    print(peaks.to_string(index=False))

    t = test[test.region == PLOT_REGION].sort_values("target_time")
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(t.target_time, t.energy_kwh, label="Actual", color="black", linewidth=2)
    ax.plot(t.target_time, t.baseline_pred, label="Previous-day baseline", color="#d62728", linestyle="--")
    ax.plot(t.target_time, t.model_pred, label=f"Ridge {best}", color="#1f77b4")
    ax.set_title(f"{PLOT_REGION}: hourly consumption, test week 22-28 Sept 2026 (CAT)")
    ax.set_ylabel("Energy (kWh per hour)"); ax.legend()
    fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(CHARTS / "8_4_forecast_plot.png", dpi=130); plt.close(fig)

    df.to_csv(OUT / "section8_dataset.csv", index=False)
    vtab.to_csv(OUT / "section8_validation.csv", index=False)
    test.to_csv(OUT / "section8_test_predictions.csv", index=False)
    overall.to_csv(OUT / "section8_overall_metrics.csv", index=False)
    per_region.to_csv(OUT / "section8_region_metrics.csv", index=False)
    peaks.to_csv(OUT / "section8_peak_hours.csv", index=False)
    print(f"\nSaved results/section8_*.csv and {CHARTS / '8_4_forecast_plot.png'}")


if __name__ == "__main__":
    main()