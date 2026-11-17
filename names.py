"""Polite, resumable crawler for the names inside IDX PDFs.

For each catalogue record (from crawl.py's idx_store/listings/) it opens the
record page to find its IDX PDF(s), downloads them, and extracts the table
rows with idxparse. Two or more requests per record, all through the shared
1.25 s pace in server.archive_get, so the full national run takes days: run it
in chunks with --max-minutes; it resumes where it stopped.

Results are appended to idx_store/names/<CC>.jsonl, one line per record:
    {"id": 13913618, "files": [{"filename": ..., "pdf_url": ..., "entries": [...]}]}
A record whose page or PDF failed is written with "error" and retried on the
next run with --retry-errors.

    python names.py --max-minutes 60          # work through all counties
    python names.py --only PH- --limit 20     # sample one county
    python names.py --stats                   # progress summary, no requests
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import idxparse
import server

ROOT = Path(__file__).resolve().parent
LISTINGS = ROOT / "idx_store" / "listings"
NAMES = ROOT / "idx_store" / "names"


def code(prefix: str) -> str:
    return prefix.rstrip("-")


def load_done(path: Path, retry_errors: bool) -> set[int]:
    done: set[int] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if retry_errors and item.get("error"):
                done.discard(item["id"])
            else:
                done.add(item["id"])
    return done


def process(record: dict) -> dict:
    record_id = int(record["record_id"])
    files = []
    for attachment in server.attachments_for(str(record_id)):
        pdf, _ = server.archive_get(attachment["pdf_url"], timeout=180)
        files.append({
            "filename": attachment["filename"],
            "pdf_url": attachment["pdf_url"],
            "entries": idxparse.parse_pdf(pdf),
        })
    return {"id": record_id, "files": files}


def stats() -> None:
    total_records = total_done = total_entries = 0
    for county in server.COUNTIES:
        listing = LISTINGS / f"{code(county['prefix'])}.json"
        if not listing.exists():
            continue
        records = len(json.loads(listing.read_text(encoding="utf-8"))["records"])
        path = NAMES / f"{code(county['prefix'])}.jsonl"
        done = entries = errors = 0
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                done += 1
                errors += bool(item.get("error"))
                entries += sum(len(f["entries"]) for f in item.get("files", []))
        total_records += records; total_done += done; total_entries += entries
        if done:
            print(f"{county['name']:<16} {done:>6}/{records:<6} records  {entries:>8} entries  {errors} errors")
    print(f"{'Total':<16} {total_done:>6}/{total_records:<6} records  {total_entries:>8} entries")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", help="county prefix, e.g. PH- (repeatable)")
    parser.add_argument("--limit", type=int, help="records per county this run")
    parser.add_argument("--sample", action="store_true", help="spread --limit records evenly across each county instead of taking the first ones")
    parser.add_argument("--max-minutes", type=float, help="stop starting new records after this long")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()
    if args.stats:
        stats()
        return
    NAMES.mkdir(parents=True, exist_ok=True)
    wanted = {p.upper() if p.endswith("-") else p.upper() + "-" for p in args.only or []}
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    for county in server.COUNTIES:
        if wanted and county["prefix"] not in wanted:
            continue
        listing = LISTINGS / f"{code(county['prefix'])}.json"
        if not listing.exists():
            continue
        records = json.loads(listing.read_text(encoding="utf-8"))["records"]
        out = NAMES / f"{code(county['prefix'])}.jsonl"
        done = load_done(out, args.retry_errors)
        todo = [r for r in records if int(r["record_id"]) not in done]
        if args.limit:
            if args.sample and len(todo) > args.limit:
                step = len(todo) / args.limit
                todo = [todo[int(i * step)] for i in range(args.limit)]
            else:
                todo = todo[: args.limit]
        if not todo:
            continue
        print(f"{county['name']}: {len(todo)} records to fetch ({len(done)} already done)", flush=True)
        with out.open("a", encoding="utf-8") as sink:
            for record in todo:
                if deadline and time.monotonic() > deadline:
                    print("Time budget reached; run again to resume.")
                    return
                try:
                    result = process(record)
                except Exception as error:  # keep going; retry later with --retry-errors
                    result = {"id": int(record["record_id"]), "error": str(error)[:300]}
                sink.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                sink.flush()
                n = sum(len(f["entries"]) for f in result.get("files", []))
                print(f"  {record['call_number']}: {n} entries{' ERROR ' + result['error'] if 'error' in result else ''}", flush=True)


if __name__ == "__main__":
    main()
