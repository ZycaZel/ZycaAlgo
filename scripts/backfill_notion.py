"""
Fills in every existing row of the Notion Stock Watchlist.

The daily sync only touches tickers that appear in that day's scan, so rows
added earlier keep whatever they had when they were created - which for most
of them is a ticker and a price. This walks the whole database once and fills
the rest.

For each row it writes:
  - live market figures and the analyst consensus (target, high, low, count,
    recommendation) from Yahoo
  - the most recent insider signal ZycaAlgo has on file for that ticker, if
    any: insider, title, size, shares, dates, cluster flag, filing URL, and
    the 1-10 conviction score derived from it

A ticker ZycaAlgo has never flagged - one added by hand - still gets its
market and analyst data. It gets no conviction score, because there is no
signal to grade and a number invented for the column would be fiction.

Anything already filled in is left alone; only live prices and analyst
figures refresh. Dry run unless --apply is passed.

Needs NOTION_TOKEN.
"""

import glob
import json
import os
import sys

import requests

import notion_client as nc
import yahoo_finance

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(SCRIPT_DIR, "..", "data", "archive")


def all_rows():
    """Every page in the watchlist database, following pagination."""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{nc.API}/databases/{nc.WATCHLIST_DB_ID}/query",
                          headers=nc._headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")


def row_ticker(row):
    for prop in (row.get("properties") or {}).values():
        if prop.get("type") == "title":
            parts = prop.get("title") or []
            if parts:
                return "".join(p.get("plain_text", "") for p in parts).strip().upper()
    return None


def latest_signals():
    """The most recent qualifying buy per ticker across every archived scan,
    plus whether that scan flagged it as a cluster."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ARCHIVE, "insider-buys-*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                scan = json.load(f)
        except (OSError, ValueError):
            continue
        clusters = scan.get("cluster_tickers") or {}
        for buy in scan.get("qualifying_buys") or []:
            t = (buy.get("ticker") or "").upper()
            if not t:
                continue
            sig = dict(buy)
            insiders = clusters.get(t) or []
            sig["is_cluster"] = bool(insiders)
            sig["insider_count"] = len(insiders) if insiders else 1
            out[t] = sig      # later files overwrite earlier ones
    return out


def main():
    apply = "--apply" in sys.argv
    if not os.environ.get("NOTION_TOKEN"):
        print("NOTION_TOKEN not set - nothing to do.")
        return 0

    rows = all_rows()
    tickers = [t for t in (row_ticker(r) for r in rows) if t]
    print(f"{len(rows)} rows in the watchlist, {len(tickers)} with a ticker.\n")
    if not tickers:
        return 0

    signals = latest_signals()
    matched = [t for t in tickers if t in signals]
    print(f"{len(matched)} have an insider signal on file; "
          f"{len(tickers) - len(matched)} will get market data only.\n")

    try:
        fundamentals = yahoo_finance.get_fundamentals(sorted(set(tickers)))
    except Exception as e:
        print(f"[warn] Yahoo Finance unavailable ({e}) - continuing without market data.")
        fundamentals = {}

    if not apply:
        print("Dry run - nothing written. Sample of what would be filled:\n")
        for t in tickers[:8]:
            f = fundamentals.get(t) or {}
            s = signals.get(t)
            bits = []
            if f.get("price"):
                bits.append(f"price ${f['price']:,.2f}")
            if f.get("target_mean"):
                bits.append(f"target ${f['target_mean']:,.2f}")
            if f.get("recommendation"):
                bits.append(f.get("recommendation"))
            bits.append(f"conviction {nc.conviction_score(s)}/10" if s else "no signal on file")
            print(f"  {t:<7} {', '.join(bits)}")
        print(f"\nRe-run with --apply to write all {len(tickers)} rows.")
        return 0

    written = failed = 0
    for row, ticker in zip(rows, (row_ticker(r) for r in rows)):
        if not ticker:
            continue
        try:
            # note_text=None so this doesn't append a callout to every page -
            # the daily sync already logs the signals as they arrive, and a
            # backfill shouldn't spam a year of them into the page body.
            nc.upsert_signal(
                ticker,
                ticker,                       # company only used when creating a row
                None,
                filing_url=(signals.get(ticker) or {}).get("filing_url"),
                fundamentals=fundamentals.get(ticker),
                signal=signals.get(ticker),
            )
            written += 1
            print(f"  {ticker}: ok")
        except Exception as e:
            failed += 1
            print(f"  [warn] {ticker}: {e}")

    print(f"\nUpdated {written} row(s), {failed} failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
