# RADAR — Growth Equity Deal Sourcing Pipeline

A personal, continuously-running deal sourcing pipeline that tracks "hot" Italian and European scale-ups from a growth equity perspective. Every Monday it collects funding news and growth signals from free public sources, scores companies against an explicit, versioned scoring model, and feeds a weekly shortlist + investment mini-memo published via newsletter and LinkedIn.

**Why this exists:** I'm building my way into growth equity by doing the analyst's job before having the job — and documenting every decision, bias and scoring revision along the way. The commit history of this repo is the proof of continuity.

## How it works

```
Free public sources          This repo (GitHub Actions)        Human judgment
──────────────────          ──────────────────────────        ──────────────
EU-Startups RSS        →    collect.py: fetch, dedupe,    →   Weekly session:
BeBeez, Google News         filter, score (v1, explicit        LinkedIn verification,
Crunchbase News, Growjo     weights) → database                shortlist, memo
```

- **Scoring is code, not vibes** — explicit weights, versioned in this repo, revised only every 8-12 weeks based on documented retrospectives.
- **Known biases are declared, not hidden** — see the project document for the full bias register (news visibility bias, hiring-centric bias, language bias…).
- **No LinkedIn scraping** — headcount and job posting checks are done manually, only on shortlisted companies.

## Status

| Component | Status |
|---|---|
| Collector: EU-Startups funding RSS | ✅ |
| Collector: BeBeez, Google News (IT/FR/DE/ES + EN/NL/SV/PL), Crunchbase News | ✅ |
| Collector: Growjo growth estimates | ✗ dropped — Cloudflare blocks non-browser clients; Growjo is now a manual source in the weekly session, with annual structured lists (FT 1000, LinkedIn Top Startups) as the non-news counterweight |
| Processor: classification, extraction, filters + scoring v1 (`process.py`) | ✅ |
| 2024-2025 funding rounds backfill (`backfill.py`) | ✅ |
| Google Sheets database connection, bidirectional (`sync_sheets.py`) | ✅ |
| GitHub Actions weekly schedule | planned |

## Run

```bash
python3 collect.py            # fetch news -> data/intake.csv
python3 sync_sheets.py pull    # pull analyst's manual edits from the Sheet -> companies.csv
python3 process.py            # classify, extract, score -> data/companies.csv
python3 sync_sheets.py push    # rewrite the Google Sheet from companies.csv
```

`collect.py` and `process.py` are stdlib only. `sync_sheets.py` needs `gspread` and `google-auth` (`pip install -r requirements.txt`) and a Google service account (`service-account.json`, gitignored). The one-time `backfill.py` pages back through the EU-Startups and BeBeez archives to seed the 12-24 month golden window — run once, never weekly.

`collect.py` appends new items to `data/intake.csv`, deduplicated by URL **and** by normalized title (Google News rotates its encoded redirect URLs, and the same story can arrive from two different feeds). The run fails if any single source fails: a feed that stays silently broken longer than the 14-day query window loses data for good.

`process.py` maintains `data/companies.csv` (one row per company, rescored on every run — the clock signal moves even without news). Uncertain extractions land in a review queue for the weekly session instead of being silently dropped or guessed; exclusions (megarounds >€100M, M&A, fund closings, crypto, non-European companies) are logged with reasons to `data/excluded.csv`. Analyst-owned columns (headcount history, key job postings, investor quality…) are filled manually during weekly sessions and survive reruns.
