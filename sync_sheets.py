"""RADAR sheets sync — bidirectional bridge between companies.csv and Google Sheets.

The analyst fills the manual columns (employees, investor_quality, notes...) in
the Google Sheet, including from a phone. The script owns the script columns
(name, country, score, last round...). To keep both in sync without ever
clobbering the analyst's edits, the weekly pipeline runs:

    collect.py  ->  sync_sheets.py pull  ->  process.py  ->  sync_sheets.py push

- pull: copy the manual columns from the Sheet back into companies.csv, so
  process.py rescores using the latest human input.
- push: overwrite the Sheet from companies.csv (by then fully recomputed).
  As a safety net, push also re-reads the Sheet's manual columns first, so a
  standalone push can never wipe an edit that hasn't reached the CSV yet.

Auth: a Google service account (service-account.json, gitignored). The target
Sheet must be shared with the service account email as Editor.

Deps: gspread, google-auth (see requirements.txt). Only this script and the
GitHub Action need them; the collectors stay dependency-free.
"""

import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from process import (
    ALL_FIELDS,
    COMPANIES_CSV,
    MANUAL_FIELDS,
    load_csv,
    normalize_company,
    write_csv,
)

KEY_FILE = Path(__file__).parent / "service-account.json"
SHEET_ID = "1O7M1btzRWDsoqc7DwuAG40YM67tBQzjOYuJqefBqjIs"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def open_worksheet():
    creds = Credentials.from_service_account_file(str(KEY_FILE), scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(SHEET_ID).sheet1


def manual_by_company(worksheet) -> dict[str, dict]:
    """Map normalized company name -> its manual-column values, read from the Sheet."""
    out = {}
    for record in worksheet.get_all_records():
        key = normalize_company(str(record.get("company", "")))
        if not key:
            continue
        out[key] = {f: str(record.get(f, "")).strip() for f in MANUAL_FIELDS}
    return out


def merge_manual_into_rows(rows: list[dict], edits: dict[str, dict]) -> int:
    """Overlay Sheet manual edits onto CSV rows. The human wins on manual fields."""
    changed = 0
    for row in rows:
        edit = edits.get(normalize_company(row.get("company", "")))
        if not edit:
            continue
        for field in MANUAL_FIELDS:
            if edit[field] and edit[field] != row.get(field, ""):
                row[field] = edit[field]
                changed += 1
    return changed


def pull() -> int:
    rows = load_csv(COMPANIES_CSV)
    edits = manual_by_company(open_worksheet())
    changed = merge_manual_into_rows(rows, edits)
    write_csv(COMPANIES_CSV, rows, ALL_FIELDS)
    print(f"pull: {len(edits)} righe lette dal foglio, {changed} valori manuali aggiornati in companies.csv")
    print("Ora esegui process.py per ricalcolare i punteggi, poi push.")
    return 0


def push() -> int:
    worksheet = open_worksheet()
    rows = load_csv(COMPANIES_CSV)
    if not rows:
        print("companies.csv vuoto: eseguire prima process.py", file=sys.stderr)
        return 1
    # Safety net: never overwrite a manual edit that only lives in the Sheet
    merge_manual_into_rows(rows, manual_by_company(worksheet))

    values = [ALL_FIELDS] + [[row.get(f, "") for f in ALL_FIELDS] for row in rows]
    worksheet.clear()
    worksheet.update(values=values, range_name="A1")
    worksheet.freeze(rows=1)
    print(f"push: {len(rows)} aziende scritte nel foglio (intestazione + dati).")
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "pull":
        return pull()
    if action == "push":
        return push()
    print("Uso: python sync_sheets.py [pull|push]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
