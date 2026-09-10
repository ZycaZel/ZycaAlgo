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
    """tickers: list of symbols. Returns a dict per ticker with sector, price,
    change_pct, and the analyst figures Yahoo publishes alongside them:
    target_mean/high/low, analyst_count and recommendation.

    Those analyst fields matter for a specific reason. ZycaAlgo has no
    valuation model - it screens filings and exits on price rules, and never
    forms a view on what a share is worth. So a "price target" it invented
    would be fabricated. The analyst consensus is a real, sourced, third-party
    number, which is why it can be written into a research database honestly
    as long as it is labelled as theirs and not ours.

    Best-effort: a ticker Yahoo doesn't recognize (or a transient failure)
    just gets an empty entry rather than raising, so one bad symbol doesn't
    stop the whole daily sync."""
    session, crumb = _session_with_crumb()
    out = {}
    for ticker in tickers:
        entry = {"sector": None, "price": None, "change_pct": None,
                 "target_mean": None, "target_high": None, "target_low": None,
                 "analyst_count": None, "recommendation": None}
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
                    fin = data.get("financialData", {})
                    price = fin.get("currentPrice", {})
                    entry["price"] = price.get("raw")
                    change_pct = data.get("price", {}).get("regularMarketChangePercent", {})
                    if "raw" in change_pct:
                        entry["change_pct"] = change_pct["raw"] * 100

                    # Analyst consensus, straight from Yahoo. Every one of
                    # these is somebody else's opinion, not ZycaAlgo's.
                    for key, field in (("target_mean", "targetMeanPrice"),
                                       ("target_high", "targetHighPrice"),
                                       ("target_low", "targetLowPrice"),
                                       ("analyst_count", "numberOfAnalystOpinions")):
                        val = fin.get(field)
                        if isinstance(val, dict):
                            entry[key] = val.get("raw")
                        elif isinstance(val, (int, float)):
                            entry[key] = val
                    rec = fin.get("recommendationKey")
                    if isinstance(rec, str) and rec and rec != "none":
                        entry["recommendation"] = rec.replace("_", " ").title()
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            print(f"  [warn] Yahoo Finance lookup failed for {ticker}: {e}")
        out[ticker] = entry
    return out
