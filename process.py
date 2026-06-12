"""RADAR processor — turns raw news intake into a scored company database.

Reads data/intake.csv (written by collect.py), classifies each item
(funding round / noise / M&A / out of mandate), extracts company name,
country, round type and amount, and maintains data/companies.csv:
one row per company, rescored on every run.

Design rules (see project document):
- Scoring is code, not vibes: explicit weights, versioned here.
- Uncertain extractions go to a "review" queue for the weekly session —
  never silently dropped, never silently guessed.
- Manual columns (filled during weekly sessions) are preserved across runs.
- Scores without headcount history are PROVISIONAL: watchlist at most.

Stdlib only.
"""

import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

INTAKE_CSV = Path(__file__).parent / "data" / "intake.csv"
COMPANIES_CSV = Path(__file__).parent / "data" / "companies.csv"
EXCLUDED_CSV = Path(__file__).parent / "data" / "excluded.csv"

# Columns the script owns (recomputed) vs columns the analyst owns (preserved)
SCRIPT_FIELDS = [
    "company", "country", "status", "status_reason", "score", "provisional",
    "score_breakdown", "last_round_date", "round_type", "amount_eur_m",
    "first_seen", "last_news", "news_url",
]
MANUAL_FIELDS = [
    "employees_now", "employees_4w_ago", "key_jobs",  # none|hiring|vp|cfo
    "investor_quality",  # 0-12, see scoring guide
    "expansion",  # 0-10
    "weak_signals",  # 0-8
    "notes",
]
ALL_FIELDS = SCRIPT_FIELDS + MANUAL_FIELDS

# ---------------------------------------------------------------- classification

ROUND_VERBS = re.compile(
    r"raises|secures|lands|bags|nets|closes|raccoglie|chiude|incassa|ottiene"
    r"|lève|boucle|finalise|réalise une levée|levée de fonds"
    r"|finanzierungsrunde|sammelt|erhält|sichert"
    r"|funding round|investment round|seed round|series [a-d]"
    r"|ronda de financiación|levanta|cierra una ronda|capta"
    r"|financieringsronde|haalt .{0,30}op"
    r"|finansieringsrunda|tar in|samlar in"
    r"|runda finansowania|pozyskuje",
    re.IGNORECASE,
)

NOISE = [
    (re.compile(r"podcast|headlines news|newsletter|webinar|layoffs tracker", re.I), "rubrica/podcast"),
    (re.compile(r"the week.s \d+|sector snapshot|monthly recap|deze \w+ startups|these \d+ startups|unicorns", re.I), "articolo riassuntivo"),
    (re.compile(r"how to|why |what |guida|cosa |come |ou comment|here.s what", re.I), "articolo di opinione/guida"),
    (re.compile(r"\bipo\b|borsa|azionist|stock|shareholders|wchodzi na giełd", re.I), "società quotata/IPO"),
    (re.compile(r"token|crypto|blockchain coin|stablecoin", re.I), "crypto/token"),
    (re.compile(r"compra|acquisisce|acquires|acquisition of|übernimmt|rachète|fusione|merger", re.I), "M&A, non round"),
    (re.compile(r"fund (i{1,3}|iv|v)\b|nuovo fondo|primo closing|closing del fondo|fund of funds|sgr\b|venture capital fund|closes .{0,20}fund\b|raccolta del fondo", re.I), "raccolta di un fondo, non di un'azienda"),
    (re.compile(r"fisco|governo|parlamento|decreto|tax|eu starts|politik|bewaffnete", re.I), "policy/politica"),
]

NON_EUROPE = re.compile(
    r"statunitense|americana?\b|us-based|american |silicon valley|san francisco"
    r"|cinese|chinese|china.s|chiñski|chiñska|israeliana|israeli|indiana?\b|india.s"
    r"|japanese|giapponese|korean|coreana|canadese|canadian|australiana|brasiliana",
    re.IGNORECASE,
)

# Megaround / valuation noise: out of growth-equity mandate by size
BILLIONS = re.compile(
    r"(miliard|billion|milliard|mrd|\bmld\b|bn\b|valutazion|valuation|bewertet|bewertung"
    r"|valorisation|wycen|värderas|waard\b|vale )",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- extraction

# Strip Google News trailing publisher: "Title - Publisher"
PUBLISHER_TAIL = re.compile(r"\s+[-–]\s+[^-–]{2,40}$")

DESCRIPTOR = (
    r"(?i:l['’]|la |il |le |de |die |der |das |el |startup |scale-?up |fintech |healthtech "
    r"|proptech |insurtech |biotech |deeptech |spacetech |edtech |agritech |società "
    r"|azienda |piattaforma |app |italian[ao]? |milanese |torinese |romana |bolognese "
    r"|napoletana |francese |française? |tedesca |deutsche[sr]? |spagnola |olandese "
    r"|svedese |polacca |niemiecka |polska |szwedzka |polish |german |french |spanish "
    r"|dutch |swedish |finnish |danish |belgian |austrian |swiss |irish |portuguese "
    r"|norwegian |czech |estonian |european |ai |ia |ki |\w+-based |\w+['’]s )*"
)

# Generic tokens that pattern matching sometimes mistakes for company names
NAME_STOPLIST = {
    "la", "il", "le", "the", "el", "de", "die", "der", "das", "una", "un",
    "ai", "ia", "ki", "tech", "app", "startup", "fintech", "scaleup", "round",
    "series", "serie", "eu", "via", "capital", "ventures", "venture", "partners",
    "fund", "fondo", "exclusive", "report", "news",
}

NAME = r"(?P<name>[A-ZÀ-Ý][\w.&'’-]*(?:\s+[A-ZÀ-Ý][\w.&'’-]*){0,2})"

COMPANY_PATTERNS = [
    # EN: "Berlin-based Acme raises €5 million" / "Acme secures $10M Series A"
    re.compile(DESCRIPTOR + NAME + r"\s+(?:raises|secures|lands|bags|nets|closes|announces)", re.I & 0),
    # IT: "Ema Health raccoglie 3 mln" / "La fintech italiana DeepTree chiude un round"
    re.compile(DESCRIPTOR + NAME + r"\s+(?:raccoglie|chiude|incassa|ottiene|annuncia|lancia un round)"),
    # FR: "Dust lève 40 millions" / "Legaia boucle une levée"
    re.compile(DESCRIPTOR + NAME + r"\s+(?:lève|boucle|finalise|réalise|annonce)"),
    # DE: "Quobly sammelt 21 Millionen ein" / "Neura Robotics: ... investieren"
    re.compile(DESCRIPTOR + NAME + r"\s*(?::|sammelt|erhält|sichert sich|schließt)"),
    # ES: "Payflow levanta 20 millones" / NL: "ZutaCore haalt $100 miljoen op"
    re.compile(DESCRIPTOR + NAME + r"\s+(?:levanta|cierra|consigue|capta|haalt|tar in|samlar in|pozyskuje)"),
    # ".. round di finanziamento di Acme" / "round de Acme"
    re.compile(r"round (?:di finanziamento )?(?:di|de|der|of)\s+" + NAME),
    # "startup Sybilla ..." (any language)
    re.compile(r"start-?up\s+" + NAME, re.I & 0),
]

ROUND_TYPE = re.compile(
    r"(pre-?seed|seed|s[eé]rie[s]?\s?[a-d]\b|series\s?[a-d]\b|serie\s?[a-d]\b|bridge)",
    re.IGNORECASE,
)

# Amounts: "€73 million", "3 mln euro", "800 K€", "40 millions de dollars",
# "175 Millionen", "30 miljoner kronor", "1,4 mld", "450 mln €"
AMOUNT = re.compile(
    r"(?P<cur1>[€$£])?\s?(?P<num>\d+(?:[.,]\d+)?)\s?"
    r"(?P<scale>k€|k\b|million[is]?|millions?|millionen|miljoen|miljoner|million[e]?r"
    r"|mln|mio|m\b|m€|milion[óo]w|millones|miliard[io]?|milliarden?|billion|bn|mld|md)?"
    r"\s?(?P<cur2>€|\$|£|euro?s?|eur\b|dollar[is]?|dollars?|usd|kronor|kr\b|zł|sek|pln)?",
    re.IGNORECASE,
)

SCALE = {  # to millions
    "k€": 0.001, "k": 0.001,
    "million": 1, "millioni": 1, "millions": 1, "millionen": 1, "miljoen": 1,
    "miljoner": 1, "millioner": 1, "mln": 1, "mio": 1, "m": 1, "m€": 1,
    "milionów": 1, "milionow": 1, "millones": 1, "millioni": 1, "millionis": 1,
    "miliard": 1000, "miliardo": 1000, "miliardi": 1000, "milliarde": 1000,
    "milliarden": 1000, "billion": 1000, "bn": 1000, "mld": 1000, "md": 1000,
}
FX = {  # rough conversion to EUR, v1: precision is irrelevant for a ≤€100M gate
    "€": 1, "euro": 1, "euros": 1, "eur": 1, "": 1,
    "$": 0.9, "dollar": 0.9, "dollari": 0.9, "dollars": 0.9, "usd": 0.9,
    "£": 1.15, "kronor": 0.09, "kr": 0.09, "sek": 0.09, "zł": 0.23, "pln": 0.23,
}

COUNTRY_TOKENS = {
    "Italy": r"italian[ao]|milanese|torinese|romana|bolognese|napoletana|bebeez",
    "France": r"français|french|parisien|paris-based",
    "Germany": r"tedesc|deutsche|german |niemiecka|berlin-based|munich-based",
    "Spain": r"spagnol|spanish|español|madrid-based|barcelona-based",
    "Netherlands": r"olandese|dutch|nederlands|amsterdam-based",
    "Sweden": r"svedese|swedish|svensk|stockholm-based",
    "Poland": r"polacc|polish|polsk|warsaw-based",
    "UK": r"british|londinese|london-based|uk-based",
    "Finland": r"finnish|finlandese|helsinki-based",
    "Other Europe": r"belgian|austrian|swiss|portoghese|portuguese|danish|norwegian"
    r"|irish|czech|estonian|lithuanian|latvian|greek|romanian|zurich-based|vienna-based",
}

CITY_COUNTRY = {
    "Italy": r"\b(milan[oe]?|rom[ae]|torino|turin|bologna|napoli)\b",
    "France": r"\b(paris|lyon|marseille|toulouse)\b",
    "Germany": r"\b(berlin[oe]?|munich|münchen|hamburg|francofort\w*|frankfurt|köln)\b",
    "Spain": r"\b(madrid|barcellona|barcelona|valencia)\b",
    "Netherlands": r"\b(amsterdam|rotterdam|eindhoven)\b",
    "Sweden": r"\b(stoccolma|stockholm|göteborg|malmö)\b",
    "Poland": r"\b(varsavia|warsaw|warszawa|kraków|cracovia)\b",
    "UK": r"\b(london|londra|cambridge|oxford|manchester|edinburgh)\b",
    "Finland": r"\b(helsinki|espoo)\b",
    "Other Europe": r"\b(zurigo|zurich|vienna|wien|bruxelles|brussels|lisbon[a]?|copenhagen"
    r"|copenaghen|oslo|dublin[o]?|praga|prague|tallinn|vilnius|riga|atene|athens|bucarest)\b",
}

# Last resort: the language of a Google News feed is a weak hint of the
# company's country — marked with "?" and confirmed during the weekly session
SOURCE_COUNTRY_HINT = {
    "google-news-it": "Italy?", "google-news-fr": "France?", "google-news-de": "Germany?",
    "google-news-es": "Spain?", "google-news-nl": "Netherlands?", "google-news-se": "Sweden?",
    "google-news-pl": "Poland?", "bebeez-venture-capital": "Italy?",
}


def classify(title: str) -> tuple[str, str]:
    """Return (kind, reason): kind in {round, noise, non_europe, mega}."""
    for pattern, reason in NOISE:
        if pattern.search(title):
            return "noise", reason
    if NON_EUROPE.search(title):
        return "non_europe", "azienda extra-europea"
    if BILLIONS.search(title):
        return "mega", "megaround/valutazione miliardaria: fuori mandato growth"
    if ROUND_VERBS.search(title):
        return "round", ""
    return "noise", "nessun segnale di round nel titolo"


def clean_name(raw: str) -> str:
    name = raw.strip(" .,'’")
    # Drop leading generic words the descriptor failed to consume
    words = name.split()
    while words and words[0].lower().strip("'’") in NAME_STOPLIST:
        words = words[1:]
    return " ".join(words)


def extract_company(title: str) -> tuple[str, str]:
    """Return (name, confidence) with confidence in {high, low}."""
    for pattern in COMPANY_PATTERNS:
        match = pattern.search(title)
        if match:
            name = clean_name(match.group("name"))
            if len(name) > 1 and name.lower() not in NAME_STOPLIST:
                return name, "high"
    # Fallback: first capitalized run that is not sentence-initial noise
    match = re.search(NAME, title)
    if match:
        name = clean_name(match.group("name"))
        if len(name) > 1:
            return name, "low"
    return "", "low"


def extract_amount_eur_m(title: str) -> float | None:
    best = None
    for m in AMOUNT.finditer(title):
        if not m.group("scale") and not (m.group("cur1") or m.group("cur2")):
            continue
        num = float(m.group("num").replace(",", "."))
        scale = SCALE.get((m.group("scale") or "m").lower().strip(), 1)
        currency = (m.group("cur1") or m.group("cur2") or "").lower().strip()
        value = round(num * scale * FX.get(currency, 1), 2)
        best = max(best, value) if best is not None else value
    return best


def extract_round_type(title: str) -> str:
    match = ROUND_TYPE.search(title)
    return re.sub(r"\s+", " ", match.group(1).lower()) if match else ""


def detect_country(title: str, source: str) -> str:
    text = f"{title} {source}"
    for country, tokens in COUNTRY_TOKENS.items():
        if re.search(tokens, text, re.IGNORECASE):
            return country
    for country, cities in CITY_COUNTRY.items():
        if re.search(cities, text, re.IGNORECASE):
            return country
    return SOURCE_COUNTRY_HINT.get(source, "")


# ---------------------------------------------------------------- scoring v1

def months_since(date_str: str) -> float | None:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (date.today() - d).days / 30.4


def score_clock(months: float | None) -> int:  # weight 20
    if months is None:
        return 0
    if 12 <= months <= 24:
        return 20
    if 6 <= months < 12:
        return 8
    if 24 < months <= 30:
        return 10
    if months < 6:
        return 2
    return 4  # >30 months


def score_headcount(now: str, before: str) -> int | None:  # weight 25
    try:
        now_n, before_n = int(now), int(before)
    except (ValueError, TypeError):
        return None  # no history yet -> provisional
    if before_n <= 0:
        return None
    growth = (now_n - before_n) / before_n
    if growth >= 0.10:
        return 25
    if growth >= 0.05:
        return 15
    if growth >= 0:
        return 6
    return 0


def score_stage_fit(employees: str) -> int | None:  # weight 10
    try:
        n = int(employees)
    except (ValueError, TypeError):
        return None
    if 40 <= n <= 120:
        return 10
    if 20 <= n <= 250:
        return 5
    return 0


def score_key_jobs(value: str) -> int:  # weight 15
    return {"cfo": 15, "vp": 10, "hiring": 6}.get((value or "").strip().lower(), 0)


def manual_int(value: str, cap: int) -> int:
    try:
        return max(0, min(cap, int(value)))
    except (ValueError, TypeError):
        return 0


def compute_score(row: dict) -> tuple[int, bool, str]:
    """Return (score, provisional, breakdown). Provisional = headcount unknown."""
    headcount = score_headcount(row.get("employees_now"), row.get("employees_4w_ago"))
    stage = score_stage_fit(row.get("employees_now"))
    parts = {
        "headcount": headcount if headcount is not None else 0,
        "clock": score_clock(months_since(row.get("last_round_date"))),
        "jobs": score_key_jobs(row.get("key_jobs")),
        "investors": manual_int(row.get("investor_quality"), 12),
        "expansion": manual_int(row.get("expansion"), 10),
        "stage": stage if stage is not None else 0,
        "weak": manual_int(row.get("weak_signals"), 8),
    }
    provisional = headcount is None
    breakdown = ";".join(f"{k}:{v}" for k, v in parts.items())
    return sum(parts.values()), provisional, breakdown


# ---------------------------------------------------------------- pipeline

def normalize_company(name: str) -> str:
    return re.sub(r"\W+", "", name.lower())


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    intake = load_csv(INTAKE_CSV)
    if not intake:
        print("intake.csv vuoto: eseguire prima collect.py", file=sys.stderr)
        return 1

    companies = {normalize_company(r["company"]): r for r in load_csv(COMPANIES_CSV)}
    excluded_rows = []
    stats = {"round": 0, "noise": 0, "non_europe": 0, "mega": 0, "new": 0, "updated": 0}

    for item in intake:
        title = PUBLISHER_TAIL.sub("", item["title"]).strip()
        kind, reason = classify(title)
        stats[kind] += 1
        if kind != "round":
            excluded_rows.append({**item, "excluded_as": kind, "reason": reason})
            continue

        name, confidence = extract_company(title)
        if not name:
            excluded_rows.append({**item, "excluded_as": "review", "reason": "nome azienda non estratto"})
            continue

        key = normalize_company(name)
        row = companies.get(key, {f: "" for f in ALL_FIELDS})
        row["company"] = row["company"] or name
        row["country"] = row["country"] or detect_country(title, item["source"])
        row["first_seen"] = row["first_seen"] or item["published"]
        if item["published"] >= (row["last_news"] or ""):
            row["last_news"] = item["published"]
            row["news_url"] = item["url"]
        if item["published"] >= (row["last_round_date"] or ""):
            row["last_round_date"] = item["published"]
            row["round_type"] = extract_round_type(title) or row["round_type"]
            amount = extract_amount_eur_m(title)
            if amount:
                row["amount_eur_m"] = amount
        if confidence == "low" and not row.get("status"):
            row["status"] = "review"
            row["status_reason"] = "estrazione incerta: verificare nome/paese in sessione"
        stats["new" if key not in companies else "updated"] += 1
        companies[key] = row

    # Rescore everything on every run (the clock moves even without news)
    for row in companies.values():
        score, provisional, breakdown = compute_score(row)
        row["score"], row["provisional"], row["score_breakdown"] = score, "yes" if provisional else "no", breakdown
        if row["status"] == "review":
            continue
        if row.get("amount_eur_m") and float(row["amount_eur_m"]) > 100:
            row["status"], row["status_reason"] = "excluded", "round >€100M: fuori mandato"
        elif not row["country"]:
            row["status"], row["status_reason"] = "review", "paese non identificato"
        elif row["country"].endswith("?"):
            row["status"], row["status_reason"] = "watchlist", "paese da confermare in sessione"
        elif score >= 70 and not provisional:
            row["status"], row["status_reason"] = "shortlist", ""
        else:
            row["status"], row["status_reason"] = "watchlist", "punteggio provvisorio" if provisional else ""

    ordered = sorted(companies.values(), key=lambda r: -int(r["score"] or 0))
    write_csv(COMPANIES_CSV, ordered, ALL_FIELDS)
    write_csv(EXCLUDED_CSV, excluded_rows, list(intake[0].keys()) + ["excluded_as", "reason"])

    by_status = {}
    for row in ordered:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    print(f"Intake: {len(intake)} elementi -> round: {stats['round']}, rumore: {stats['noise']}, "
          f"extra-Europa: {stats['non_europe']}, megaround: {stats['mega']}")
    print(f"Aziende in database: {len(ordered)} ({stats['new']} nuove, {stats['updated']} aggiornate)")
    print("Per stato:", ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
