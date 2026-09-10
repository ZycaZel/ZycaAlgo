"""
Thin client for ZycaAlgo's Notion integration ("Stock Research" workspace).

Three things this supports:
  - upsert_signal(): called from the daily scan when a qualifying insider
    buy is found, to add/update a row in the user's Stock Watchlist database.
  - get_watchlist_context(): read a ticker's existing research (properties +
    page body) so a chat session can use the user's own notes as context.
  - create_writeup_page(): push a markdown write-up as a new page under the
    Stock Research page.

Requires NOTION_TOKEN env var (a Notion internal integration secret, shared
with the "Stock Research" page and its "Stock Watchlist" database).
"""

import os
import re

import requests

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

WATCHLIST_DB_ID = "34b05e41-eb5c-802b-ab6a-e95c7a4ae683"
STOCK_RESEARCH_PAGE_ID = "34b05e41-eb5c-805c-8eaa-cf78b895c2ee"

VALID_STATUSES = {"Passed", "Sold", "Researching", "Watching", "Owned"}


def _headers():
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN env var is not set.")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rich_text(text):
    return [{"type": "text", "text": {"content": text[:2000]}}]


_schema_cache = {}


def watchlist_schema():
    """{property name: notion type} for the Stock Watchlist database.

    Read once and reused. Everything written below is filtered through this,
    because a payload naming a property the database doesn't have - or giving
    it the wrong type - is rejected outright, taking the whole row with it.
    Reading the schema means the sync fits whatever columns exist rather than
    assuming a particular database layout."""
    if not _schema_cache:
        r = requests.get(f"{API}/databases/{WATCHLIST_DB_ID}", headers=_headers(), timeout=30)
        r.raise_for_status()
        for name, meta in (r.json().get("properties") or {}).items():
            _schema_cache[name] = meta.get("type")
    return _schema_cache


def _prop(name, value, schema):
    """Build one property payload, or None if the column is absent, the type
    isn't one handled here, or the value is empty."""
    if value is None or value == "":
        return None
    kind = schema.get(name)
    if kind is None:
        return None
    if kind == "number":
        try:
            return {"number": float(value)}
        except (TypeError, ValueError):
            return None
    if kind == "select":
        return {"select": {"name": str(value)[:100]}}
    if kind == "status":
        return {"status": {"name": str(value)[:100]}}
    if kind == "rich_text":
        return {"rich_text": _rich_text(str(value))}
    if kind == "title":
        return {"title": _rich_text(str(value))}
    if kind == "url":
        return {"url": str(value)[:2000]}
    if kind == "date":
        return {"date": {"start": str(value)}}
    if kind == "checkbox":
        return {"checkbox": bool(value)}
    return None


def resolve_prop(schema, *candidates):
    """First candidate name that exists in the database, else None.

    Column names differ between workspaces - a conviction field might be
    "Conviction" or "Conviction (1-10)" - so the sync asks the schema what
    this database actually calls things instead of assuming one spelling."""
    for name in candidates:
        if name in schema:
            return name
    lowered = {k.lower(): k for k in schema}
    for name in candidates:
        hit = lowered.get(name.lower())
        if hit:
            return hit
    return None


def conviction_score(signal):
    """Signal strength on a 1-10 scale.

    Built only from the two dimensions this project has actually measured.
    The ablation study over 3,561 backtested signals found cluster buys beat
    solitary ones by 1.39 points at five days (p = 0.016), and purchases at or
    above the median dollar size beat smaller ones by 1.30 points (p < 0.001).
    Nothing else tested separated anything, so nothing else feeds this.

    It grades the signal, not the company. It is not a recommendation, and
    it is not a view on what the shares are worth."""
    total = signal.get("total") or 0
    score = 2
    if signal.get("is_cluster"):
        score += 3
    if (signal.get("insider_count") or 1) >= 3:
        score += 1
    if total >= 1_000_000:
        score += 3
    elif total >= 500_000:
        score += 2
    elif total >= 250_000:
        score += 1
    return max(1, min(10, score))


def conviction_from_signal(signal):
    """The same grading as a word, for databases whose conviction column is a
    select rather than a number."""
    score = conviction_score(signal)
    if score >= 7:
        return "High"
    if score >= 4:
        return "Medium"
    return "Low"


def find_row_by_ticker(ticker):
    """Returns the page dict for an existing Stock Watchlist row, or None."""
    r = requests.post(
        f"{API}/databases/{WATCHLIST_DB_ID}/query",
        headers=_headers(),
        json={"filter": {"property": "Ticker", "title": {"equals": ticker}}},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def upsert_signal(ticker, company, note_text=None, filing_url=None, fundamentals=None, signal=None):
    """Add a new Stock Watchlist row for `ticker` if one doesn't exist
    (status defaults to 'Researching' - flagged, not a recommendation),
    then append `note_text` as a callout block on that row's page (skipped
    if `note_text` is None - used for fundamentals-only refreshes) so a
    running log builds up without ever overwriting the user's own
    Recommendation/Conviction/Status fields on an existing row.

    `fundamentals` carries the Yahoo figures (sector, price, and the analyst
    consensus target/recommendation). `signal` carries the filing that
    triggered this - insider, title, size, dates, cluster flag.

    Two write rules. Live market figures - current price and the analyst
    consensus - refresh on every run, because they are meant to track. Every
    other field is written only where the row has nothing yet, so anything
    the user typed or corrected by hand survives the next sync untouched.

    On Price Target specifically: ZycaAlgo has no valuation model, so a target
    it invented would be fabricated. The number written here is the analyst
    mean from Yahoo - a real, sourced, third-party figure - and the note on
    the page says so. Conviction is graded from the signal's own measured
    dimensions (see conviction_from_signal), not from a view on the company.

    Every property is filtered through the database's actual schema first, so
    a column you don't have is skipped rather than failing the whole row."""
    fundamentals = fundamentals or {}
    signal = signal or {}
    try:
        schema = watchlist_schema()
    except requests.RequestException:
        schema = {}

    def live(props, name, value):
        """Refreshed every run - these are live market figures."""
        p = _prop(name, value, schema)
        if p:
            props[name] = p

    def once(props, name, value, existing_row):
        """Written only where the row has nothing yet, so a value the user
        typed or corrected by hand is never overwritten by the next sync."""
        if existing_row is not None:
            current = (existing_row.get("properties") or {}).get(name) or {}
            kind = current.get("type")
            if kind and current.get(kind) not in (None, "", [], {}):
                return
        p = _prop(name, value, schema)
        if p:
            props[name] = p

    def market_and_signal(props, existing_row):
        # Live market data - always current.
        live(props, "Current Price", fundamentals.get("price"))

        # Analyst consensus. Somebody else's opinion, refreshed as it moves.
        # ZycaAlgo has no valuation model and never invents a target.
        live(props, "Price Target", fundamentals.get("target_mean"))
        live(props, "Target High", fundamentals.get("target_high"))
        live(props, "Target Low", fundamentals.get("target_low"))
        live(props, "Analyst Count", fundamentals.get("analyst_count"))
        once(props, "Recommendation", fundamentals.get("recommendation"), existing_row)

        # Conviction lands in whatever this database calls it, in whatever
        # type it is: a 1-10 number column gets the score, a select gets the
        # word. Writing the wrong type would fail the whole row silently.
        # Only where there is a signal to grade. A ticker ZycaAlgo has never
        # flagged has no signal strength, and a number put in the column
        # anyway would be fiction dressed as a score.
        conv = resolve_prop(schema, "Conviction (1-10)", "Conviction", "Conviction Score")
        if conv and signal:
            value = conviction_score(signal) if schema.get(conv) == "number" \
                else conviction_from_signal(signal)
            once(props, conv, value, existing_row)

        # When ZycaAlgo last looked at this ticker - a fact, unlike the
        # user's own "Next Review" planning date, which is left alone.
        reviewed = resolve_prop(schema, "Last Reviewed", "Last Reviewed Date")
        if reviewed and signal.get("date_filed"):
            live(props, reviewed, signal["date_filed"])

        # The signal that flagged this ticker. Facts from the filing, plus a
        # conviction grade derived from them - written once so the user's own
        # judgment on an existing row always wins.
        once(props, "Sector", fundamentals.get("sector"), existing_row)
        once(props, "Conviction", conviction_from_signal(signal), existing_row)
        once(props, "Insider", signal.get("insider"), existing_row)
        once(props, "Insider Title", signal.get("title"), existing_row)
        once(props, "Purchase Size", signal.get("total"), existing_row)
        once(props, "Shares Bought", signal.get("shares"), existing_row)
        once(props, "Insider Price", signal.get("price"), existing_row)
        once(props, "Transaction Date", signal.get("txn_date"), existing_row)
        once(props, "Filing Date", signal.get("date_filed"), existing_row)
        once(props, "Cluster Buy", signal.get("is_cluster"), existing_row)
        once(props, "Insiders Buying", signal.get("insider_count"), existing_row)
        once(props, "Filing URL", filing_url, existing_row)

    existing = find_row_by_ticker(ticker)
    if existing:
        page_id = existing["id"]
        update_props = {}
        market_and_signal(update_props, existing)
        if update_props:
            r = requests.patch(
                f"{API}/pages/{page_id}",
                headers=_headers(),
                json={"properties": update_props},
                timeout=30,
            )
            r.raise_for_status()
    else:
        props = {
            "Ticker": {"title": _rich_text(ticker)},
        }
        p = _prop("Company Name", company, schema)
        if p:
            props["Company Name"] = p
        p = _prop("Status", "Researching", schema)
        if p:
            props["Status"] = p
        market_and_signal(props, None)
        r = requests.post(
            f"{API}/pages",
            headers=_headers(),
            json={"parent": {"database_id": WATCHLIST_DB_ID}, "properties": props},
            timeout=30,
        )
        r.raise_for_status()
        page_id = r.json()["id"]

    if note_text is None:
        return page_id

    blocks = [{
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(note_text),
            "icon": {"type": "emoji", "emoji": "\U0001F514"},
        },
    }]
    if filing_url:
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": "View filing", "link": {"url": filing_url}},
                }]
            },
        })
    r = requests.patch(
        f"{API}/blocks/{page_id}/children",
        headers=_headers(),
        json={"children": blocks},
        timeout=30,
    )
    r.raise_for_status()
    return page_id


def get_watchlist_context(ticker):
    """Returns {properties, notes: [block plain-text strings]} for a ticker's
    existing row, or None if there isn't one yet."""
    row = find_row_by_ticker(ticker)
    if not row:
        return None
    props = {}
    for name, val in row["properties"].items():
        t = val["type"]
        if t == "title":
            props[name] = "".join(x["plain_text"] for x in val["title"])
        elif t == "rich_text":
            props[name] = "".join(x["plain_text"] for x in val["rich_text"])
        elif t == "number":
            props[name] = val["number"]
        elif t == "select":
            props[name] = val["select"]["name"] if val["select"] else None
        elif t == "status":
            props[name] = val["status"]["name"] if val["status"] else None
        elif t == "date":
            props[name] = val["date"]["start"] if val["date"] else None
        elif t == "formula":
            props[name] = val["formula"].get(val["formula"]["type"])

    notes = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(f"{API}/blocks/{row['id']}/children", headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for block in data.get("results", []):
            t = block["type"]
            rich = block.get(t, {}).get("rich_text")
            if rich:
                notes.append("".join(x["plain_text"] for x in rich))
        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break
    return {"properties": props, "notes": notes}


def _markdown_to_blocks(markdown_text):
    """Minimal markdown -> Notion blocks converter: headings, paragraphs,
    bullet lists, tables (rendered as a monospace code block, since Notion's
    real table blocks are awkward to build from scratch), and images."""
    blocks = []
    lines = markdown_text.split("\n")
    i = 0
    table_buf = []

    def flush_table():
        if table_buf:
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {"rich_text": _rich_text("\n".join(table_buf)), "language": "plain text"},
            })
            table_buf.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|"):
            table_buf.append(line)
            i += 1
            continue
        flush_table()

        if not line.strip():
            i += 1
            continue
        img = re.match(r"!\[.*?\]\((.*?)\)", line)
        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        bullet = re.match(r"^[-*]\s+(.*)", line)
        if img:
            i += 1
            continue  # local image paths aren't reachable from Notion; skip rather than break the page
        elif heading:
            level = len(heading.group(1))
            blocks.append({
                "object": "block",
                "type": f"heading_{level}",
                f"heading_{level}": {"rich_text": _rich_text(heading.group(2))},
            })
        elif bullet:
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _rich_text(bullet.group(1))},
            })
        elif line.strip() == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rich_text(line)},
            })
        i += 1
    flush_table()
    return blocks


def create_writeup_page(title, markdown_text, parent_page_id=STOCK_RESEARCH_PAGE_ID):
    """Creates a new page under `parent_page_id` (defaults to the Stock
    Research page) from a markdown string. Notion caps each page-creation
    call at 100 blocks, so extra blocks are appended in follow-up calls."""
    blocks = _markdown_to_blocks(markdown_text)
    r = requests.post(
        f"{API}/pages",
        headers=_headers(),
        json={
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": _rich_text(title)}},
            "children": blocks[:100],
        },
        timeout=30,
    )
    r.raise_for_status()
    page_id = r.json()["id"]

    for start in range(100, len(blocks), 100):
        r = requests.patch(
            f"{API}/blocks/{page_id}/children",
            headers=_headers(),
            json={"children": blocks[start:start + 100]},
            timeout=30,
        )
        r.raise_for_status()
    return f"https://notion.so/{page_id.replace('-', '')}"
