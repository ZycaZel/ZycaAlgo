"""Aggregates every archived daily scan (data/archive/*.json) into a single
deduped watchlist of every ticker ever flagged by a qualifying insider buy,
enriched with current sector/price from Yahoo Finance. Writes data/watchlist.json
for the public /watchlist page to read directly (no live API calls on page load).
"""

import glob
import json
import os

import yahoo_finance as yf

ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "archive")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "watchlist.json")


def main():
    tickers = {}
    for path in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        for buy in report.get("qualifying_buys", []):
            t = buy["ticker"]
            entry = tickers.setdefault(t, {
                "ticker": t,
                "company": buy["company"],
                "first_seen": buy["date_filed"],
                "last_seen": buy["date_filed"],
                "signal_count": 0,
            })
            entry["company"] = buy["company"]
            entry["first_seen"] = min(entry["first_seen"], buy["date_filed"])
            entry["last_seen"] = max(entry["last_seen"], buy["date_filed"])
            entry["signal_count"] += 1

    print(f"Found {len(tickers)} unique tickers across {len(glob.glob(os.path.join(ARCHIVE_DIR, '*.json')))} archived days.")

    fundamentals = yf.get_fundamentals(sorted(tickers.keys()))
    for t, entry in tickers.items():
        fu = fundamentals.get(t, {})
        entry["sector"] = fu.get("sector")
        entry["price"] = fu.get("price")
        entry["change_pct"] = fu.get("change_pct")

    watchlist = sorted(tickers.values(), key=lambda e: e["last_seen"], reverse=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"tickers": watchlist}, f, indent=2)
    print(f"Wrote {OUT_PATH} ({len(watchlist)} tickers)")


if __name__ == "__main__":
    main()
