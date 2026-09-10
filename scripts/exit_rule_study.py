"""
Does the live exit rule actually suit the signal it trades?

The backtest measures the entry signal at fixed horizons and finds the edge
concentrated at five days: +2.38% mean excess at 5d, with the median already
negative by 30d. The live system, though, exits on a 15% stop, a trailing
stop that only arms after +15%, and a 90-day timeout - so positions are held
for weeks. Nothing in those rules exits near the point where the measured
advantage exists.

This replays every backtested signal under several exit rules on real daily
bars and compares them on the same footing:

    live        what the system does today
    hold_5      close at the 5th trading day
    hold_10     close at the 10th
    hold_20     close at the 20th
    hold_5_stop 5-day hold, but exit early if the 15% stop is hit first

Every result is excess return over SPY across that position's own holding
window, so a rule that simply held through a rising market is not credited
for it. Entry is the close of the first bar on or after the filing date -
the earliest point a scanner reading that day's index could have acted.

Prices come from the cache the technical filter study already built
(data/backtest/_price_cache), so this runs offline apart from SPY.

Writes data/backtest/exit_rules.json and exit_rule_report.md.
"""

import json
import os
import statistics
import sys
import time

import pandas as pd
import requests
from scipy import stats

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "backtest")
SIGNALS_CSV = os.path.join(OUT_DIR, "signals_with_returns.csv")
CACHE_DIR = os.path.join(OUT_DIR, "_price_cache")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

STOP_PCT = 0.15
TRAIL_TRIGGER = 0.15
TRAIL_PCT = 0.10
TIME_STOP_DAYS = 90
TIME_STOP_BAND = 0.03


def load_bars(ticker):
    path = os.path.join(CACHE_DIR, f"{ticker}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    if not raw or not raw.get("t"):
        return None
    rows = []
    for t, c, v in zip(raw["t"], raw.get("c") or [], raw.get("v") or []):
        if c is None:
            continue
        rows.append({"date": pd.Timestamp(t, unit="s").normalize().date().isoformat(), "close": c})
    return rows or None


def fetch_spy():
    """SPY closes by date, cached alongside the rest."""
    path = os.path.join(CACHE_DIR, "SPY.json")
    bars = load_bars("SPY")
    if bars:
        return {b["date"]: b["close"] for b in bars}
    r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/SPY"
                     "?range=5y&interval=1d", headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    raw = {"t": res["timestamp"], "c": q["close"], "v": q.get("volume") or []}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    return {b["date"]: b["close"] for b in load_bars("SPY")}


def live_rule(bars, entry_idx):
    """The exit the system uses today. Returns (exit_index, reason)."""
    entry = bars[entry_idx]["close"]
    stop = entry * (1 - STOP_PCT)
    high = entry
    trailing = False
    for i in range(entry_idx + 1, len(bars)):
        close = bars[i]["close"]
        # Daily closes only here - the cache has no intraday lows, so a stop
        # is treated as triggering on a close through it. That makes this a
        # slightly forgiving version of the live rule, not a harsher one.
        if close <= stop:
            return i, ("trailing stop" if trailing else "stop loss")
        if close > high:
            high = close
        if not trailing and high >= entry * (1 + TRAIL_TRIGGER):
            trailing = True
        if trailing:
            stop = max(stop, high * (1 - TRAIL_PCT))
        held = i - entry_idx
        if held >= TIME_STOP_DAYS and abs((close - entry) / entry) <= TIME_STOP_BAND:
            return i, "time stop"
    return len(bars) - 1, "still open"


def fixed_rule(bars, entry_idx, days, use_stop=False):
    entry = bars[entry_idx]["close"]
    stop = entry * (1 - STOP_PCT)
    last = min(entry_idx + days, len(bars) - 1)
    if use_stop:
        for i in range(entry_idx + 1, last + 1):
            if bars[i]["close"] <= stop:
                return i, "stop loss"
    return last, f"{days}-day exit"


RULES = {
    "live": lambda b, i: live_rule(b, i),
    "hold_5": lambda b, i: fixed_rule(b, i, 5),
    "hold_10": lambda b, i: fixed_rule(b, i, 10),
    "hold_20": lambda b, i: fixed_rule(b, i, 20),
    "hold_5_stop": lambda b, i: fixed_rule(b, i, 5, use_stop=True),
}


def describe(values):
    v = [x for x in values if x is not None]
    if len(v) < 2:
        return {"n": len(v)}
    t, p = stats.ttest_1samp(v, 0.0)
    return {
        "n": len(v),
        "mean": statistics.fmean(v),
        "median": statistics.median(v),
        "hit_rate": sum(1 for x in v if x > 0) / len(v),
        "t_stat": float(t),
        "p_value": float(p),
    }


def main():
    if not os.path.exists(SIGNALS_CSV):
        print(f"No backtested signals at {SIGNALS_CSV}.")
        return 1

    signals = pd.read_csv(SIGNALS_CSV)
    signals["filing_date"] = pd.to_datetime(signals["filing_date"]).dt.date.astype(str)
    spy = fetch_spy()
    spy_days = sorted(spy)

    def spy_on(day):
        prior = [d for d in spy_days if d <= day]
        return spy[prior[-1]] if prior else None

    results = {name: [] for name in RULES}
    held = {name: [] for name in RULES}
    reasons = {name: {} for name in RULES}
    evaluated = 0

    cache = {}
    for _, s in signals.iterrows():
        ticker = s["ticker"]
        if ticker not in cache:
            cache[ticker] = load_bars(ticker)
        bars = cache[ticker]
        if not bars:
            continue

        entry_idx = next((i for i, b in enumerate(bars) if b["date"] >= s["filing_date"]), None)
        if entry_idx is None or entry_idx >= len(bars) - 2:
            continue
        entry_price = bars[entry_idx]["close"]
        spy_in = spy_on(bars[entry_idx]["date"])
        if not spy_in or not entry_price:
            continue
        evaluated += 1

        for name, rule in RULES.items():
            exit_idx, reason = rule(bars, entry_idx)
            exit_price = bars[exit_idx]["close"]
            spy_out = spy_on(bars[exit_idx]["date"])
            if not spy_out:
                continue
            stock_ret = (exit_price - entry_price) / entry_price
            bench_ret = (spy_out - spy_in) / spy_in
            results[name].append(stock_ret - bench_ret)
            held[name].append(exit_idx - entry_idx)
            reasons[name][reason] = reasons[name].get(reason, 0) + 1

    out = {"evaluated": evaluated, "rules": {}}
    for name in RULES:
        out["rules"][name] = {
            **describe(results[name]),
            "median_days_held": statistics.median(held[name]) if held[name] else None,
            "exit_reasons": reasons[name],
        }

    # Is each alternative actually different from what the system does now,
    # or just different-looking? Paired, because every rule trades the same
    # signals - the only thing that varies is when it gets out.
    base = results["live"]
    for name in RULES:
        if name == "live":
            continue
        pairs = list(zip(base, results[name]))
        if len(pairs) > 2:
            t, p = stats.ttest_rel([a for a, _ in pairs], [b for _, b in pairs])
            out["rules"][name]["vs_live"] = {
                "difference": statistics.fmean([b - a for a, b in pairs]),
                "p_value": float(p),
                "significant_5pct": bool(p < 0.05),
            }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "exit_rules.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    write_report(out)
    print(f"Evaluated {evaluated} signals across {len(RULES)} exit rules.")
    for name, d in out["rules"].items():
        if d.get("n"):
            extra = ""
            if "vs_live" in d:
                extra = f"   vs live {d['vs_live']['difference']*100:+.2f}pp (p={d['vs_live']['p_value']:.3g})"
            print(f"  {name:<12} mean {d['mean']*100:+.2f}%  median {d['median']*100:+.2f}%  "
                  f"hit {d['hit_rate']*100:.0f}%  held {d['median_days_held']:.0f}d{extra}")
    return 0


def write_report(out):
    lines = [
        "# Does the exit rule suit the signal?",
        "",
        f"Every one of {out['evaluated']:,} backtested signals replayed under five exit rules on "
        "real daily bars. Returns are **excess over SPY** across each position's own holding "
        "window, so a rule is never credited simply for holding through a rising market.",
        "",
        "| Exit rule | Mean excess | Median | Hit rate | Days held | vs. live |",
        "|---|---|---|---|---|---|",
    ]
    labels = {
        "live": "Live rules (15% stop, trail, 90d)",
        "hold_5": "Fixed 5-day hold",
        "hold_10": "Fixed 10-day hold",
        "hold_20": "Fixed 20-day hold",
        "hold_5_stop": "5-day hold with 15% stop",
    }
    for name, d in out["rules"].items():
        if not d.get("n"):
            continue
        vs = "—"
        if "vs_live" in d:
            v = d["vs_live"]
            vs = f"{v['difference']*100:+.2f}pp (p={v['p_value']:.3f})"
            if v["significant_5pct"]:
                vs = f"**{vs}**"
        lines.append(
            f"| {labels.get(name, name)} | {d['mean']*100:+.2f}% | {d['median']*100:+.2f}% | "
            f"{d['hit_rate']*100:.0f}% | {d['median_days_held']:.0f} | {vs} |"
        )

    lines += [
        "",
        "## Reading this",
        "",
        "The comparison against the live rule is paired: every rule trades exactly the same "
        "signals, and the only thing that differs is when it exits. That removes signal "
        "selection from the comparison entirely and leaves the exit timing as the sole variable.",
        "",
        "A result here says nothing about whether the entry signal is good. It says only "
        "whether the system is holding its positions for the right length of time.",
        "",
        "Daily closes are used, so a stop is treated as triggering on a close through the level "
        "rather than an intraday touch. That makes the live rule look slightly *better* here "
        "than it would in practice, not worse.",
        "",
    ]
    with open(os.path.join(OUT_DIR, "exit_rule_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
