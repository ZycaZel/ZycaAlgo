"""
Emails each opted-in trader the signals that hit their own watchlist.

Only sends to users who set email_alerts = true on /account. That column
defaults to false on purpose: adding a ticker to a watchlist is not the
same as asking to be emailed about it. A user with alerts on but no
matching signals today gets nothing - no "no signals" mail.

Requires these GitHub Actions secrets:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY   (server-side only)
    RESEND_API_KEY              (https://resend.com - free tier)
    ALERT_FROM_EMAIL            (a verified sender on that Resend account)

Missing any of them makes this a no-op, like the other optional
integrations - the core scanner and paper trading never depend on it.
"""

import json
import os
import sys

import requests

RESEND_ENDPOINT = "https://api.resend.com/emails"


def fetch_opted_in_users(supabase_url, service_key):
    """Users with alerts on, each with the tickers they follow."""
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    r = requests.get(
        f"{supabase_url}/rest/v1/profiles",
        params={"email_alerts": "eq.true", "select": "id,email"},
        headers=headers, timeout=20,
    )
    r.raise_for_status()
    profiles = {p["id"]: p["email"] for p in r.json() if p.get("email")}
    if not profiles:
        return []

    r = requests.get(
        f"{supabase_url}/rest/v1/watchlist_items",
        params={"select": "user_id,ticker"},
        headers=headers, timeout=20,
    )
    r.raise_for_status()

    watchlists = {}
    for item in r.json():
        if item["user_id"] in profiles:
            watchlists.setdefault(item["user_id"], []).append(item["ticker"])

    return [
        {"id": uid, "email": email, "tickers": watchlists[uid]}
        for uid, email in profiles.items()
        if watchlists.get(uid)
    ]


def build_email(matches, cluster_tickers, scan_date):
    """Plain-text and HTML bodies for one user's matching signals."""
    lines, rows = [], []
    for buy in matches:
        t = buy.get("ticker", "")
        cluster = " (cluster buy - 2+ insiders)" if t in cluster_tickers else ""
        insider = buy.get("insider") or "-"
        title = buy.get("title") or "-"
        try:
            total = f"${float(buy.get('total') or 0):,.0f}"
        except (TypeError, ValueError):
            total = "-"
        lines.append(f"  {t}{cluster} - {insider} ({title}), {total}")
        rows.append(
            f"<tr><td style='padding:6px 12px 6px 0'><strong>{t}</strong>{cluster}</td>"
            f"<td style='padding:6px 12px 6px 0'>{insider}</td>"
            f"<td style='padding:6px 12px 6px 0'>{title}</td>"
            f"<td style='padding:6px 0;text-align:right'>{total}</td></tr>"
        )

    plural = "s" if len(matches) != 1 else ""
    text = (
        f"{len(matches)} ticker{plural} on your ZycaAlgo watchlist had an "
        f"insider buy in the {scan_date} scan:\n\n" + "\n".join(lines) +
        "\n\nSee the full scan: https://zyca-algo.vercel.app/\n"
        "Turn these off any time from your account page.\n\n"
        "ZycaAlgo is a research project, not investment advice. The site's own "
        "trading is simulated (paper) money."
    )
    html = (
        f"<div style=\"font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        f"max-width:560px;color:#111\">"
        f"<h2 style='margin:0 0 4px'>Insider buys on your watchlist</h2>"
        f"<p style='margin:0 0 18px;color:#555'>{len(matches)} ticker{plural} you follow "
        f"had a qualifying insider buy in the {scan_date} scan.</p>"
        f"<table style='border-collapse:collapse;font-size:14px;width:100%'>"
        f"{''.join(rows)}</table>"
        f"<p style='margin:20px 0 0'><a href='https://zyca-algo.vercel.app/'>See the full scan</a></p>"
        f"<p style='margin:16px 0 0;font-size:12px;color:#777'>You're getting this because "
        f"you turned on email alerts in your ZycaAlgo account. You can turn them off there "
        f"any time.<br />ZycaAlgo is a research project, not investment advice - the site's "
        f"own trading is simulated (paper) money.</p></div>"
    )
    return text, html


def main():
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    resend_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("ALERT_FROM_EMAIL")

    if not (supabase_url and service_key and resend_key and from_email):
        print("Email alerts not configured (missing Supabase/Resend secrets) - skipping.")
        return 0

    signals_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "latest.json"
    )
    with open(signals_path, encoding="utf-8") as f:
        scan = json.load(f)

    qualifying = scan.get("qualifying_buys", [])
    cluster_tickers = set(scan.get("cluster_tickers", {}))
    scan_date = scan.get("date", "latest")
    if not qualifying:
        print("No qualifying buys today - no alerts to send.")
        return 0

    by_ticker = {}
    for buy in qualifying:
        if buy.get("ticker"):
            by_ticker.setdefault(buy["ticker"], []).append(buy)

    users = fetch_opted_in_users(supabase_url, service_key)
    print(f"{len(users)} user(s) opted in with a non-empty watchlist.")

    sent = 0
    for user in users:
        matches = []
        for ticker in user["tickers"]:
            matches.extend(by_ticker.get(ticker, []))
        if not matches:
            continue

        text, html = build_email(matches, cluster_tickers, scan_date)
        subject = (
            f"ZycaAlgo: insider buy on {matches[0]['ticker']}"
            if len(matches) == 1
            else f"ZycaAlgo: insider buys on {len(matches)} of your tickers"
        )
        try:
            r = requests.post(
                RESEND_ENDPOINT,
                headers={"Authorization": f"Bearer {resend_key}"},
                json={"from": from_email, "to": [user["email"]],
                      "subject": subject, "text": text, "html": html},
                timeout=20,
            )
            if r.status_code >= 400:
                # One bad address must not stop everyone else's mail.
                print(f"  [warn] {user['email']}: {r.status_code} {r.text[:200]}")
                continue
            sent += 1
            print(f"  sent to {user['email']} ({len(matches)} match(es))")
        except Exception as e:
            print(f"  [warn] {user['email']}: {e}")

    print(f"Done - {sent} email(s) sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
