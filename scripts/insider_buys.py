"""
Finds genuine insider BUYS (Form 4, transaction code "P") from SEC EDGAR.

Plain-English summary of what this script does, step by step:

1. Downloads SEC's list of every filing made on a given day.
2. Keeps only the "Form 4" filings (insider transaction reports).
3. For each Form 4, opens that filing's index page and finds the actual
   data file (it's XML) - the filename varies, so we don't guess it.
4. Reads the XML and pulls out: who the insider is, what company, what
   they bought/sold, how many shares, at what price, and whether the
   trade was a "10b5-1" pre-scheduled plan trade.
5. Throws away everything that ISN'T a genuine, real-time, open-market
   BUY worth at least $100,000 by an officer or director.
6. Checks whether other insiders at the same company also bought in the
   last 30 days (a stronger signal - "cluster buying").
7. Writes a Markdown table of the survivors, sorted by dollar size.

Run it with:
    python insider_buys.py            (uses today's date)
    python insider_buys.py 20260805   (uses a specific date, YYYYMMDD)
"""

import sys
import re
import time
import gzip
import io
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

# SEC requires a real name + email in the User-Agent, or it will block us (403).
USER_AGENT = "Nattawut Chaichanawanich nattawutgorn@gmail.com"

BASE = "https://www.sec.gov"

# SEC's hard limit is 10 requests/second. We stay under that on purpose.
MIN_SECONDS_BETWEEN_REQUESTS = 0.15   # ~6.5 requests/second
MIN_DOLLAR_VALUE = 100_000
CLUSTER_LOOKBACK_DAYS = 30

_last_request_time = [0.0]


# ---------------------------------------------------------------------------
# LOW-LEVEL: fetching pages politely (rate-limited, with retries)
# ---------------------------------------------------------------------------

def fetch(url, tries=5, missing_codes=(404,)):
    """Download a URL as text, respecting SEC's rate limit and retrying
    with backoff if SEC temporarily says no (403/429/5xx).

    `missing_codes` lists HTTP status codes that mean "doesn't exist" rather
    than "temporarily blocked". Normally that's just 404, but SEC's static
    archive (daily-index files) returns 403 for a file that simply hasn't
    been published yet - pass missing_codes=(403, 404) there to avoid
    pointlessly retrying something that will never succeed.
    """
    for attempt in range(1, tries + 1):
        # --- rate limiting: never go faster than our allowed pace ---
        wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - _last_request_time[0])
        if wait > 0:
            time.sleep(wait)

        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            },
        )
        try:
            with urlopen(req, timeout=20) as resp:
                _last_request_time[0] = time.time()
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except HTTPError as e:
            _last_request_time[0] = time.time()
            if e.code in missing_codes:
                return None  # doesn't exist - not an error, just "not found"
            backoff = 2 ** attempt
            print(f"  [warn] HTTP {e.code} on {url} - retrying in {backoff}s "
                  f"(attempt {attempt}/{tries})")
            time.sleep(backoff)
        except URLError as e:
            _last_request_time[0] = time.time()
            backoff = 2 ** attempt
            print(f"  [warn] network error ({e.reason}) on {url} - retrying in "
                  f"{backoff}s (attempt {attempt}/{tries})")
            time.sleep(backoff)
        except (TimeoutError, ConnectionError, OSError) as e:
            # A timeout while reading the response body (as opposed to while
            # connecting) surfaces as a bare TimeoutError/OSError, not a
            # URLError - SEC's slower endpoints (company filing history) hit
            # this occasionally. Without this handler it crashes the whole
            # scan instead of just retrying this one request.
            _last_request_time[0] = time.time()
            backoff = 2 ** attempt
            print(f"  [warn] connection error ({e}) on {url} - retrying in "
                  f"{backoff}s (attempt {attempt}/{tries})")
            time.sleep(backoff)
    print(f"  [error] giving up on {url} after {tries} attempts")
    return None


# ---------------------------------------------------------------------------
# STEP 1: get today's list of every filing, keep only Form 4s
# ---------------------------------------------------------------------------

def quarter_for(date):
    return (date.month - 1) // 3 + 1


def daily_index_url(date):
    return (f"{BASE}/Archives/edgar/daily-index/{date.year}/"
            f"QTR{quarter_for(date)}/form.{date.strftime('%Y%m%d')}.idx")


def parse_form_idx(text):
    """The .idx file is fixed-width text. We find the column boundaries from
    the header row itself, instead of hardcoding character positions, so it
    keeps working if SEC tweaks the layout slightly."""
    lines = text.splitlines()
    header_line_no = next(
        (i for i, l in enumerate(lines) if l.startswith("Form Type")), None
    )
    if header_line_no is None:
        return []

    # SEC sometimes wraps this header across two physical lines (the "CIK"
    # label ends one line and "Date Filed  File Name" starts the next) even
    # though every data row below is a single line, and the wrap can eat a
    # few spaces. The "Company Name" column start is unaffected, so we use
    # the header just for the Form Type / Company Name boundary, and pull
    # the trailing CIK / Date Filed / File Name fields out with a pattern
    # match (CIK = digits, Date Filed = YYYYMMDD, File Name = a path with
    # no spaces) so we don't depend on exact trailing-column offsets.
    header = lines[header_line_no]
    consumed = 1
    while "File Name" not in header and header_line_no + consumed < len(lines):
        header += lines[header_line_no + consumed]
        consumed += 1

    company_start = header.index("Company Name")
    tail_pattern = re.compile(r"^(.*?)\s+(\d+)\s+(\d{8})\s+(\S+)\s*$")

    rows = []
    for line in lines[header_line_no + consumed + 1:]:
        if not line.strip():
            continue
        form_type = line[:company_start].strip()
        m = tail_pattern.match(line[company_start:])
        if not m:
            continue
        company, cik, date_filed, file_name = m.groups()
        rows.append({
            "form_type": form_type,
            "company": company.strip(),
            "cik": cik,
            "date_filed": date_filed,
            "file_name": file_name,
        })
    return rows


def find_most_recent_available_index(date, max_days_back=7):
    """Steps backward from `date` (skipping weekends) until it finds a
    daily-index file SEC has actually published. SEC's archive returns 403
    (not 404) for a date that hasn't been generated yet, e.g. because it's
    still the current trading day and the day hasn't closed out."""
    candidate = date
    for _ in range(max_days_back):
        if candidate.weekday() < 5:  # Mon-Fri only
            url = daily_index_url(candidate)
            text = fetch(url, missing_codes=(403, 404))
            if text:
                return candidate, text
        candidate = candidate - timedelta(days=1)
    return None, None


def get_form4_filings_for_day(date):
    """Returns a list of dicts, one per Form 4 filing, with an index-page URL."""
    print(f"Looking for the most recent published daily index at/before "
          f"{date.strftime('%Y-%m-%d')}...")
    actual_date, text = find_most_recent_available_index(date)

    filings = []
    if text:
        if actual_date.date() != date.date():
            print(f"Note: SEC hasn't published a daily index for "
                  f"{date.strftime('%Y-%m-%d')} yet. Using the most recent "
                  f"available trading day instead: {actual_date.strftime('%Y-%m-%d')}.")
        rows = parse_form_idx(text)
        seen_accessions = set()
        for row in rows:
            if row["form_type"] != "4":
                continue
            accession_dashed = row["file_name"].rsplit("/", 1)[-1].replace(".txt", "")
            # A single Form 4 filing is listed once per associated CIK (the
            # issuer AND each reporting owner), so the same accession number
            # can appear multiple times in the daily index under different
            # CIKs. Keep only the first occurrence or we'd double/triple
            # count the same real-world transaction.
            if accession_dashed in seen_accessions:
                continue
            seen_accessions.add(accession_dashed)
            accession_nodash = accession_dashed.replace("-", "")
            cik_in_path = row["file_name"].split("/")[2]
            index_url = (f"{BASE}/Archives/edgar/data/{cik_in_path}/"
                         f"{accession_nodash}/{accession_dashed}-index.htm")
            filings.append({
                "company": row["company"],
                "cik": cik_in_path,
                "date_filed": f"{row['date_filed'][:4]}-{row['date_filed'][4:6]}-{row['date_filed'][6:]}",
                "index_url": index_url,
            })
        print(f"Daily index available - found {len(filings)} unique Form 4 filings "
              f"(after de-duplicating multi-CIK listings).")
        return filings, actual_date

    # Fallback: no daily index found within the lookback window at all.
    # Use the "latest filings" live feed instead and only keep entries
    # dated the originally requested day.
    print("No recent daily index found - falling back to the live feed.")
    return get_form4_filings_from_live_feed(date), date


def get_form4_filings_from_live_feed(date, max_pages=20):
    filings = []
    target = date.strftime("%Y-%m-%d")
    start = 0
    while start < max_pages * 100:
        url = (f"{BASE}/cgi-bin/browse-edgar?action=getcurrent&type=4&company="
               f"&dateb=&owner=include&count=100&start={start}&output=atom")
        text = fetch(url)
        if not text:
            break
        entries = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)
        if not entries:
            break
        stop = False
        for entry in entries:
            updated_m = re.search(r"<updated>([^<]+)</updated>", entry)
            link_m = re.search(r'<link[^>]*href="([^"]+)"', entry)
            title_m = re.search(r"<title>([^<]*)</title>", entry)
            if not (updated_m and link_m):
                continue
            entry_date = updated_m.group(1)[:10]
            if entry_date != target:
                if entry_date < target:
                    stop = True
                continue
            filings.append({
                "company": (title_m.group(1) if title_m else "").strip(),
                "cik": None,
                "date_filed": entry_date,
                "index_url": link_m.group(1),
            })
        if stop:
            break
        start += 100
    print(f"Live feed - found {len(filings)} Form 4 filings dated {target}.")
    return filings


# ---------------------------------------------------------------------------
# STEP 2: for a given filing, find its XML data file (name varies!) and parse it
# ---------------------------------------------------------------------------

def find_xml_url(index_url):
    """Each filing document is normally listed TWICE on the index page: once
    through a .../xslF345X06/whatever.xml path, which despite the .xml in the
    URL actually serves SEC's server-rendered HTML view of the document, and
    once as a plain top-level whatever.xml, which is the real raw XML data.
    We must pick the latter."""
    html = fetch(index_url)
    if not html:
        return None
    candidates = re.findall(r'href="([^"]+\.xml)"', html, re.IGNORECASE)
    # Drop SEC's XSL-rendered viewer links (path contains an "/xsl.../" segment)
    # and XBRL taxonomy support files if any ever show up.
    candidates = [c for c in candidates
                  if not re.search(r"/xsl[^/]*/", c, re.IGNORECASE)
                  and not re.search(r"(_cal|_def|_lab|_pre)\.xml$", c, re.IGNORECASE)]
    if not candidates:
        return None
    return urljoin(index_url, candidates[0])


def xml_text(el, path):
    if el is None:
        return None
    node = el.find(path)
    if node is None or node.text is None:
        return None
    return node.text.strip()


def xml_bool(el, path):
    """SEC's Form 4 XML is inconsistent about how it encodes booleans - some
    filers' software emits "1"/"0", others emit "true"/"false". Accept both."""
    val = xml_text(el, path)
    return val is not None and val.strip().lower() in ("1", "true")


def parse_form4_xml(xml_str):
    """Pulls every field we care about out of one Form 4 XML document.
    Returns (issuer_info, insider_info, list_of_transactions)."""
    root = ET.fromstring(xml_str)

    issuer_name = xml_text(root, ".//issuer/issuerName")
    ticker = xml_text(root, ".//issuer/issuerTradingSymbol")
    # The .idx file's CIK column can belong to any filer associated with the
    # accession (issuer OR any reporting owner - joint/SPAC filings list
    # several), so it's not reliably the issuer. The XML itself always states
    # the true issuer CIK directly - use that instead for anything that needs
    # to look up the ISSUER (cluster history, market cap, etc).
    issuer_cik = xml_text(root, ".//issuer/issuerCik")

    owner_name = xml_text(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    is_director = xml_bool(rel, "isDirector")
    is_officer = xml_bool(rel, "isOfficer")
    officer_title = xml_text(rel, "officerTitle") if is_officer else None

    title_parts = []
    if officer_title:
        title_parts.append(officer_title)
    elif is_officer:
        title_parts.append("Officer")
    if is_director:
        title_parts.append("Director")
    title = ", ".join(title_parts) if title_parts else "Insider"

    transactions = []
    for txn in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        code = xml_text(txn, ".//transactionCoding/transactionCode")
        shares = xml_text(txn, ".//transactionAmounts/transactionShares/value")
        price = xml_text(txn, ".//transactionAmounts/transactionPricePerShare/value")
        txn_date = xml_text(txn, ".//transactionDate/value")

        plan_node = txn.find(".//aff10b5One")
        if plan_node is None:
            plan_node = root.find(".//aff10b5One")
        plan_value = None
        if plan_node is not None:
            plan_value = plan_node.text
            if plan_value is None:
                inner = plan_node.find("value")
                plan_value = inner.text if inner is not None else None
        is_10b5_plan = str(plan_value).strip().lower() in ("1", "true")

        transactions.append({
            "code": code,
            "shares": shares,
            "price": price,
            "txn_date": txn_date,
            "is_10b5_plan": is_10b5_plan,
        })

    issuer = {"name": issuer_name, "ticker": ticker, "cik": issuer_cik}
    insider = {"name": owner_name, "is_officer": is_officer,
               "is_director": is_director, "title": title}
    return issuer, insider, transactions


# ---------------------------------------------------------------------------
# STEP 2b: ticker universe filter (S&P 500 or NASDAQ-listed) - excludes thin,
# illiquid, or obscure small-caps that happen to pass every other filter
# ---------------------------------------------------------------------------

_universe_cache = {"tickers": None}


def load_ticker_universe():
    """Lazily loads and caches the set of allowed tickers: any S&P 500
    constituent, plus every NASDAQ-listed security (covers most small/mid
    caps). Both sources are free and require no API key. If both fail to
    load, returns None as a sentinel meaning "don't filter" - we'd rather
    let everything through than silently zero out every result because a
    data source hiccuped."""
    if _universe_cache["tickers"] is not None:
        return _universe_cache["tickers"]

    tickers = set()

    text = fetch("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt")
    if text:
        for line in text.splitlines()[1:]:
            if line.startswith("File Creation Time"):
                break
            parts = line.split("|")
            if len(parts) > 3 and parts[3].strip().upper() != "Y":  # skip test issues
                tickers.add(parts[0].strip().upper())
    else:
        print("  [warn] could not load NASDAQ-listed securities list")

    html = fetch("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    if html:
        m = re.search(r'<table[^>]*id="constituents"[^>]*>(.*?)</table>', html, re.DOTALL)
        if m:
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.DOTALL):
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
                if cells:
                    symbol = re.sub("<[^<]+?>", "", cells[0]).strip().upper()
                    if symbol:
                        tickers.add(symbol)
    else:
        print("  [warn] could not load S&P 500 constituent list")

    if not tickers:
        print("  [warn] ticker universe filter loaded nothing from either "
              "source - skipping the universe filter for this run")
        tickers = None

    _universe_cache["tickers"] = tickers
    return tickers


def is_allowed_ticker(ticker):
    universe = load_ticker_universe()
    if universe is None:
        return True
    return bool(ticker) and ticker.upper() in universe


# ---------------------------------------------------------------------------
# STEP 3: apply the "genuine insider buy" filter
# ---------------------------------------------------------------------------

def qualifying_buys_from_filing(filing):
    """Fetches + parses one filing, returns a list of qualifying buy dicts."""
    xml_url = find_xml_url(filing["index_url"])
    if not xml_url:
        return []
    xml_str = fetch(xml_url)
    if not xml_str:
        return []
    try:
        issuer, insider, transactions = parse_form4_xml(xml_str)
    except ET.ParseError:
        return []

    if not (insider["is_officer"] or insider["is_director"]):
        return []  # 10%-owner-only filer (e.g. a fund) - skip

    results = []
    for txn in transactions:
        if txn["code"] != "P":
            continue
        if txn["is_10b5_plan"]:
            continue
        try:
            shares = float(txn["shares"])
            price = float(txn["price"])
        except (TypeError, ValueError):
            continue
        total = shares * price
        if total < MIN_DOLLAR_VALUE:
            continue
        if not is_allowed_ticker(issuer["ticker"]):
            continue
        results.append({
            "ticker": issuer["ticker"] or "?",
            "company": issuer["name"] or filing["company"],
            "cik": issuer["cik"] or filing["cik"],
            "insider": insider["name"] or "Unknown",
            "title": insider["title"],
            "shares": shares,
            "price": price,
            "total": total,
            "txn_date": txn["txn_date"],
            "date_filed": filing["date_filed"],
            "filing_url": filing["index_url"],
        })
    return results


# ---------------------------------------------------------------------------
# STEP 4: cluster-buying check (2+ different insiders at the same company
# filing a code-P buy within the last 30 days)
# ---------------------------------------------------------------------------

def recent_form4_filings_for_company(cik, since_date):
    url = (f"{BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4"
           f"&dateb=&owner=include&count=40&output=atom")
    text = fetch(url)
    if not text:
        return []
    entries = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)
    out = []
    for entry in entries:
        date_m = re.search(r"<filing-date>([^<]+)</filing-date>", entry)
        href_m = re.search(r"<filing-href>([^<]+)</filing-href>", entry)
        if not (date_m and href_m):
            continue
        filed = datetime.strptime(date_m.group(1), "%Y-%m-%d").date()
        if filed < since_date:
            continue
        out.append({"date_filed": date_m.group(1), "index_url": href_m.group(1),
                     "cik": cik, "company": ""})
    return out


def find_cluster_tickers(qualifying_buys, run_date):
    """For each company that already has a qualifying buy today, check its
    last 30 days of Form 4s for buys by OTHER insiders too."""
    since = run_date.date() - timedelta(days=CLUSTER_LOOKBACK_DAYS)
    by_ticker = {}
    for b in qualifying_buys:
        by_ticker.setdefault(b["ticker"], {"cik": b["cik"], "insiders": set()})
        by_ticker[b["ticker"]]["insiders"].add(b["insider"])

    cluster_tickers = {}
    for ticker, info in by_ticker.items():
        if not info["cik"]:
            continue
        print(f"  Checking 30-day history for cluster buying: {ticker}")
        insiders = set(info["insiders"])
        for filing in recent_form4_filings_for_company(info["cik"], since):
            for buy in qualifying_buys_from_filing(filing):
                insiders.add(buy["insider"])
        if len(insiders) >= 2:
            cluster_tickers[ticker] = sorted(insiders)
    return cluster_tickers


# ---------------------------------------------------------------------------
# STEP 5: write the report
# ---------------------------------------------------------------------------

def write_report(qualifying_buys, cluster_tickers, run_date, out_path):
    qualifying_buys = sorted(qualifying_buys, key=lambda b: b["total"], reverse=True)

    lines = []
    lines.append(f"# Insider Buys - Form 4 filings for {run_date.strftime('%Y-%m-%d')}\n")
    lines.append(
        "Filters applied: transaction code **P** (open-market purchase) only, "
        "value >= $100,000, filer is an officer or director, the trade is "
        "**not** part of a pre-scheduled 10b5-1 plan, and the ticker is an "
        "S&P 500 constituent or a NASDAQ-listed security.\n"
    )

    if not qualifying_buys:
        lines.append("No filings passed all the filters today.\n")
    else:
        lines.append("| Ticker | Company | Insider | Title | Shares | Price | Total $ | Date Filed | Filing |")
        lines.append("|---|---|---|---|---:|---:|---:|---|---|")
        for b in qualifying_buys:
            flag = " 🔥" if b["ticker"] in cluster_tickers else ""
            lines.append(
                f"| {b['ticker']}{flag} | {b['company']} | {b['insider']} | {b['title']} | "
                f"{b['shares']:,.0f} | ${b['price']:,.2f} | ${b['total']:,.0f} | "
                f"{b['date_filed']} | [Filing]({b['filing_url']}) |"
            )
        lines.append("")

    if cluster_tickers:
        lines.append("## 🔥 Cluster buying (2+ different insiders, last 30 days)\n")
        for ticker, insiders in cluster_tickers.items():
            lines.append(f"- **{ticker}**: {', '.join(insiders)}")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return qualifying_buys


def write_json_report(qualifying_buys, cluster_tickers, run_date, out_path):
    """Machine-readable twin of the markdown report, for anything that needs
    to act on the results programmatically (e.g. a paper-trading task) rather
    than parse a markdown table."""
    payload = {
        "date": run_date.strftime("%Y-%m-%d"),
        "qualifying_buys": [
            {k: v for k, v in b.items()} for b in qualifying_buys
        ],
        "cluster_tickers": cluster_tickers,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) > 1:
        run_date = datetime.strptime(sys.argv[1], "%Y%m%d")
    else:
        run_date = datetime.today()

    filings, run_date = get_form4_filings_for_day(run_date)
    if not filings:
        print("No Form 4 filings found for this date.")
        return

    qualifying_buys = []
    total = len(filings)
    for i, filing in enumerate(filings, 1):
        if i % 25 == 0 or i == total:
            print(f"  Processed {i}/{total} filings - "
                  f"{len(qualifying_buys)} qualifying buys so far")
        try:
            qualifying_buys.extend(qualifying_buys_from_filing(filing))
        except Exception as e:
            print(f"  [warn] skipping {filing['index_url']}: {e}")

    print(f"\nDone scanning. {len(qualifying_buys)} qualifying buys found.")
    print("Checking for cluster buying (this fetches each hit company's 30-day history)...")
    cluster_tickers = find_cluster_tickers(qualifying_buys, run_date)

    out_path = f"insider-buys-{run_date.strftime('%Y-%m-%d')}.md"
    json_path = f"insider-buys-{run_date.strftime('%Y-%m-%d')}.json"
    qualifying_buys = write_report(qualifying_buys, cluster_tickers, run_date, out_path)
    write_json_report(qualifying_buys, cluster_tickers, run_date, json_path)

    print(f"\nSaved report to {out_path}")
    print(f"Saved machine-readable report to {json_path}")
    print(f"Qualifying buys: {len(qualifying_buys)}")
    print(f"Cluster tickers: {list(cluster_tickers.keys())}")


if __name__ == "__main__":
    main()
