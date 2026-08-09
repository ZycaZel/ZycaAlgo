"""Posts today's qualifying insider-buy signals to a Discord channel via
webhook. Cluster buys (2+ different insiders on the same ticker within 30
days) are called out first and separately, since they're the highest-
conviction signals the scanner produces.

If DISCORD_WEBHOOK_URL isn't set, this is a no-op (optional integration,
same pattern as the Notion sync).
"""

import json
import os
import sys

import requests

STAMP_COLOR = 0xFF5240  # matches the site's --stamp accent


def fmt_signal(buy):
    return (
        f"**{buy['ticker']}** — {buy['company']}\n"
        f"{buy['insider']} ({buy['title']}) bought "
        f"${buy['total']:,.0f} ({buy['shares']:,.0f} sh @ ${buy['price']:.2f}) "
        f"on {buy['txn_date']} · [Filing]({buy['filing_url']})"
    )


def build_embeds(report):
    buys = report.get("qualifying_buys", [])
    cluster_tickers = report.get("cluster_tickers", {})
    cluster_buys = [b for b in buys if b["ticker"] in cluster_tickers]
    solo_buys = [b for b in buys if b["ticker"] not in cluster_tickers]

    embeds = []
    if cluster_buys:
        embeds.append({
            "title": f"\U0001F525 Cluster buys ({len(cluster_buys)})",
            "description": "\n\n".join(fmt_signal(b) for b in cluster_buys[:12]),
            "color": STAMP_COLOR,
        })
    if solo_buys:
        embeds.append({
            "title": f"New signals ({len(solo_buys)})",
            "description": "\n\n".join(fmt_signal(b) for b in solo_buys[:12]),
            "color": STAMP_COLOR,
        })
    return embeds


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set - skipping Discord alert.")
        return

    report_path = sys.argv[1] if len(sys.argv) > 1 else "../data/latest.json"
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    buys = report.get("qualifying_buys", [])
    if not buys:
        print("No qualifying signals today - skipping Discord alert.")
        return

    embeds = build_embeds(report)
    payload = {
        "content": f"**ZycaAlgo** — {len(buys)} qualifying signal(s) for {report.get('date')}",
        "embeds": embeds,
    }
    r = requests.post(webhook_url, json=payload, timeout=15)
    if r.status_code == 204:
        print(f"Posted {len(buys)} signal(s) to Discord.")
    else:
        print(f"[warn] Discord webhook returned {r.status_code}: {r.text[:300]}")


if __name__ == "__main__":
    main()
