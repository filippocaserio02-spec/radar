"""RADAR collector — fetches funding news from free public sources.

Sources: EU-Startups, BeBeez, Crunchbase News, Google News (IT/FR/DE/ES).
Stdlib only (no dependencies), so it runs anywhere — including GitHub Actions.

New items are appended to data/intake.csv, deduplicated by URL and by
normalized title (Google News rotates its encoded redirect URLs, so the
same article can reappear under a different URL; and the same story can
arrive from two different feeds).
The CSV mirrors the structure of the future Google Sheets "Intake" tab,
so switching the storage backend later won't change the collectors.
"""

import csv
import html
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

def google_news(query: str, lang: str, country: str) -> str:
    # when:14d window: one week of overlap between weekly runs (a skipped week
    # loses nothing), while staying safely under Google News RSS's hard cap of
    # 100 items per feed — a 30d window already hit the cap on FR/ES, silently
    # truncating results
    return (
        "https://news.google.com/rss/search?"
        f"q={urllib.parse.quote(query + ' when:14d')}"
        f"&hl={lang}&gl={country}&ceid={country}:{lang}"
    )


SOURCES = [
    {
        "name": "eu-startups-funding",
        # "fundin" is NOT a typo: it's the site's real category slug
        # ("funding" returns 404 — verified 12 Jun 2026)
        "url": "https://www.eu-startups.com/category/fundin/feed/",
    },
    {
        "name": "bebeez-venture-capital",
        "url": "https://bebeez.it/category/venture-capital/feed/",
    },
    {
        "name": "crunchbase-news",
        "url": "https://news.crunchbase.com/feed/",
    },
    {
        "name": "google-news-it",
        "url": google_news('"round di finanziamento"', "it", "IT"),
    },
    {
        "name": "google-news-fr",
        "url": google_news('"levée de fonds"', "fr", "FR"),
    },
    {
        "name": "google-news-de",
        "url": google_news('"Finanzierungsrunde"', "de", "DE"),
    },
    {
        "name": "google-news-es",
        "url": google_news('"ronda de financiación"', "es", "ES"),
    },
    {
        "name": "google-news-en",
        # Worldwide EN volume exceeds the 100-item feed cap even at 14 days,
        # so this is a best-effort sample of the most recent items; primary
        # EN coverage comes from EU-Startups and Crunchbase News
        "url": google_news('"funding round"', "en", "GB"),
    },
    {
        "name": "google-news-nl",
        "url": google_news('"financieringsronde"', "nl", "NL"),
    },
    {
        "name": "google-news-se",
        "url": google_news('"finansieringsrunda"', "sv", "SE"),
    },
    {
        "name": "google-news-pl",
        "url": google_news('"runda finansowania"', "pl", "PL"),
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


def normalize_title(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


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


def load_known() -> tuple[set[str], set[str]]:
    if not INTAKE_CSV.exists():
        return set(), set()
    with INTAKE_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["url"] for r in rows}, {normalize_title(r["title"]) for r in rows}


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
    known_urls, known_titles = load_known()
    new_rows = []
    failures = 0

    for source in SOURCES:
        try:
            items = fetch_rss(source["url"])
        except Exception as error:  # one broken source must not stop the others
            print(f"[ERROR] {source['name']}: {error}", file=sys.stderr)
            failures += 1
            continue

        fresh = 0
        for item in items:
            title_key = normalize_title(item["title"])
            if not item["url"] or item["url"] in known_urls:
                continue
            if title_key and title_key in known_titles:
                continue
            known_urls.add(item["url"])
            known_titles.add(title_key)
            new_rows.append({"collected_at": collected_at, "source": source["name"], **item})
            fresh += 1
        print(f"{source['name']}: {len(items)} items in feed, {fresh} new")

    if new_rows:
        append_items(new_rows)
    print(f"Done: {len(new_rows)} new items appended to {INTAKE_CSV.name}")

    # Any broken source fails the run (and the GitHub Action): a feed that
    # stays silently down longer than the query window loses data for good
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
