"""
Ablation study: which parts of the signal definition actually carry the edge?

The backtest answers "does the filtered signal set beat the benchmark?".
It does not answer "which filter is doing the work?" - and citing the
papers that motivated each filter is not the same as showing the filter
earns its place in *this* dataset.

This splits the backtested signals along one dimension at a time and tests
whether the two halves genuinely differ, rather than differing by noise:

  cluster    2+ distinct insiders on a ticker vs a lone buyer
             (Alldredge & Cicero 2015 report roughly double the abnormal
             return for clustered purchases)
  role       Officer vs Director
  size       large purchases vs small, split at the median dollar value

Every return here is already benchmark-adjusted (excess over SPY over the
same window), so an arm beating zero means beating the index, not just
rising with it.

Reads data/backtest/signals_with_returns.csv, which the backtest already
produces - no network calls, no API keys, no re-downloading SEC quarters.
Writes data/backtest/ablation.json and data/backtest/ablation_report.md.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backtest")
SIGNALS_CSV = os.path.join(OUT_DIR, "signals_with_returns.csv")
HORIZONS = (5, 30, 90)

# Below this an arm's statistics are too thin to read anything into, so it
# is reported but explicitly flagged rather than quietly compared.
MIN_ARM_N = 30


def describe(values):
    """Summary stats for one arm's excess returns."""
    v = pd.Series(values).dropna()
    n = int(len(v))
    if n == 0:
        return {"n": 0}
    out = {
        "n": n,
        "mean": float(v.mean()),
        "median": float(v.median()),
        "hit_rate": float((v > 0).mean()),
        "std": float(v.std(ddof=1)) if n > 1 else None,
    }
    if n > 1 and v.std(ddof=1) > 0:
        t, p = stats.ttest_1samp(v, 0.0)
        out["t_stat"] = float(t)
        out["p_value"] = float(p)
    else:
        out["t_stat"] = None
        out["p_value"] = None
    out["underpowered"] = n < MIN_ARM_N
    return out


def compare(a_vals, b_vals):
    """Welch's t-test between two arms - unequal variances, unequal sizes.

    This is the part that actually matters: an arm can look better on its
    mean while being statistically indistinguishable from the other one.
    """
    a = pd.Series(a_vals).dropna()
    b = pd.Series(b_vals).dropna()
    if len(a) < 2 or len(b) < 2:
        return {"difference": None, "p_value": None, "significant_5pct": None}
    t, p = stats.ttest_ind(a, b, equal_var=False)
    return {
        "difference": float(a.mean() - b.mean()),
        "t_stat": float(t),
        "p_value": float(p),
        "significant_5pct": bool(p < 0.05),
    }


def split_arms(df):
    """Each ablation as (name, description, mask_a, label_a, mask_b, label_b)."""
    arms = []

    if "n_insiders" in df.columns:
        cluster = df["n_insiders"] >= 2
        arms.append((
            "cluster_buying",
            "2+ distinct insiders buying the same ticker vs a single insider",
            cluster, "cluster (2+ insiders)",
            ~cluster, "solitary (1 insider)",
        ))

    if "title" in df.columns:
        title = df["title"].fillna("")
        officer = title.str.contains("Officer", case=False, na=False)
        director = title.str.contains("Director", case=False, na=False) & ~officer
        arms.append((
            "insider_role",
            "Officers vs Directors (rows counted as an officer where both apply)",
            officer, "Officer",
            director, "Director",
        ))

    if "total_value" in df.columns:
        median_value = df["total_value"].median()
        large = df["total_value"] >= median_value
        arms.append((
            "purchase_size",
            f"Purchases at or above the median (${median_value:,.0f}) vs below it",
            large, f"large (>= ${median_value:,.0f})",
            ~large, f"small (< ${median_value:,.0f})",
        ))

    return arms


def main():
    if not os.path.exists(SIGNALS_CSV):
        print(f"No backtest signals found at {SIGNALS_CSV} - run backtest_insider_signals.py first.")
        return 1

    df = pd.read_csv(SIGNALS_CSV)
    print(f"Loaded {len(df)} backtested signals.")

    results = {"signal_count": int(len(df)), "min_arm_n": MIN_ARM_N, "ablations": {}}

    for key, description, mask_a, label_a, mask_b, label_b in split_arms(df):
        entry = {"description": description, "arms": {label_a: {}, label_b: {}}, "comparison": {}}
        for h in HORIZONS:
            col = f"excess_ret_{h}d"
            if col not in df.columns:
                continue
            a_vals = df.loc[mask_a, col]
            b_vals = df.loc[mask_b, col]
            entry["arms"][label_a][f"{h}d"] = describe(a_vals)
            entry["arms"][label_b][f"{h}d"] = describe(b_vals)
            entry["comparison"][f"{h}d"] = compare(a_vals, b_vals)
        results["ablations"][key] = entry

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "ablation.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    write_report(results)
    print(f"Wrote ablation.json and ablation_report.md to {os.path.normpath(OUT_DIR)}")
    return 0


def pct(x):
    return "-" if x is None else f"{x * 100:+.2f}%"


def write_report(results):
    lines = [
        "# Ablation study: which filters carry the edge?",
        "",
        f"Based on {results['signal_count']:,} backtested insider-buy signals. "
        "All returns are **excess over SPY** across the same window, so a positive "
        "number means beating the index rather than merely rising with it.",
        "",
        "`p` on each arm tests whether that arm's excess return differs from zero. "
        "The **difference** row is a Welch's t-test between the two arms - the "
        "question of whether the split actually separates anything, which a "
        "gap in the means alone cannot answer.",
        "",
    ]

    for key, entry in results["ablations"].items():
        lines += [f"## {key.replace('_', ' ').title()}", "", entry["description"], ""]
        labels = list(entry["arms"].keys())
        lines += [
            "| Horizon | " + " | ".join(f"{l} (n, mean, p)" for l in labels) + " | Difference | Significant? |",
            "|---|" + "---|" * (len(labels) + 2),
        ]
        for h in HORIZONS:
            hk = f"{h}d"
            cells = []
            thin = False
            for l in labels:
                a = entry["arms"][l].get(hk, {})
                if not a.get("n"):
                    cells.append("-")
                    continue
                if a.get("underpowered"):
                    thin = True
                p = a.get("p_value")
                cells.append(f"n={a['n']:,}, {pct(a.get('mean'))}, p={p:.3f}" if p is not None else f"n={a['n']:,}, {pct(a.get('mean'))}")
            c = entry["comparison"].get(hk, {})
            sig = c.get("significant_5pct")
            sig_txt = "-" if sig is None else ("**yes**" if sig else "no")
            if thin:
                sig_txt += " (thin sample)"
            diff = pct(c.get("difference")) if c.get("difference") is not None else "-"
            if c.get("p_value") is not None:
                diff += f" (p={c['p_value']:.3f})"
            lines.append(f"| {h}d | " + " | ".join(cells) + f" | {diff} | {sig_txt} |")
        lines.append("")

    lines += [
        "## Reading this honestly",
        "",
        "An arm that beats the other on its mean but shows **no** in the significance "
        "column has not been shown to be better - the gap is within what noise of "
        "this sample size produces. Splitting an already-filtered set also shrinks "
        "each arm, so a real effect can fail to reach significance here purely for "
        "lack of data; absence of evidence is not evidence of absence.",
        "",
        "This table runs one test per split per horizon. Across that many tests, "
        "roughly one result at the 5% threshold is expected from chance alone, so a "
        "single borderline `p` just under 0.05 deserves much less weight than a "
        "result that clears it comfortably. No multiple-comparison correction is "
        "applied here - the raw p-values are reported so the arithmetic stays "
        "visible rather than buried in an adjustment.",
        "",
        "These arms are all *subsets* of the filtered signal set. They cannot say "
        "anything about the filters applied before this stage - the 10b5-1 "
        "exclusion and the $100K floor remove their rows upstream, so no return "
        "was ever computed for them. Testing those would mean re-running the "
        "backtest with each filter disabled.",
        "",
    ]

    with open(os.path.join(OUT_DIR, "ablation_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
