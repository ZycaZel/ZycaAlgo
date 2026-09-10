"""Pushes today's qualifying insider-buy signals (data/latest.json) into the
user's Notion Stock Watchlist database. Run after insider_buys.py.

If NOTION_TOKEN isn't set, this is a no-op (the Notion integration is
optional - the scan/trading pipeline must work without it).
"""

import json
import os
import sys

import notion_client as nc
import yahoo_finance


def main():
    if not os.environ.get("NOTION_TOKEN"):
        print("NOTION_TOKEN not set - skipping Notion sync.")
        return

    report_path = sys.argv[1] if len(sys.argv) > 1 else "../data/latest.json"
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    buys = report.get("qualifying_buys", [])
    print(f"Pushing {len(buys)} signal(s) from {report.get('date')} to Notion...")

    tickers = sorted({b["ticker"] for b in buys})
    try:
        fundamentals = yahoo_finance.get_fundamentals(tickers)
    except Exception as e:
        # Fundamentals are a nice-to-have on top of the core signal sync -
        # if Yahoo Finance is unreachable/blocked entirely, still push the
        # signals themselves rather than failing the whole sync.
        print(f"  [warn] Yahoo Finance fundamentals unavailable: {e}")
        fundamentals = {}

    # Which tickers had two or more different insiders buying inside the
    # scanner's 30-day window. This is the stronger of the two dimensions the
    # ablation study found actually separates returns, so it feeds both the
    # Cluster Buy column and the conviction grade.
    clusters = report.get("cluster_tickers", {}) or {}

    for buy in buys:
        ticker = buy["ticker"]
        insiders = clusters.get(ticker) or []
        signal = dict(buy)
        signal["is_cluster"] = bool(insiders)
        signal["insider_count"] = len(insiders) if insiders else 1

        f = fundamentals.get(ticker) or {}
        note = (
            f"Insider buy detected (filed {buy['date_filed']}): "
            f"{buy['insider']} ({buy['title']}) bought "
            f"${buy['total']:,.0f} ({buy['shares']:,.0f} sh @ ${buy['price']:.2f}) "
            f"on {buy['txn_date']}."
        )
        if signal["is_cluster"]:
            note += (f" Cluster buy: {signal['insider_count']} different insiders "
                     f"bought within 30 days ({', '.join(insiders[:4])}).")
        note += f" Signal strength: {nc.conviction_from_signal(signal)}."
        if f.get("target_mean"):
            # Attribute the target explicitly. ZycaAlgo does not value
            # companies, and a number in a research database should never be
            # mistaken for its opinion.
            note += (f" Analyst consensus target ${f['target_mean']:,.2f}"
                     + (f" across {f['analyst_count']} analysts" if f.get("analyst_count") else "")
                     + " (Yahoo Finance, not a ZycaAlgo estimate).")

        try:
            nc.upsert_signal(
                ticker, buy["company"], note,
                filing_url=buy["filing_url"],
                fundamentals=f,
                signal=signal,
            )
            print(f"  {ticker}: ok")
        except Exception as e:
            # Notion sync is a nice-to-have, not a critical trading step -
            # one bad row (e.g. an unexpected ticker/property edge case)
            # shouldn't take down the rest of the push.
            print(f"  [warn] {buy['ticker']}: {e}")


if __name__ == "__main__":
    main()
