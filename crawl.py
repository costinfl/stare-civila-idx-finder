"""Polite, resumable crawler that builds the static IDX catalogue.

It does not open record pages. The IDX PDFs are text reports with fixed
column headers ("Tip eveniment", "Imagine PDF"), and the archive's
"Conținut text fișiere atașate UD" search indexes that text, so a listing
search on those headers returns exactly the records that carry an index.

Per-county listings are cached in idx_store/listings/ so an interrupted run
resumes where it stopped. Every request goes through server.archive_get,
which keeps the shared 1.25 s pace.

    python crawl.py              # crawl missing counties, then export
    python crawl.py --refresh    # re-crawl every county
    python crawl.py --only PH-   # crawl one county prefix
    python crawl.py --export     # only rebuild site/data from the store
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "idx_store" / "listings"
SITE_DATA = ROOT / "site" / "data"
PHRASES = ["Tip eveniment", "Imagine PDF"]
PAGE_SIZE = 100
MAX_PAGES = 50


def listing_url(prefix: str, phrase: str, page: int) -> str:
    terms = [("-999005", "Contains", phrase), ("2", "StartsWith", prefix)]
    params: list[tuple[str, str]] = []
    for index, (field_id, operator, value) in enumerate(terms):
        params += [
            (f"searchParams[ts][{index}][FieldId]", field_id),
            (f"searchParams[ts][{index}][Negate]", "false"),
            (f"searchParams[ts][{index}][Operator]", operator),
            (f"searchParams[ts][{index}][Value]", value),
        ]
    params += [
        ("searchParams[pg]", str(page)),
        ("searchParams[pgs]", str(PAGE_SIZE)),
        ("searchParams[so]", "AND"),
        ("searchParams[sm]", "0"),
        ("searchParams[srt]", "3"),  # sort by Cotă UD so pages are stable
        ("searchParams[sro]", "1"),
        ("searchParams[tabletype]", "list"),
        ("searchParams[count]", server.COUNT_HINT),
    ]
    return server.AJAX + "&" + urllib.parse.urlencode(params)


def fetch_page(prefix: str, phrase: str, page: int) -> tuple[list[dict], int | None]:
    for attempt in range(4):
        try:
            payload, _ = server.archive_get(listing_url(prefix, phrase, page))
            data = json.loads(payload.decode("utf-8-sig")).get("data", {})
            break
        except Exception as error:  # network hiccup: back off, then retry
            if attempt == 3:
                raise
            print(f"    retry {attempt + 1} after error: {error}", flush=True)
            time.sleep(10 * (attempt + 1))
    if not isinstance(data, dict):
        return [], None
    rows = server.parse_search_rows(data.get("htmlResult", ""))
    holder = re.search(r"'fieldId': -999007.*?'count': (\d+)", str(data.get("facets", "")))
    return rows, int(holder.group(1)) if holder else None


RESULT_CAP = 5000  # the archive never returns more than 50 pages of 100
SPLIT_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-()_. "


def crawl_prefix(prefix: str, phrase: str, records: dict[str, dict], label: str) -> int | None:
    """Collect every listing row for one Cotă prefix, splitting it if capped."""
    page, seen, total = 1, 0, None
    while page <= MAX_PAGES:
        rows, page_total = fetch_page(prefix, phrase, page)
        if page == 1:
            total = page_total
        for row in rows:
            records.setdefault(row["record_id"], row)
        seen += len(rows)
        print(f"  {label} · {prefix} · “{phrase}” · page {page}: {len(rows)} rows ({len(records)} unique)", flush=True)
        if len(rows) < PAGE_SIZE:
            return total
        page += 1
    if seen >= RESULT_CAP:
        # The list was truncated: search each longer prefix separately. Empty
        # children cost one request each; only capped prefixes get here.
        print(f"  {prefix} hit the {RESULT_CAP}-result cap; splitting", flush=True)
        for char in SPLIT_CHARS:
            crawl_prefix(prefix + char, phrase, records, label)
    return total


def crawl_county(county: dict) -> dict:
    prefix = county["prefix"]
    records: dict[str, dict] = {}
    totals: dict[str, int | None] = {}
    for phrase in PHRASES:
        totals[phrase] = crawl_prefix(prefix, phrase, records, county["name"])
    return {
        "name": county["name"],
        "prefix": prefix,
        "crawled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "facet_totals": totals,
        "records": sorted(records.values(), key=lambda r: r["call_number"]),
    }


def export() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    counties, rows, levels = [], [], []
    for index, county in enumerate(server.COUNTIES):
        path = STORE / f"{county['prefix'].rstrip('-')}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        counties.append({"name": county["name"], "prefix": county["prefix"], "count": len(data["records"]), "crawled_at": data["crawled_at"]})
        for r in data["records"]:
            level = r["description_level"].removeprefix("niv.")
            if level not in levels:
                levels.append(level)
            # Compact row: [county index, record id, call number, title, years, level index]
            rows.append([len(counties) - 1, int(r["record_id"]), r["call_number"], r["title"], r["date_extremes"], levels.index(level)])
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": server.ARCHIVE,
        "columns": ["county", "record_id", "call_number", "title", "years", "level"],
        "counties": counties,
        "levels": levels,
        "rows": rows,
    }
    target = SITE_DATA / "catalogue.json"
    target.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(rows)} records from {len(counties)} counties to {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-crawl counties already in the store")
    parser.add_argument("--only", action="append", help="county prefix to crawl, e.g. PH- (repeatable)")
    parser.add_argument("--export", action="store_true", help="skip crawling; rebuild site data only")
    args = parser.parse_args()
    STORE.mkdir(parents=True, exist_ok=True)
    if not args.export:
        wanted = {p.upper() if p.endswith("-") else p.upper() + "-" for p in args.only or []}
        started = time.monotonic()
        for county in server.COUNTIES:
            if wanted and county["prefix"] not in wanted:
                continue
            path = STORE / f"{county['prefix'].rstrip('-')}.json"
            if path.exists() and not args.refresh and not wanted:
                continue
            print(f"{county['name']} ({county['prefix']})", flush=True)
            result = crawl_county(county)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
            print(f"  saved {len(result['records'])} records; facet totals {result['facet_totals']}; elapsed {time.monotonic() - started:.0f}s", flush=True)
    export()


if __name__ == "__main__":
    sys.exit(main())
