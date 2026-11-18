"""Build the static name-index shards that the Pages site searches.

Reads the names.py store (<store>/<CC>/*.jsonl) and writes:

    <out>/index.json   manifest: {"shards": {key: [rows, gz_bytes]}, ...}
    <out>/<key>.gz     gzip'd JSON array of rows

A row is [record_id, name, place, event, date, act, page, image_no]; the page
joins record_id to catalogue.json for county, call number and title.

Every distinct folded name token of 3+ letters files the row under that
token's prefix shard, so a search downloads a single shard. Shards start at 3
letters and split one letter deeper while larger than --max-kb (raw JSON); a
key ending in "_" holds tokens that end exactly at that prefix.

    python names_export.py                                  # idx_store/names -> site/data/names
    python names_export.py --store names-data/names --out _site/data/names
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_TOKEN = 3


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn").casefold()


def tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", fold(name)) if len(t) >= MIN_TOKEN}


def normalize_date(date: str, year: str) -> str:
    """ISO date where possible; else the year; else ''. Inputs seen: 1913-05-06,
    18121888 (DDMMYYYY), 00-12-1865 (DD-MM-YYYY, 00 = unknown day), #Num!."""
    date = date.strip()
    if re.fullmatch(r"\d{4}(-\d\d){0,2}", date):
        return date
    m = re.fullmatch(r"(\d\d)[-./]?(\d\d)[-./]?(\d{4})", date)
    if m:
        day, month, yyyy = m.groups()
        parts = [yyyy] + ([month] if month != "00" else []) + ([day] if month != "00" and day != "00" else [])
        return "-".join(parts)
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", date)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", year or "")
    return m.group(1) if m else ""


def compact(record_id: int, entry: dict) -> list:
    date = normalize_date(entry.get("date", ""), entry.get("year", ""))
    image = re.search(r"(\d+)\D*$", entry.get("image", ""))
    return [record_id, entry.get("name", ""), entry.get("place", ""), entry.get("event", ""), date,
            entry.get("act", ""), entry.get("page", ""), int(image.group(1)) if image else 0]


def load_rows(store: Path) -> tuple[list[str], list[set[str]], int]:
    latest: dict[int, dict] = {}
    for path in sorted(store.glob("*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item["id"] not in latest or not item.get("error"):
                latest[item["id"]] = item
    rows: list[str] = []
    toks: list[set[str]] = []
    for record_id, item in latest.items():
        for file in item.get("files", []):
            for entry in file["entries"]:
                t = tokens(entry.get("name", ""))
                if t:
                    rows.append(json.dumps(compact(record_id, entry), ensure_ascii=False, separators=(",", ":")))
                    toks.append(t)
    return rows, toks, len(latest)


def build(rows: list[str], toks: list[set[str]], max_bytes: int) -> dict[str, list[int]]:
    """Return shard key -> row indexes, splitting oversized prefixes."""
    pending: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for index, ts in enumerate(toks):
        for t in ts:
            pending[t[:MIN_TOKEN]].append((index, t))
    shards: dict[str, list[int]] = {}
    while pending:
        key, members = pending.popitem()
        size = sum(len(rows[i]) + 1 for i, _ in members)
        if size <= max_bytes:
            shards[key] = sorted({i for i, _ in members})
            continue
        depth = len(key) + 1
        exact = [i for i, t in members if len(t) < depth]
        if exact:
            shards[key + "_"] = sorted(set(exact))
        for i, t in members:
            if len(t) >= depth:
                pending[t[:depth]].append((i, t))
    return shards


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", type=Path, default=ROOT / "idx_store" / "names")
    parser.add_argument("--out", type=Path, default=ROOT / "site" / "data" / "names")
    parser.add_argument("--max-kb", type=int, default=3000, help="split shards above this raw size")
    args = parser.parse_args()
    rows, toks, records = load_rows(args.store)
    shards = build(rows, toks, args.max_kb * 1000)
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    manifest: dict[str, list[int]] = {}
    total = 0
    for key in sorted(shards):
        payload = ("[" + ",".join(rows[i] for i in shards[key]) + "]").encode("utf-8")
        data = gzip.compress(payload, compresslevel=9, mtime=0)
        (args.out / f"{key}.gz").write_bytes(data)
        manifest[key] = [len(shards[key]), len(data)]
        total += len(data)
    (args.out / "index.json").write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "records": records,
        "entries": len(rows),
        "min_token": MIN_TOKEN,
        "columns": ["record_id", "name", "place", "event", "date", "act", "page", "image"],
        "shards": manifest,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"{len(rows)} entries from {records} records -> {len(shards)} shards, {total / 1e6:.1f} MB gzip in {args.out}")


if __name__ == "__main__":
    main()
