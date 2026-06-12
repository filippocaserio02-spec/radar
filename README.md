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
| Scoring v1 in script | planned |
| Google Sheets database connection | planned |
| GitHub Actions weekly schedule | planned |
| 2024-2025 funding rounds backfill | planned |

## Run

```bash
python3 collect.py        # stdlib only, no dependencies
```

New items are appended to `data/intake.csv`, deduplicated by URL **and** by normalized title (Google News rotates its encoded redirect URLs, and the same story can arrive from two different feeds). The run fails if any single source fails: a feed that stays silently broken longer than the 14-day query window loses data for good.
