"""
Historical backtest of the ZycaAlgo insider-buy signal.

Uses SEC's official bulk quarterly Form 3/4/5 datasets (pre-parsed TSVs,
not per-filing XML scraping) to reconstruct every historical signal that
would have matched the live scanner's definition: transaction code P,
officer or director, not under a 10b5-1 plan, dollar value >= $100k.

For each signal, computes forward returns (5/30/90 trading days from the
first trading day on/after the filing date - i.e. when the signal would
actually have been actionable) against a SPY benchmark, then reports
mean excess return, hit rate, and significance, split into an in-sample
and out-of-sample period.

Requires APCA_API_KEY_ID / APCA_API_SECRET_KEY env vars (Alpaca paper
keys - only used here for historical market data, read-only).
"""

import csv
import io
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
from scipy import stats

SEC_HEADERS = {"User-Agent": "ZycaAlgo Research nattawutgorn@gmail.com"}
DATA_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
MIN_DOLLAR_VALUE = 100_000
HORIZONS = (5, 30, 90)
BENCHMARK = "SPY"

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backtest")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backtest", "_quarters_cache")


def quarters_between(start_year, start_q, end_year, end_q):
    y, q = start_year, start_q
    out = []
    while (y, q) <= (end_year, end_q):
        out.append(f"{y}q{q}")
        q += 1
        if q > 4:
            q = 1
            y += 1
    return out


def download_quarter(q):
    os.makedirs(CACHE_DIR, exist_ok=True)
    dest = os.path.join(CACHE_DIR, f"{q}.zip")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    url = DATA_URL.format(q=q)
    print(f"  downloading {url}")
    r = requests.get(url, headers=SEC_HEADERS, timeout=120)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)
    time.sleep(0.3)
    return dest


def load_quarter_signals(q):
    path = download_quarter(q)
    with zipfile.ZipFile(path) as zf:
        def read_tsv(name, usecols):
            with zf.open(name) as f:
                return pd.read_csv(
                    io.TextIOWrapper(f, encoding="latin-1"),
                    sep="\t", usecols=usecols, dtype=str, na_filter=False,
                )

        sub = read_tsv("SUBMISSION.tsv", [
            "ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
            "ISSUERTRADINGSYMBOL", "ISSUERNAME", "AFF10B5ONE",
        ])
        sub = sub[sub["DOCUMENT_TYPE"] == "4"]
        sub = sub[sub["ISSUERTRADINGSYMBOL"].str.len() > 0]
        sub = sub[sub["ISSUERTRADINGSYMBOL"] != "NONE"]

        own = read_tsv("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNER_RELATIONSHIP", "RPTOWNERNAME"])
        own = own[own["RPTOWNER_RELATIONSHIP"].str.contains("Director|Officer", regex=True)]
        # collapse to one row per accession (an accession can have >1 reporting owner;
        # keep the first officer/director owner as the attributed insider)
        own = own.drop_duplicates(subset="ACCESSION_NUMBER", keep="first")

        trans = read_tsv("NONDERIV_TRANS.tsv", [
            "ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE",
            "TRANS_SHARES", "TRANS_PRICEPERSHARE",
        ])
        trans = trans[trans["TRANS_CODE"] == "P"]

        df = trans.merge(sub, on="ACCESSION_NUMBER", how="inner")
        df = df.merge(own, on="ACCESSION_NUMBER", how="inner")

        df["AFF10B5ONE"] = df["AFF10B5ONE"].isin(["1", "true", "True"])
        df = df[~df["AFF10B5ONE"]]

        df["TRANS_SHARES"] = pd.to_numeric(df["TRANS_SHARES"], errors="coerce")
        df["TRANS_PRICEPERSHARE"] = pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce")
        df = df.dropna(subset=["TRANS_SHARES", "TRANS_PRICEPERSHARE"])
        df["total_value"] = df["TRANS_SHARES"] * df["TRANS_PRICEPERSHARE"]
        df = df[df["total_value"] >= MIN_DOLLAR_VALUE]

        df["filing_date"] = pd.to_datetime(df["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
        df = df.dropna(subset=["filing_date"])

        return df[[
            "ACCESSION_NUMBER", "filing_date", "ISSUERTRADINGSYMBOL", "ISSUERNAME",
            "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "TRANS_SHARES",
            "TRANS_PRICEPERSHARE", "total_value",
        ]].rename(columns={
            "ISSUERTRADINGSYMBOL": "ticker", "ISSUERNAME": "issuer",
            "RPTOWNERNAME": "insider", "RPTOWNER_RELATIONSHIP": "title",
        })


import re

_INVALID_SYMBOL_RE = re.compile(r"invalid symbol:\s*(\S+)")


def _fetch_chunk(chunk, start, end, key_id, secret, base, headers, out, depth=0):
    """Fetch one chunk of symbols. On an 'invalid symbol' 400, drop just that
    symbol and retry, rather than losing every other symbol in the chunk."""
    if not chunk:
        return
    page_token = None
    while True:
        params = {
            "symbols": ",".join(chunk), "timeframe": "1Day",
            "start": start, "end": end, "limit": 10000, "adjustment": "split",
        }
        if page_token:
            params["page_token"] = page_token
        r = requests.get(base, headers=headers, params=params, timeout=60)
        if r.status_code != 200:
            m = _INVALID_SYMBOL_RE.search(r.text)
            bad = m.group(1).strip() if m else None
            if bad and bad in chunk and depth < 20:
                print(f"  [warn] dropping invalid symbol '{bad}' and retrying chunk of {len(chunk)-1}")
                _fetch_chunk([s for s in chunk if s != bad], start, end, key_id, secret, base, headers, out, depth + 1)
            else:
                print(f"  [warn] bars fetch failed for {len(chunk)} symbols: {r.status_code} {r.text[:200]}")
            return
        data = r.json()
        for sym, bars in (data.get("bars") or {}).items():
            out.setdefault(sym, []).extend(bars)
        page_token = data.get("next_page_token")
        if not page_token:
            return


def fetch_bars(symbols, start, end, key_id, secret):
    """Fetch daily bars for a list of symbols from Alpaca, batching requests.
    A malformed symbol in a batch is dropped and the rest of the batch is
    retried, instead of losing every symbol in that batch."""
    out = {}
    CHUNK = 100
    base = "https://data.alpaca.markets/v2/stocks/bars"
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        _fetch_chunk(chunk, start, end, key_id, secret, base, headers, out)
        print(f"  fetched bars for symbols {i+1}-{min(i+CHUNK, len(symbols))}/{len(symbols)}")
    return out


def clean_ticker(t):
    t = t.strip().upper()
    if not t or " " in t or not re.fullmatch(r"[A-Z0-9.\-]+", t):
        return None
    return t


def bars_to_series(bars_list):
    s = pd.Series(
        {pd.Timestamp(b["t"]).tz_localize(None).normalize(): b["c"] for b in bars_list}
    ).sort_index()
    return s


def forward_return(price_series, entry_date, horizon_days):
    """entry_date: first trading day on/after this date is the entry.
    horizon_days: number of *trading days* forward for the exit."""
    idx = price_series.index
    pos = idx.searchsorted(entry_date)
    if pos >= len(idx):
        return None, None, None
    entry_price = price_series.iloc[pos]
    exit_pos = pos + horizon_days
    if exit_pos >= len(idx):
        return None, None, None
    exit_price = price_series.iloc[exit_pos]
    entry_d = idx[pos]
    exit_d = idx[exit_pos]
    return (exit_price / entry_price - 1.0), entry_d, exit_d


def main():
    key_id = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key_id or not secret:
        print("ERROR: set APCA_API_KEY_ID and APCA_API_SECRET_KEY env vars first.")
        sys.exit(1)

    start_year, start_q = 2023, 3
    end_year, end_q = 2025, 2
    quarters = quarters_between(start_year, start_q, end_year, end_q)
    print(f"Building signal set from {quarters[0]} to {quarters[-1]} ({len(quarters)} quarters)...")

    frames = []
    for q in quarters:
        print(f"quarter {q}")
        frames.append(load_quarter_signals(q))
    signals = pd.concat(frames, ignore_index=True)

    before_clean = len(signals)
    signals["ticker"] = signals["ticker"].apply(clean_ticker)
    signals = signals.dropna(subset=["ticker"])
    print(f"Dropped {before_clean - len(signals)} signals with non-standard ticker symbols "
          f"(SPAC units, exchange prefixes, preferred-share notation, etc.)")

    # Collapse to one signal per (ticker, filing_date): a single buy is often
    # reported as several line items (multiple lots, multiple insiders on the
    # same accession, separate same-day filings) - treating each line as an
    # independent observation would pseudo-replicate one real-world event and
    # invalidate the significance test below.
    before = len(signals)
    signals = signals.groupby(["ticker", "filing_date"], as_index=False).agg(
        issuer=("issuer", "first"),
        insider=("insider", lambda s: s.iloc[0] if s.nunique() == 1 else f"{s.nunique()} insiders"),
        n_insiders=("insider", "nunique"),
        title=("title", "first"),
        total_value=("total_value", "sum"),
    )
    print(f"Collapsed {before} filing line-items into {len(signals)} (ticker, day) signals")

    signals = signals.sort_values("filing_date").reset_index(drop=True)
    print(f"Total qualifying signals: {len(signals)}")

    os.makedirs(OUT_DIR, exist_ok=True)
    signals.to_csv(os.path.join(OUT_DIR, "signals_raw.csv"), index=False)

    tickers = sorted(signals["ticker"].unique().tolist())
    print(f"Unique tickers: {len(tickers)}")

    max_horizon_calendar_buffer = 200  # trading days ~90 max horizon, buffer for weekends/holidays
    bars_start = (signals["filing_date"].min() - timedelta(days=10)).strftime("%Y-%m-%d")
    bars_end = (signals["filing_date"].max() + timedelta(days=max_horizon_calendar_buffer)).strftime("%Y-%m-%d")
    bars_end = min(bars_end, datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    print(f"Fetching benchmark ({BENCHMARK}) bars first, in isolation...")
    bench_raw = fetch_bars([BENCHMARK], bars_start, bars_end, key_id, secret)
    if BENCHMARK not in bench_raw or not bench_raw[BENCHMARK]:
        print(f"ERROR: could not fetch benchmark {BENCHMARK} bars. Aborting.")
        sys.exit(1)
    bench_series = bars_to_series(bench_raw[BENCHMARK])
    print(f"Got {len(bench_series)} benchmark bars.")

    print(f"Fetching price bars {bars_start} to {bars_end} for {len(tickers)} tickers...")
    bars_raw = fetch_bars(tickers, bars_start, bars_end, key_id, secret)

    price_series = {sym: bars_to_series(bl) for sym, bl in bars_raw.items() if bl}
    print(f"Got price history for {len(price_series)}/{len(tickers)} tickers.")

    rows = []
    for _, sig in signals.iterrows():
        sym = sig["ticker"]
        if sym not in price_series:
            continue
        pxs = price_series[sym]
        row = dict(sig)
        for h in HORIZONS:
            ret, entry_d, exit_d = forward_return(pxs, sig["filing_date"], h)
            bench_ret = None
            if ret is not None:
                bench_ret, _, _ = forward_return(bench_series, entry_d, h)
                if bench_ret is None:
                    ret = None
            row[f"ret_{h}d"] = ret
            row[f"bench_ret_{h}d"] = bench_ret
            row[f"excess_ret_{h}d"] = (ret - bench_ret) if (ret is not None and bench_ret is not None) else None
        rows.append(row)

    results = pd.DataFrame(rows)
    results.to_csv(os.path.join(OUT_DIR, "signals_with_returns.csv"), index=False)
    print(f"Signals with usable price data: {len(results)}")

    write_report(signals, results, f"{quarters[0]} - {quarters[-1]}")


def write_report(signals, results, window_label):
    cutoff = signals["filing_date"].quantile(0.7)
    print(f"\nIn-sample: filing_date < {cutoff.date()}  |  Out-of-sample: filing_date >= {cutoff.date()}\n")

    report_lines = []
    report_lines.append(f"# ZycaAlgo insider-buy signal backtest\n")
    report_lines.append(f"Window: {window_label}  |  Signals: {len(signals)}  |  With price data: {len(results)}\n")
    report_lines.append(f"In-sample cutoff (70th percentile by filing date): {cutoff.date()}\n")
    report_lines.append(
        "\nBoth a paired t-test (on the mean) and a Wilcoxon signed-rank test "
        "(on the median, distribution-free - doesn't assume symmetric returns) "
        "are reported. Where they disagree, treat the Wilcoxon result as the "
        "more conservative read, since the return distributions here are "
        "right-skewed (a handful of large winners pull the mean above the "
        "median at every horizon).\n"
    )

    for h in HORIZONS:
        report_lines.append(f"\n## {h}-trading-day horizon\n")
        report_lines.append(
            "| Period | n | mean excess ret | median excess ret | hit rate | "
            "t-stat | t-test p | Wilcoxon p |"
        )
        report_lines.append("|---|---|---|---|---|---|---|---|")
        for label, sub in [
            ("Full sample", results),
            ("In-sample", results[results["filing_date"] < cutoff]),
            ("Out-of-sample", results[results["filing_date"] >= cutoff]),
        ]:
            col = f"excess_ret_{h}d"
            vals = sub[col].dropna()
            if len(vals) < 2:
                report_lines.append(f"| {label} | {len(vals)} | - | - | - | - | - | - |")
                continue
            mean = vals.mean()
            median = vals.median()
            hit = (vals > 0).mean()
            tstat, tpval = stats.ttest_1samp(vals, 0)
            try:
                _, wpval = stats.wilcoxon(vals)
            except ValueError:
                wpval = float("nan")
            report_lines.append(
                f"| {label} | {len(vals)} | {mean:+.2%} | {median:+.2%} | {hit:.1%} | "
                f"{tstat:.2f} | {tpval:.4f} | {wpval:.4f} |"
            )

    report = "\n".join(report_lines) + "\n"
    report_path = os.path.join(OUT_DIR, "backtest_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nWrote {report_path}")
    print(report)


if __name__ == "__main__":
    main()
