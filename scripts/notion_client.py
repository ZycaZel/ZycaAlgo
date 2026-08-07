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


def upsert_signal(ticker, company, note_text, filing_url=None):
    """Add a new Stock Watchlist row for `ticker` if one doesn't exist
    (status defaults to 'Researching' - flagged, not a recommendation),
    then append `note_text` as a callout block on that row's page so a
    running log builds up without ever overwriting the user's own
    Recommendation/Conviction/Status fields on an existing row."""
    existing = find_row_by_ticker(ticker)
    if existing:
        page_id = existing["id"]
    else:
        r = requests.post(
            f"{API}/pages",
            headers=_headers(),
            json={
                "parent": {"database_id": WATCHLIST_DB_ID},
                "properties": {
                    "Ticker": {"title": _rich_text(ticker)},
                    "Company Name": {"rich_text": _rich_text(company)},
                    "Status": {"status": {"name": "Researching"}},
                },
            },
            timeout=30,
        )
        r.raise_for_status()
        page_id = r.json()["id"]

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
