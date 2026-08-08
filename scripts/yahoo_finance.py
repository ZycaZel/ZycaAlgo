"""
Minimal client for Yahoo Finance's unofficial quoteSummary endpoint.
No API key, but requires a session cookie + crumb token (Yahoo's
anti-scraping measure) obtained fresh each run.
"""

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Yahoo's broad sector names -> the fixed option set already used in the
# Notion watchlist's Sector select property. Falls back to Yahoo's own
# name (Notion auto-creates a new select option) if there's no mapping.
SECTOR_MAP = {
    "Technology": "Technology",
    "Financial Services": "Financials",
    "Healthcare": "Healthcare",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Basic Materials": "Materials",
    "Communication Services": "Communication Services",
}


def _session_with_crumb():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://fc.yahoo.com", timeout=15)
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).text
    return s, crumb


def get_fundamentals(tickers):
    """tickers: list of symbols. Returns {ticker: {'sector', 'price', 'change_pct'}},
    each value None if unavailable. Best-effort: a ticker Yahoo doesn't
    recognize (or a transient failure) just gets an empty entry rather than
    raising, so one bad symbol doesn't stop the whole daily sync."""
    session, crumb = _session_with_crumb()
    out = {}
    for ticker in tickers:
        entry = {"sector": None, "price": None, "change_pct": None}
        try:
            r = session.get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                params={"modules": "summaryProfile,financialData,price", "crumb": crumb},
                timeout=15,
            )
            if r.status_code == 200:
                result = r.json().get("quoteSummary", {}).get("result")
                if result:
                    data = result[0]
                    raw_sector = data.get("summaryProfile", {}).get("sector")
                    if raw_sector:
                        entry["sector"] = SECTOR_MAP.get(raw_sector, raw_sector)
                    price = data.get("financialData", {}).get("currentPrice", {})
                    entry["price"] = price.get("raw")
                    change_pct = data.get("price", {}).get("regularMarketChangePercent", {})
                    if "raw" in change_pct:
                        entry["change_pct"] = change_pct["raw"] * 100
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            print(f"  [warn] Yahoo Finance lookup failed for {ticker}: {e}")
        out[ticker] = entry
    return out
