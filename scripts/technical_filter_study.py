"""
Does a technical confirmation filter actually improve the insider signal?

The insider filters already in the pipeline each trace to a paper. A
technical overlay - "only take the buy if the chart also looks healthy" -
is a reasonable idea, but adding one because it sounds sensible is exactly
the black-box move this project avoids. So measure it first.

For every signal in the backtest, this computes a handful of standard
indicators as they stood *before* entry, then splits the signals on each
condition and tests whether the halves genuinely differ.

Conditions tested:
  above_sma200   price above its 200-day average (long-term uptrend;
                 the classic trend filter, cf. Faber 2007)
  above_sma50    price above its 50-day average (medium-term trend)
  rsi_not_hot    RSI(14) below 70 - not buying into an extended move
  volume_surge   entry-day volume above its own 20-day average
  near_high      within 25% of the 52-week high

LOOKAHEAD: every indicator uses only bars dated strictly before the
filing date. Using the filing day's own bar would leak information the
strategy could not have had, and would make any result meaningless.

Prices come from Yahoo's public chart endpoint and are cached per ticker
under data/backtest/_price_cache/, so a rerun costs nothing.

Writes data/backtest/technical_filters.json and
data/backtest/technical_filter_report.md.
"""

import json
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

from ablation_study import HORIZONS, compare, describe, pct

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backtest")
SIGNALS_CSV = os.path.join(OUT_DIR, "signals_with_returns.csv")
CACHE_DIR = os.path.join(OUT_DIR, "_price_cache")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Enough history before the earliest signal to warm up a 200-day average.
HISTORY_START = "2022-06-01"
HISTORY_END = "2025-10-01"


def fetch_history(ticker):
    """Daily bars for one ticker, cached. Returns a DataFrame or None."""
    safe = ticker.replace("/", "_").replace("\\", "_")
    path = os.path.join(CACHE_DIR, f"{safe}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = None
        if raw is not None:
            return _to_frame(raw)

    p1 = int(pd.Timestamp(HISTORY_START).timestamp())
    p2 = int(pd.Timestamp(HISTORY_END).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?period1={p1}&period2={p2}&interval=1d")

    raw = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code >= 400:
                break
            res = (r.json().get("chart") or {}).get("result")
            if not res:
                break
            q = res[0]["indicators"]["quote"][0]
            raw = {"t": res[0].get("timestamp") or [],
                   "c": q.get("close") or [], "v": q.get("volume") or []}
            break
        except Exception:
            time.sleep(1.5 * (attempt + 1))

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)  # cache misses too, so we don't retry them every run
    return _to_frame(raw)


def _to_frame(raw):
    if not raw or not raw.get("t"):
        return None
    df = pd.DataFrame({
        "date": pd.to_datetime(raw["t"], unit="s").normalize(),
        "close": raw["c"],
        "volume": raw["v"],
    }).dropna(subset=["close"])
    return df.sort_values("date").reset_index(drop=True) if len(df) else None


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()

    # A window with no down days has zero average loss. Dividing by it gives
    # NaN, which would quietly drop exactly the strongest uptrends from the
    # study instead of scoring them - RSI is 100 there by definition, and 50
    # when the price hasn't moved at all.
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gain / loss
    out = 100 - (100 / (1 + rs))
    flat_loss = loss == 0
    out = out.mask(flat_loss & (gain > 0), 100.0)
    out = out.mask(flat_loss & (gain <= 0), 50.0)
    return out


def indicators_before(df, entry_date):
    """Indicator values from the last bar STRICTLY before entry_date."""
    prior = df[df["date"] < entry_date]
    if len(prior) < 200:
        return None  # not enough history to judge a 200-day trend

    close = prior["close"]
    vol = prior["volume"]
    last = close.iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    r = rsi(close).iloc[-1]
    avg_vol = vol.rolling(20).mean().iloc[-1]
    high_52w = close.tail(252).max()

    if pd.isna(sma200) or pd.isna(sma50):
        return None

    return {
        "above_sma200": bool(last > sma200),
        "above_sma50": bool(last > sma50),
        "rsi_not_hot": bool(r < 70) if pd.notna(r) else None,
        "volume_surge": bool(vol.iloc[-1] > avg_vol) if pd.notna(avg_vol) else None,
        "near_high": bool(last >= 0.75 * high_52w) if pd.notna(high_52w) else None,
    }


CONDITIONS = {
    "above_sma200": "Price above its 200-day moving average (long-term uptrend)",
    "above_sma50": "Price above its 50-day moving average (medium-term trend)",
    "rsi_not_hot": "RSI(14) below 70 (not buying into an overbought move)",
    "volume_surge": "Volume above its own 20-day average on the prior bar",
    "near_high": "Within 25% of the 52-week high",
}


def main():
    if not os.path.exists(SIGNALS_CSV):
        print(f"No backtest signals at {SIGNALS_CSV} - run backtest_insider_signals.py first.")
        return 1

    signals = pd.read_csv(SIGNALS_CSV)
    signals["filing_date"] = pd.to_datetime(signals["filing_date"])
    tickers = sorted(signals["ticker"].dropna().unique())
    print(f"{len(signals)} signals across {len(tickers)} tickers.")

    histories, misses = {}, 0
    for i, t in enumerate(tickers, 1):
        cached = os.path.exists(os.path.join(CACHE_DIR, f"{t}.json"))
        histories[t] = fetch_history(t)
        if histories[t] is None:
            misses += 1
        if not cached:
            time.sleep(0.35)  # be polite to Yahoo on first build
        if i % 100 == 0:
            print(f"  {i}/{len(tickers)} tickers ({misses} without usable history)")

    rows = []
    for _, s in signals.iterrows():
        df = histories.get(s["ticker"])
        if df is None:
            continue
        ind = indicators_before(df, s["filing_date"])
        if ind is None:
            continue
        row = {c: ind[c] for c in CONDITIONS}
        row["filing_date"] = s["filing_date"]
        for h in HORIZONS:
            row[f"excess_ret_{h}d"] = s.get(f"excess_ret_{h}d")
        rows.append(row)

    data = pd.DataFrame(rows)
    print(f"{len(data)} signals had enough price history to evaluate "
          f"({len(signals) - len(data)} dropped).")
    if data.empty:
        print("Nothing to test.")
        return 1

    results = {"evaluated": int(len(data)), "of_total": int(len(signals)),
               "conditions": {}, "holdout": {}}

    # Anything that looks good across five conditions and three horizons could
    # just be the luckiest of fifteen draws. Re-run each split either side of
    # the backtest's own in-sample cutoff: a real effect shows up in both.
    cutoff = None
    summary_path = os.path.join(OUT_DIR, "summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                cutoff = pd.Timestamp(json.load(f)["in_sample_cutoff"])
        except Exception:
            cutoff = None

    if cutoff is not None:
        results["holdout"]["cutoff"] = str(cutoff.date())
        for cond in CONDITIONS:
            per_period = {}
            for label, sub in (("in_sample", data[data["filing_date"] <= cutoff]),
                               ("out_of_sample", data[data["filing_date"] > cutoff])):
                per_period[label] = {
                    f"{h}d": compare(sub.loc[sub[cond] == True, f"excess_ret_{h}d"],   # noqa: E712
                                     sub.loc[sub[cond] == False, f"excess_ret_{h}d"])  # noqa: E712
                    for h in HORIZONS
                }
            results["holdout"][cond] = per_period

    for cond, desc in CONDITIONS.items():
        passed = data[cond] == True   # noqa: E712 - None must not count as False
        failed = data[cond] == False  # noqa: E712
        entry = {"description": desc, "n_pass": int(passed.sum()), "n_fail": int(failed.sum()),
                 "pass": {}, "fail": {}, "comparison": {}}
        for h in HORIZONS:
            col = f"excess_ret_{h}d"
            entry["pass"][f"{h}d"] = describe(data.loc[passed, col])
            entry["fail"][f"{h}d"] = describe(data.loc[failed, col])
            entry["comparison"][f"{h}d"] = compare(data.loc[passed, col], data.loc[failed, col])
        results["conditions"][cond] = entry

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "technical_filters.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    write_report(results)
    print("Wrote technical_filters.json and technical_filter_report.md")
    return 0


def write_report(results):
    lines = [
        "# Would a technical confirmation filter help?",
        "",
        f"Tested on {results['evaluated']:,} of {results['of_total']:,} backtested insider "
        "signals (the rest lacked enough price history for a 200-day average). Returns are "
        "**excess over SPY**. Every indicator is computed from bars dated strictly *before* "
        "the filing date, so nothing here uses information the strategy wouldn't have had.",
        "",
        "A filter is only worth adding if the **passing** signals genuinely beat the ones it "
        "would have thrown away. A filter that keeps the good ones *and* the bad ones equally "
        "is just throwing away trades.",
        "",
    ]
    for cond, e in results["conditions"].items():
        lines += [f"## `{cond}`", "", e["description"], "",
                  f"Passes: {e['n_pass']:,} signals · Filtered out: {e['n_fail']:,}", "",
                  "| Horizon | Passes filter | Would be discarded | Difference | Worth it? |",
                  "|---|---|---|---|---|"]
        for h in HORIZONS:
            hk = f"{h}d"
            p, fl, c = e["pass"][hk], e["fail"][hk], e["comparison"][hk]
            pv = f"n={p['n']:,}, {pct(p.get('mean'))}" if p.get("n") else "-"
            fv = f"n={fl['n']:,}, {pct(fl.get('mean'))}" if fl.get("n") else "-"
            diff = pct(c.get("difference")) if c.get("difference") is not None else "-"
            if c.get("p_value") is not None:
                diff += f" (p={c['p_value']:.3f})"
            sig = c.get("significant_5pct")
            verdict = "-" if sig is None else ("**yes**" if sig and (c.get("difference") or 0) > 0
                                               else ("no - and worse" if (c.get("difference") or 0) < 0 else "no"))
            lines.append(f"| {h}d | {pv} | {fv} | {diff} | {verdict} |")
        lines.append("")

    ho = results.get("holdout") or {}
    if ho.get("cutoff"):
        lines += [
            "## Does it survive out-of-sample?",
            "",
            f"Split at the backtest's own in-sample cutoff (**{ho['cutoff']}**). A filter that "
            "only works on one side of this line is a pattern found in the data, not a property "
            "of the strategy. Values are the difference between passing and discarded signals.",
            "",
            "| Condition | Period | 5d | 30d | 90d |",
            "|---|---|---|---|---|",
        ]
        for cond in CONDITIONS:
            per = ho.get(cond) or {}
            for label in ("in_sample", "out_of_sample"):
                cells = []
                for h in HORIZONS:
                    c = (per.get(label) or {}).get(f"{h}d") or {}
                    d = c.get("difference")
                    cells.append("-" if d is None else f"{pct(d)} (p={c['p_value']:.3f})")
                lines.append(f"| `{cond}` | {label.replace('_', '-')} | " + " | ".join(cells) + " |")
        lines.append("")

    lines += [
        "## How to read this",
        "",
        "A **yes** means signals passing that filter beat the ones it would discard by more "
        "than noise explains, at that horizon. **no** means the filter would cost you trades "
        "without buying anything measurable. A negative difference means the filter is "
        "actively selecting the *worse* half.",
        "",
        "Five conditions across three horizons is fifteen tests, so roughly one 5% result is "
        "expected by chance. Treat a single marginal p-value with suspicion; a filter worth "
        "shipping should hold up across horizons, not appear at exactly one.",
        "",
    ]
    with open(os.path.join(OUT_DIR, "technical_filter_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
