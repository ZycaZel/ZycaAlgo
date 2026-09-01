"""
Warns if the scanner has gone quiet.

The daily job can succeed - green checkmark, no failed step, failure alert
never fires - while silently finding nothing at all, because "zero qualifying
buys today" and "SEC changed their filing format and our parser now matches
nothing" look identical from the outside. Both just produce an empty
qualifying_buys list.

A single empty day is normal (recent history runs 1-15 signals/day, and slow
days happen). Several empty days in a row is not, so that's what this checks.

Reads the archived scans the daily job already commits - no extra API calls,
no new data source. Posts to the same Discord webhook as the other alerts.
No-op if DISCORD_WEBHOOK_URL isn't set, and never fails the build: a
monitoring check that can break the pipeline it monitors is worse than no
check at all.
"""

import glob
import json
import os
import re
import sys

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(SCRIPT_DIR, "..", "data", "archive")

# Recent history never shows two zero-days back to back, so three in a row is
# the point where "quiet market" stops being the likely explanation.
QUIET_DAYS_THRESHOLD = 3


def recent_scans(limit):
    """The most recent `limit` archived scans, newest last.

    Sorted by the date in the filename rather than mtime - a re-run or a
    fresh clone rewrites mtimes, but the date in the name is the actual
    trading day the scan covered.
    """
    paths = glob.glob(os.path.join(ARCHIVE_DIR, "insider-buys-*.json"))
    dated = []
    for p in paths:
        m = re.search(r"insider-buys-(\d{4}-\d{2}-\d{2})\.json$", os.path.basename(p))
        if m:
            dated.append((m.group(1), p))
    dated.sort()
    return dated[-limit:]


def qualifying_count(path):
    with open(path, encoding="utf-8") as f:
        return len(json.load(f).get("qualifying_buys", []))


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")

    scans = recent_scans(QUIET_DAYS_THRESHOLD)
    if len(scans) < QUIET_DAYS_THRESHOLD:
        print(f"Only {len(scans)} archived scan(s) - not enough history to judge. Skipping.")
        return

    counts = []
    for date, path in scans:
        try:
            counts.append((date, qualifying_count(path)))
        except Exception as e:
            print(f"[warn] could not read {path}: {e}")
            return

    summary = ", ".join(f"{d}: {c}" for d, c in counts)

    if any(c > 0 for _, c in counts):
        print(f"Scanner healthy - {summary}")
        return

    print(f"WARNING: {QUIET_DAYS_THRESHOLD} consecutive scans found zero qualifying buys ({summary})")
    if not webhook:
        print("DISCORD_WEBHOOK_URL not set - skipping alert.")
        return

    try:
        requests.post(
            webhook,
            json={
                "content": (
                    f"**ZycaAlgo: the scanner has gone quiet.** The last "
                    f"{QUIET_DAYS_THRESHOLD} scans all found zero qualifying buys "
                    f"({summary}).\nThe daily job is still passing, so this won't "
                    f"trigger a failure alert - but it's worth checking whether SEC "
                    f"changed their filing format and the parser is silently matching "
                    f"nothing."
                )
            },
            timeout=20,
        )
        print("Alert posted to Discord.")
    except Exception as e:
        # Never fail the build over a monitoring notification.
        print(f"[warn] could not post alert: {e}")


if __name__ == "__main__":
    main()
