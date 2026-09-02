"""
One-off repair: recover exits that were closed before exits were logged.

manage_exits() used to delete a vanished position from state without
writing anything, so any position closed by a filled stop left no record.
This finds those gaps - tickers with an ENTRY, no EXIT, and no open
position - and reconstructs the missing row from Alpaca's order history.

Defaults to a dry run. Nothing is written without --apply, because this
edits the trade log that the site and the report both read from.

    python backfill_exits.py            # show what's missing, change nothing
    python backfill_exits.py --apply    # write the rows

Needs APCA_API_KEY_ID / APCA_API_SECRET_KEY, same as trade_manager.
"""

import json
import os
import sys
from datetime import datetime, timezone

import trade_manager as tm

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
TRADES_JSON = os.path.join(DATA_DIR, "trades.json")
TRADES_MD = os.path.join(DATA_DIR, "..", "trades.md")
STATE = os.path.join(DATA_DIR, "positions_state.json")


def load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_missing():
    """Tickers that were entered, are no longer held, and have no exit row."""
    trades = load(TRADES_JSON, [])
    state = load(STATE, {})

    entered, exited, meta = set(), set(), {}
    for row in trades:
        event = row.get("event", "")
        ticker = row.get("ticker")
        if event == "ENTRY":
            entered.add(ticker)
            meta[ticker] = row
        elif event.startswith("EXIT") or event.startswith("TIME STOP"):
            exited.add(ticker)

    return sorted(entered - exited - set(state)), meta, trades


def closing_sell(ticker):
    """The filled sell order that closed this position, if Alpaca still has it."""
    try:
        orders = tm._get(tm.TRADING_BASE, "/v2/orders", {
            "status": "closed", "symbols": ticker, "limit": 500, "direction": "desc",
        })
    except RuntimeError as e:
        print(f"  [warn] {ticker}: could not read orders ({e})")
        return None

    for o in orders:
        if o.get("side") == "sell" and o.get("filled_at") and o.get("filled_avg_price"):
            return o
    return None


def build_row(ticker, entry_row, order):
    """An exit row in the same shape append_trade_log writes."""
    price = float(order["filled_avg_price"])
    date = order["filled_at"][:10]

    entry_price = None
    raw = (entry_row or {}).get("entry", "")
    try:
        entry_price = float(str(raw).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        entry_price = None

    if entry_price:
        gain = (price - entry_price) / entry_price
        event = f"EXIT - stop filled at ${price:,.2f} ({gain:+.1%})"
    else:
        event = f"EXIT - closed at ${price:,.2f}"

    return {
        "timestamp": order["filled_at"],
        "date": date,
        "ticker": ticker,
        "insider": (entry_row or {}).get("insider", ""),
        "entry": (entry_row or {}).get("entry", ""),
        "size": (entry_row or {}).get("size", ""),
        "stop": f"${price:,.2f}",
        "event": event,
        "filing_url": (entry_row or {}).get("filing_url", ""),
        "backfilled": True,   # so this is never mistaken for a live log line
    }


def md_line(row):
    return (f"| {row['date']} | {row['ticker']} | {row['insider']} | {row['entry']} | "
            f"{row['size']} | {row['stop']} | {row['event']} | {row['filing_url']} |\n")


def insert_in_date_order(trades, new_rows):
    """Both logs read oldest-first, and the site reverses them for display -
    so a recovered exit has to go where it happened, not on the end."""
    merged = list(trades)
    for row in new_rows:
        pos = len(merged)
        for i, existing in enumerate(merged):
            if existing.get("date", "") > row["date"]:
                pos = i
                break
        merged.insert(pos, row)
    return merged


def main():
    apply = "--apply" in sys.argv
    missing, meta, trades = find_missing()

    if not missing:
        print("No missing exits - every entered position is either still open or already logged.")
        return 0

    print(f"Positions entered, no longer held, and never logged an exit: {', '.join(missing)}\n")

    new_rows = []
    for ticker in missing:
        order = closing_sell(ticker)
        if not order:
            print(f"  {ticker}: no filled sell order found in Alpaca's history - skipping "
                  f"(it may have aged out; nothing can be reconstructed for it)")
            continue
        row = build_row(ticker, meta.get(ticker), order)
        new_rows.append(row)
        print(f"  {ticker}: {row['date']}  {row['event']}")

    if not new_rows:
        print("\nNothing could be reconstructed.")
        return 0

    if not apply:
        print(f"\nDry run - nothing written. Re-run with --apply to add "
              f"{len(new_rows)} row(s) to trades.md and data/trades.json.")
        return 0

    merged = insert_in_date_order(trades, new_rows)
    with open(TRADES_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    # Rebuild trades.md from the merged list so the two stay row-for-row
    # identical - they were in sync before this and must stay that way.
    with open(TRADES_MD, "w", encoding="utf-8") as f:
        f.write("# ZycaAlgo Trade Log\n\n")
        f.write("| Date | Ticker | Insider | Entry | Size | Stop | Event | Filing |\n")
        f.write("|---|---|---|---:|---:|---:|---|---|\n")
        for row in merged:
            f.write(md_line(row))

    print(f"\nWrote {len(new_rows)} recovered exit(s). "
          f"trades.json and trades.md now hold {len(merged)} rows each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
