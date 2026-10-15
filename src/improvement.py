"""Section 4.3 - % improvement of compressed tables vs the uncompressed 1-day baseline.
Run: docker compose exec app python src/improvement.py
Positive = compressed table is faster.
"""
import pandas as pd

s = pd.read_csv("results/section4_summary.csv")
base = s[s.table == "energy_readings"].set_index("query")["median_ms"]
parts = []
for t in ["energy_time_compressed", "energy_meter_compressed"]:
    comp = s[s.table == t].set_index("query")["median_ms"]
    parts.append(pd.DataFrame({"table": t, "baseline_median_ms": base, "compressed_median_ms": comp,
                               "pct_improvement": (100 * (base - comp) / base).round(1)}))
out = pd.concat(parts).reset_index()
out.to_csv("results/section4_improvement.csv", index=False)
print(out.to_string(index=False))