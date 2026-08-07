"""Generates the chart used in the backtest write-up, from the CSVs already
produced by backtest_insider_signals.py (no network calls)."""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from scipy import stats

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backtest")
HORIZONS = (5, 30, 90)


def main():
    results = pd.read_csv(os.path.join(OUT_DIR, "signals_with_returns.csv"), parse_dates=["filing_date"])
    signals = pd.read_csv(os.path.join(OUT_DIR, "signals_raw.csv"), parse_dates=["filing_date"])
    cutoff = signals["filing_date"].quantile(0.7)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))

    # Panel 1: mean vs median excess return by horizon (full sample) - shows the skew.
    means, medians = [], []
    for h in HORIZONS:
        vals = results[f"excess_ret_{h}d"].dropna()
        means.append(vals.mean() * 100)
        medians.append(vals.median() * 100)
    x = range(len(HORIZONS))
    w = 0.35
    ax = axes[0]
    ax.bar([i - w / 2 for i in x], means, width=w, label="Mean", color="#2e7d4f")
    ax.bar([i + w / 2 for i in x], medians, width=w, label="Median", color="#9a3324")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{h}d" for h in HORIZONS])
    ax.set_ylabel("Excess return vs SPY (%)")
    ax.set_title("Mean vs. median excess return\n(full sample) - mean is pulled up by a few large winners")
    ax.legend()

    # Panel 2: mean excess return by horizon, in-sample vs out-of-sample - shows decay/reversal.
    ax = axes[1]
    for label, sub, color in [
        ("In-sample", results[results["filing_date"] < cutoff], "#2e7d4f"),
        ("Out-of-sample", results[results["filing_date"] >= cutoff], "#e6391d"),
    ]:
        vals = [sub[f"excess_ret_{h}d"].dropna().mean() * 100 for h in HORIZONS]
        ax.plot([str(h) + "d" for h in HORIZONS], vals, marker="o", label=label, color=color)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Mean excess return vs SPY (%)")
    ax.set_title("Mean excess return by horizon\nin-sample vs. out-of-sample")
    ax.legend()

    fig.suptitle("ZycaAlgo insider-buy signal: forward excess return vs. SPY", fontsize=12, fontweight="bold")
    fig.tight_layout()
    out_path = os.path.join(OUT_DIR, "backtest_chart.png")
    fig.savefig(out_path, dpi=150)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
