"""
Runs the paper-trading side of ZycaAlgo: applies the liquidity filter,
sizes and places entries, manages exits (flat stop -> trailing stop ->
time stop), and logs everything to trades.md.

This talks to Alpaca's REST API directly (no MCP/Claude tool access is
available in a GitHub Actions runner), and to SEC's XBRL companyfacts API
for shares-outstanding (to compute market cap). Requires these environment
variables to be set (as GitHub Actions secrets):

    APCA_API_KEY_ID
    APCA_API_SECRET_KEY

Always talks to the PAPER endpoint (https://paper-api.alpaca.markets).
This script must never be pointed at a live trading endpoint.

Usage:
    python trade_manager.py enter    # read today's signals.json, place new entries
    python trade_manager.py manage   # check open positions for stop/trailing/time exits
    python trade_manager.py summary  # print the weekly Friday summary
"""

import os
import sys
import json
import math
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

TRADING_BASE = "https://paper-api.alpaca.markets"  # PAPER ONLY. Never change.
DATA_BASE = "https://data.alpaca.markets"

API_KEY = os.environ.get("APCA_API_KEY_ID")
API_SECRET = os.environ.get("APCA_API_SECRET_KEY")

POSITION_PCT_OF_EQUITY = 0.02
MAX_OPEN_POSITIONS = 15
MIN_MARKET_CAP = 1_000_000_000
MIN_AVG_DAILY_VOLUME = 500_000
STOP_LOSS_PCT = 0.15          # flat stop at entry
TRAIL_TRIGGER_PCT = 0.15      # once up this much, switch to trailing
TRAIL_PCT = 0.10              # trail this far below the high-water mark
TIME_STOP_DAYS = 90
TIME_STOP_BAND = 0.03         # "flat" = within +/-3% after 90 days

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
STATE_PATH = os.path.join(DATA_DIR, "positions_state.json")
TRADES_MD_PATH = os.path.join(DATA_DIR, "..", "trades.md")
TRADES_JSON_PATH = os.path.join(DATA_DIR, "trades.json")


def _headers():
    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set. This script "
            "only ever talks to the PAPER endpoint, but it still needs your "
            "paper account's key pair to authenticate."
        )
    return {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}


def _get(base, path, params=None):
    r = requests.get(f"{base}{path}", headers=_headers(), params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _post(base, path, body):
    r = requests.post(f"{base}{path}", headers=_headers(), json=body, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} failed [{r.status_code}]: {r.text}")
    return r.json()


def _delete(base, path, params=None):
    r = requests.delete(f"{base}{path}", headers=_headers(), params=params, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"DELETE {path} failed [{r.status_code}]: {r.text}")
    return r.json() if r.text else {}


# ---------------------------------------------------------------------------
# STATE (persisted in data/positions_state.json, committed back to the repo
# by the GitHub Actions workflow after every run)
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def append_trade_log(row):
    os.makedirs(os.path.dirname(TRADES_MD_PATH), exist_ok=True)
    is_new = not os.path.exists(TRADES_MD_PATH)
    with open(TRADES_MD_PATH, "a", encoding="utf-8") as f:
        if is_new:
            f.write("# ZycaAlgo Trade Log\n\n")
            f.write("| Date | Ticker | Insider | Entry | Size | Stop | Event | Filing |\n")
            f.write("|---|---|---|---:|---:|---:|---|---|\n")
        f.write(
            f"| {row['date']} | {row['ticker']} | {row['insider']} | "
            f"{row.get('entry', '')} | {row.get('size', '')} | {row.get('stop', '')} | "
            f"{row['event']} | {row.get('filing_url', '')} |\n"
        )

    # Structured twin for the live activity feed on the website - append-only,
    # newest last (the frontend reverses it for display).
    os.makedirs(DATA_DIR, exist_ok=True)
    entries = []
    if os.path.exists(TRADES_JSON_PATH):
        with open(TRADES_JSON_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f)
    entries.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": row["date"],
        "ticker": row["ticker"],
        "insider": row["insider"],
        "entry": row.get("entry", ""),
        "size": row.get("size", ""),
        "stop": row.get("stop", ""),
        "event": row["event"],
        "filing_url": row.get("filing_url", ""),
    })
    with open(TRADES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


# ---------------------------------------------------------------------------
# MARKET CAP / LIQUIDITY FILTER
# ---------------------------------------------------------------------------

SEC_USER_AGENT = "ZycaAlgo trade_manager (contact via repo owner)"


def shares_outstanding(cik):
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    r = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=20)
    if r.status_code != 200:
        return None
    data = r.json()
    tag = data.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not tag:
        return None
    units = tag["units"]["shares"]
    return sorted(units, key=lambda x: x["end"])[-1]["val"]


def latest_price(symbol):
    data = _get(DATA_BASE, "/v2/stocks/trades/latest", {"symbols": symbol})
    trade = data.get("trades", {}).get(symbol)
    return trade["p"] if trade else None


def avg_daily_volume(symbol, days=21):
    # The raw bars endpoint (unlike a couple of higher-level SDK wrappers)
    # does NOT default to "last N days" from `limit` alone - it needs an
    # explicit `start`, or it silently returns zero bars.
    start = (datetime.now(timezone.utc) - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    data = _get(
        DATA_BASE, "/v2/stocks/bars",
        {"symbols": symbol, "timeframe": "1Day", "start": start, "limit": days},
    )
    bars = data.get("bars", {}).get(symbol, [])
    if not bars:
        return None
    return sum(b["v"] for b in bars) / len(bars)


def passes_liquidity_filter(ticker, cik):
    price = latest_price(ticker)
    adv = avg_daily_volume(ticker)
    so = shares_outstanding(cik) if cik else None
    market_cap = so * price if (so and price) else None

    reasons = []
    if market_cap is None:
        reasons.append("market cap unknown (no shares-outstanding data)")
    elif market_cap < MIN_MARKET_CAP:
        reasons.append(f"market cap ${market_cap:,.0f} < $1B")
    if adv is None:
        reasons.append("average volume unknown")
    elif adv < MIN_AVG_DAILY_VOLUME:
        reasons.append(f"avg volume {adv:,.0f} < 500K")

    return {
        "passes": not reasons,
        "reasons": reasons,
        "price": price,
        "market_cap": market_cap,
        "adv": adv,
    }


# ---------------------------------------------------------------------------
# ENTRIES
# ---------------------------------------------------------------------------

def get_account():
    return _get(TRADING_BASE, "/v2/account")


def get_positions():
    return _get(TRADING_BASE, "/v2/positions")


def enter_new_signals(signals_path):
    with open(signals_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    buys = payload.get("qualifying_buys", [])
    date = payload.get("date", datetime.now().strftime("%Y-%m-%d"))

    account = get_account()
    equity = float(account["equity"])
    positions = get_positions()
    held_symbols = {p["symbol"] for p in positions}
    open_count = len(positions)

    state = load_state()
    per_ticker = {}
    for b in buys:
        per_ticker.setdefault(b["ticker"], b)  # first occurrence wins per ticker

    results = []
    for ticker, b in per_ticker.items():
        if ticker in held_symbols:
            results.append({"ticker": ticker, "action": "skip", "reason": "already held"})
            continue
        if open_count >= MAX_OPEN_POSITIONS:
            results.append({"ticker": ticker, "action": "skip", "reason": "at 15-position cap"})
            append_trade_log({
                "date": date, "ticker": ticker, "insider": b["insider"],
                "event": "SKIPPED - at 15-position cap", "filing_url": b["filing_url"],
            })
            continue

        liq = passes_liquidity_filter(ticker, b.get("cik"))
        if not liq["passes"]:
            reason = "; ".join(liq["reasons"])
            results.append({"ticker": ticker, "action": "skip", "reason": reason})
            append_trade_log({
                "date": date, "ticker": ticker, "insider": b["insider"],
                "event": f"SKIPPED - {reason}", "filing_url": b["filing_url"],
            })
            continue

        price = liq["price"]
        dollar_size = equity * POSITION_PCT_OF_EQUITY
        qty = math.floor(dollar_size / price)
        if qty < 1:
            results.append({"ticker": ticker, "action": "skip", "reason": "too expensive for 2% allocation"})
            continue

        order = _post(TRADING_BASE, "/v2/orders", {
            "symbol": ticker,
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "client_order_id": f"za-{ticker}-{date}",
        })

        stop_price = round(price * (1 - STOP_LOSS_PCT), 2)
        state[ticker] = {
            "entry_date": date,
            "entry_price": price,
            "qty": qty,
            "mode": "initial_stop",
            "stop_price": stop_price,
            "high_water_mark": price,
            "insider": b["insider"],
            "filing_url": b["filing_url"],
            "buy_order_id": order.get("id"),
            "stop_order_id": None,  # placed once the buy fills, see manage_exits()
        }
        open_count += 1

        results.append({"ticker": ticker, "action": "bought", "qty": qty, "price": price})
        append_trade_log({
            "date": date, "ticker": ticker, "insider": b["insider"],
            "entry": f"${price:,.2f}", "size": f"{qty} sh (${qty * price:,.0f})",
            "stop": f"${stop_price:,.2f}", "event": "ENTRY",
            "filing_url": b["filing_url"],
        })

    save_state(state)
    return results


# ---------------------------------------------------------------------------
# EXITS: flat stop -> trailing stop after +15% -> 90-day time stop
# ---------------------------------------------------------------------------

def ensure_stop_order(ticker, qty, stop_price, existing_order_id):
    """Places a fresh stop-sell order, canceling any previous one for this
    ticker first. Alpaca doesn't let us just edit a stop order's price down
    through PATCH in every state, so cancel+recreate is the reliable path."""
    if existing_order_id:
        try:
            _delete(TRADING_BASE, f"/v2/orders/{existing_order_id}")
        except RuntimeError:
            pass  # already filled/canceled - fine
    order = _post(TRADING_BASE, "/v2/orders", {
        "symbol": ticker,
        "qty": str(qty),
        "side": "sell",
        "type": "stop",
        "stop_price": str(stop_price),
        "time_in_force": "gtc",
    })
    return order["id"]


def order_status(order_id):
    """Returns an order's current status string, or None if it can't be found."""
    try:
        return _get(TRADING_BASE, f"/v2/orders/{order_id}").get("status")
    except RuntimeError:
        return None


STILL_PENDING_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "held"}


def manage_exits():
    state = load_state()
    positions = {p["symbol"]: p for p in get_positions()}
    today = datetime.now(timezone.utc).date()
    changed = False

    for ticker, info in list(state.items()):
        position = positions.get(ticker)
        if position is None:
            # No open position - but that's ALSO what a same-day entry looks
            # like before the market has had a chance to fill it (e.g. the
            # job ran while markets were closed). Only treat this as "closed"
            # if we can confirm the original buy order isn't still pending.
            buy_id = info.get("buy_order_id")
            if buy_id and order_status(buy_id) in STILL_PENDING_STATUSES:
                continue  # entry order hasn't filled yet - leave state alone
            del state[ticker]
            changed = True
            continue

        entry_price = info["entry_price"]
        current_price = float(position["current_price"])
        qty = int(float(position["qty"]))
        gain_pct = (current_price - entry_price) / entry_price

        # --- time stop: flat after 90 days closes regardless of mode ---
        entry_date = datetime.strptime(info["entry_date"], "%Y-%m-%d").date()
        age_days = (today - entry_date).days
        if age_days >= TIME_STOP_DAYS and -TIME_STOP_BAND <= gain_pct <= TIME_STOP_BAND:
            if info.get("stop_order_id"):
                try:
                    _delete(TRADING_BASE, f"/v2/orders/{info['stop_order_id']}")
                except RuntimeError:
                    pass
            _delete(TRADING_BASE, f"/v2/positions/{ticker}")
            append_trade_log({
                "date": today.isoformat(), "ticker": ticker, "insider": info["insider"],
                "event": f"TIME STOP - flat after {age_days}d ({gain_pct:+.1%})",
                "filing_url": info["filing_url"],
            })
            del state[ticker]
            changed = True
            continue

        # --- first-ever stop placement (buy order has since filled) ---
        if info.get("stop_order_id") is None:
            info["stop_order_id"] = ensure_stop_order(ticker, qty, info["stop_price"], None)
            changed = True

        # --- trailing logic: once +15%, track high-water mark, floor only rises ---
        if gain_pct >= TRAIL_TRIGGER_PCT or info["mode"] == "trailing":
            new_high = max(info["high_water_mark"], current_price)
            new_stop = round(new_high * (1 - TRAIL_PCT), 2)
            if info["mode"] != "trailing" or new_high > info["high_water_mark"] or new_stop > info["stop_price"]:
                info["mode"] = "trailing"
                info["high_water_mark"] = new_high
                if new_stop > info["stop_price"]:
                    info["stop_order_id"] = ensure_stop_order(ticker, qty, new_stop, info["stop_order_id"])
                    info["stop_price"] = new_stop
                changed = True

        state[ticker] = info

    if changed:
        save_state(state)
    return state


# ---------------------------------------------------------------------------
# WEEKLY SUMMARY (run on Fridays)
# ---------------------------------------------------------------------------

def weekly_summary():
    state = load_state()
    positions = {p["symbol"]: p for p in get_positions()}
    account = get_account()

    lines = [f"# ZycaAlgo Weekly Summary - {datetime.now().strftime('%Y-%m-%d')}\n"]
    lines.append(f"Equity: ${float(account['equity']):,.2f} | Open positions: {len(positions)}\n")

    if not positions:
        lines.append("No open positions.\n")
    else:
        lines.append("| Ticker | Insider | Entry | Current | Return | Mode |")
        lines.append("|---|---|---:|---:|---:|---|")
        for ticker, p in positions.items():
            info = state.get(ticker, {})
            entry = info.get("entry_price")
            current = float(p["current_price"])
            ret = (current - entry) / entry if entry else None
            ret_str = f"{ret:+.1%}" if ret is not None else "n/a"
            lines.append(
                f"| {ticker} | {info.get('insider', 'n/a')} | "
                f"${entry:,.2f} | ${current:,.2f} | {ret_str} | {info.get('mode', 'n/a')} |"
            )

    # Win rate / avg return / by-insider performance come from closed trades
    # logged in trades.md (EXIT/STOP/TIME STOP rows) - left as a manual read
    # of that file for now since it's a small, human-scale log.
    lines.append("\nFor closed-trade win rate and per-insider performance, see trades.md.")

    print("\n".join(lines))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "manage"
    if cmd == "enter":
        signals_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA_DIR, "latest.json")
        print(json.dumps(enter_new_signals(signals_path), indent=2))
    elif cmd == "manage":
        print(json.dumps(manage_exits(), indent=2, default=str))
    elif cmd == "summary":
        weekly_summary()
    else:
        print(f"Unknown command: {cmd}. Use enter | manage | summary.")
        sys.exit(1)
