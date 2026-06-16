"""RADAR backfill — one-time load of 2024-2025 funding archives.

The live collectors (collect.py) read RSS feeds, which only expose the most
recent items. News sources therefore signal a company at round time, when its
"clock" is at zero and its score is at the minimum — so for the first year the
pipeline would only produce lukewarm shortlists (see project document, "Backfill
iniziale").

This script fixes the cold start: it pages back through the WordPress archives of
EU-Startups and BeBeez (both expose ?paged=N on their category feeds) until it
reaches CUTOFF, feeding everything into the same data/intake.csv with the same
URL/title dedupe. After running it once, run process.py to rescore: rounds from
2024-2025 land in the 12-24 month "golden window" (score_clock = 20).

Run once, then commit the enlarged intake.csv. Stdlib only.
"""

import sys
import time
from datetime import datetime, timezone

from collect import (
    INTAKE_CSV,
    append_items,
    fetch_rss,
    load_known,
    normalize_title,
)

# Rounds older than ~30 months score low anyway; 2024-01-01 captures the whole
# golden window (12-24 months as of mid-2026) plus the adjacent 6-12 and 24-30
# month bands.
CUTOFF = "2024-01-01"

ARCHIVES = [
    {
        "name": "bebeez-venture-capital",  # 200 items/page: cheap, ~6 pages to 2024
        "base": "https://bebeez.it/category/venture-capital/feed/",
        "max_pages": 12,
    },
    {
        "name": "eu-startups-funding",  # 10 items/page: ~270 pages to mid-2024
        "base": "https://www.eu-startups.com/category/fundin/feed/",
        "max_pages": 320,
    },
]

# Be polite to the archives: this is a deep one-time crawl, not a weekly feed read
PAUSE_SECONDS = 0.3


def page_url(base: str, page: int) -> str:
    return base if page == 1 else f"{base}?paged={page}"


def fetch_with_retries(url: str, retries: int = 4) -> list[dict]:
    """Fetch a feed page, retrying transient errors with backoff.

    A deep one-time crawl spans hundreds of requests, so a single transient
    timeout must not abort the whole backfill (unlike the weekly collector,
    which fails loud on purpose). A 404 is permanent — re-raised immediately
    so the caller stops at the last archive page.
    """
    for attempt in range(1, retries + 1):
        try:
            return fetch_rss(url)
        except Exception as error:
            if "404" in str(error) or attempt == retries:
                raise
            wait = attempt * 2
            print(f"    retry {attempt}/{retries - 1} after {type(error).__name__} (wait {wait}s)")
            time.sleep(wait)
    return []


def backfill_source(source: dict, known_urls: set, known_titles: set) -> list[dict]:
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    rows = []
    start = source.get("start_page", 1)
    for page in range(start, source["max_pages"] + 1):
        try:
            items = fetch_with_retries(page_url(source["base"], page))
        except Exception as error:  # 404 past the last page, or retries exhausted
            print(f"  page {page}: stop ({type(error).__name__}: {str(error)[:60]})")
            break
        if not items:
            print(f"  page {page}: empty, stop")
            break

        dates = [it["published"] for it in items if it["published"]]
        newest = max(dates) if dates else ""

        fresh = 0
        for item in items:
            if item["published"] and item["published"] < CUTOFF:
                continue
            title_key = normalize_title(item["title"])
            if not item["url"] or item["url"] in known_urls:
                continue
            if title_key and title_key in known_titles:
                continue
            known_urls.add(item["url"])
            known_titles.add(title_key)
            rows.append({"collected_at": collected_at, "source": source["name"], **item})
            fresh += 1

        span = f"{min(dates)}..{max(dates)}" if dates else "no-dates"
        print(f"  page {page}: {len(items)} items {span}, {fresh} new")

        # Stop once the whole page predates the cutoff (ordering isn't strictly
        # monotonic on BeBeez, so gate on the newest date, not the oldest)
        if newest and newest < CUTOFF:
            print(f"  page {page}: newest item < {CUTOFF}, stop")
            break
        time.sleep(PAUSE_SECONDS)
    return rows


def main() -> int:
    known_urls, known_titles = load_known()
    print(f"Backfill cutoff: {CUTOFF}. Known items before: {len(known_urls)}")

    all_new = []
    for source in ARCHIVES:
        print(f"\n{source['name']} (max {source['max_pages']} pages):")
        all_new.extend(backfill_source(source, known_urls, known_titles))

    if all_new:
        append_items(all_new)
    print(f"\nDone: {len(all_new)} new items appended to {INTAKE_CSV.name}")
    print("Next: run process.py to rescore the database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
