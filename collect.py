"""RADAR collector — fetches funding news from free public sources.

Component 1: EU-Startups funding RSS feed.
Stdlib only (no dependencies), so it runs anywhere — including GitHub Actions.

New items are appended to data/intake.csv, deduplicated by URL.
The CSV mirrors the structure of the future Google Sheets "Intake" tab,
so switching the storage backend later won't change the collectors.
"""

import csv
import html
import re
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

SOURCES = [
    {
        "name": "eu-startups-funding",
        "url": "https://www.eu-startups.com/category/fundin/feed/",
    },
]

INTAKE_CSV = Path(__file__).parent / "data" / "intake.csv"
CSV_FIELDS = ["collected_at", "source", "published", "title", "url", "summary"]

# Sites often block Python's default user agent
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RADAR-pipeline/1.0)"}


def ssl_context() -> ssl.SSLContext:
    # python.org builds on macOS don't ship CA certificates; fall back to the system bundle
    context = ssl.create_default_context()
    if not context.get_ca_certs() and Path("/etc/ssl/cert.pem").exists():
        context = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return context


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_rss(url: str) -> list[dict]:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
        root = ET.fromstring(response.read())

    items = []
    for item in root.iter("item"):
        published = ""
        pub_date = item.findtext("pubDate", "")
        if pub_date:
            try:
                published = parsedate_to_datetime(pub_date).date().isoformat()
            except (ValueError, TypeError):
                published = pub_date
        items.append(
            {
                "published": published,
                "title": strip_html(item.findtext("title", "")),
                "url": (item.findtext("link") or "").strip(),
                "summary": strip_html(item.findtext("description", ""))[:500],
            }
        )
    return items


def load_known_urls() -> set[str]:
    if not INTAKE_CSV.exists():
        return set()
    with INTAKE_CSV.open(newline="", encoding="utf-8") as f:
        return {row["url"] for row in csv.DictReader(f)}


def append_items(rows: list[dict]) -> None:
    is_new_file = not INTAKE_CSV.exists()
    INTAKE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with INTAKE_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new_file:
            writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    known_urls = load_known_urls()
    new_rows = []
    failures = 0

    for source in SOURCES:
        try:
            items = fetch_rss(source["url"])
        except Exception as error:  # one broken source must not stop the others
            print(f"[ERROR] {source['name']}: {error}", file=sys.stderr)
            failures += 1
            continue

        fresh = [i for i in items if i["url"] and i["url"] not in known_urls]
        for item in fresh:
            new_rows.append({"collected_at": collected_at, "source": source["name"], **item})
            known_urls.add(item["url"])
        print(f"{source['name']}: {len(items)} items in feed, {len(fresh)} new")

    if new_rows:
        append_items(new_rows)
    print(f"Done: {len(new_rows)} new items appended to {INTAKE_CSV.name}")

    # Fail the run (and the GitHub Action) only if every source failed
    return 1 if failures == len(SOURCES) else 0


if __name__ == "__main__":
    sys.exit(main())
