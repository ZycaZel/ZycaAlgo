"""Pushes today's qualifying insider-buy signals (data/latest.json) into the
user's Notion Stock Watchlist database. Run after insider_buys.py.

If NOTION_TOKEN isn't set, this is a no-op (the Notion integration is
optional - the scan/trading pipeline must work without it).
"""

import json
import os
import sys

import notion_client as nc


def main():
    if not os.environ.get("NOTION_TOKEN"):
        print("NOTION_TOKEN not set - skipping Notion sync.")
        return

    report_path = sys.argv[1] if len(sys.argv) > 1 else "../data/latest.json"
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    buys = report.get("qualifying_buys", [])
    print(f"Pushing {len(buys)} signal(s) from {report.get('date')} to Notion...")
    for buy in buys:
        note = (
            f"Insider buy detected (filed {buy['date_filed']}): "
            f"{buy['insider']} ({buy['title']}) bought "
            f"${buy['total']:,.0f} ({buy['shares']:,.0f} sh @ ${buy['price']:.2f}) "
            f"on {buy['txn_date']}."
        )
        try:
            nc.upsert_signal(buy["ticker"], buy["company"], note, filing_url=buy["filing_url"])
            print(f"  {buy['ticker']}: ok")
        except Exception as e:
            # Notion sync is a nice-to-have, not a critical trading step -
            # one bad row (e.g. an unexpected ticker/property edge case)
            # shouldn't take down the rest of the push.
            print(f"  [warn] {buy['ticker']}: {e}")


if __name__ == "__main__":
    main()
