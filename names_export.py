"""Build the static name index that the Pages site searches.

Reads the names.py store (<store>/<CC>/*.jsonl) plus site/data/catalogue.json
and writes, under --out:

    index.json      manifest (progress, shard sizes, privacy cutoff, ...)
    prefixes.json   {record_id: image-name prefix}
    batches.json    {record_id: [[mediaId, first image number], ...]} of the scanned
                    batch PDFs (from batches.py), so the page can open an image in the
                    Archives' viewer at the right page; see image_page()
    n/<key>.gz      name shards: gzip'd JSON array of rows
    p/<key>.gz      place shards: {place_key: [[record_id, entries], ...]}
    r/<id>.gz       every published row of one register

A row is
    [record_id, name, place, event, date, year, act, page, image]
with every value exactly as printed in the IDX PDF (the Archives publish the
data under CC BY-NC-ND 4.0, so values are not altered). To save space, an
image name that starts with its register's common prefix is stored as
"~" + the rest; the prefixes are in prefixes.json and the page restores the
full name. The page also derives, for sorting and filtering only, the same
sort_date (normalize_date) and event_code (event_code) as this file - keep
the JavaScript copies in site/index.html identical (tests/derived.json).

Search keys: every name word is folded (no diacritics, lower case) and passed
through variant_key(), which merges common Romanian/Hungarian spelling
variants (Ioan/Ion, Kovács/Covaci, Alexandru/Alecsandru). Keys of 3+ letters
file the row under the key's prefix shard; shards start at 3 letters and
split one letter deeper while larger than --max-kb (raw JSON); a key ending
in "_" holds words that end exactly at that prefix. The JavaScript copy of
variant_key in site/index.html must stay identical (tests/variants.json).

Privacy: entries dated (or, if undated, in registers ending) within the last
--privacy-years years are left out, as are records and entries listed in
privacy/exclusions.json.

    python names_export.py                                  # idx_store/names -> site/data/names
    python names_export.py --store names-data/names --out site/data/names
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from array import array
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_TOKEN = 3
SIZE_LIMIT = 900_000_000  # GitHub Pages sites may be at most 1 GB

VARIANT_RULES = [
    ("x", "cs"),
    ("cs", "ci"), ("sz", "s"), ("zs", "j"), ("gy", "g"), ("ny", "n"), ("ly", "l"),
    ("ph", "f"), ("th", "t"), ("ck", "c"), ("k", "c"), ("q", "c"), ("w", "v"), ("y", "i"),
    ("ioa", "io"),
]


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn").casefold()


def variant_key(word: str) -> str:
    """Spelling-variant key of an already folded word (a-z only)."""
    for old, new in VARIANT_RULES:
        word = word.replace(old, new)
    return re.sub(r"(.)\1+", r"\1", word)


def keys(text: str) -> set[str]:
    return {k for k in (variant_key(w) for w in re.findall(r"[a-z]+", fold(text))) if len(k) >= MIN_TOKEN}


def normalize_date(date: str, year: str) -> str:
    """ISO-like sort key where possible; else the year; else ''. Inputs seen:
    1913-05-06, 18121888 (DDMMYYYY), 00-12-1865 (DD-MM-YYYY, 00 = unknown day), #Num!."""
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


EVENT_WORDS = [("b", ("nast", "nasc", "nat", "botez")), ("m", ("casat", "cunun", "matrim")), ("d", ("dec", "mort", "inmor", "ingrop"))]
ACT_PREFIX = {"n": "b", "c": "m", "d": "d", "m": "d"}


def event_code(event: str, act: str, register_title: str) -> str:
    folded = fold(event).strip()
    for code, stems in EVENT_WORDS:
        if folded.startswith(stems) or folded in {code, {"b": "n", "m": "c", "d": "d"}[code]}:
            return code
    m = re.match(r"\s*([NCDMncdm])\s*-", act)
    if m:
        return ACT_PREFIX[m.group(1).lower()]
    title = fold(register_title)
    found = {code for code, stems in [("b", ("nascut", "naster")), ("m", ("casator", "cununa")), ("d", ("decedat", "decese", "morti"))] if any(s in title for s in stems)}
    return found.pop() if len(found) == 1 else ""


def batch_starts(batches: list) -> list[list]:
    """[[mediaId, first image number or None], ...] from batches.py's
    [mediaId, name, page count, first image] lists. The first batch starts at
    image 1; a missing start is derived from the previous batch's start + count."""
    out, previous = [], None
    for index, (media_id, _name, count, start) in enumerate(batches):
        if start is None:
            if index == 0:
                start = 1
            elif previous and previous[0] is not None and previous[1] is not None:
                start = previous[0] + previous[1]
        out.append([media_id, start])
        previous = (start, count)
    return out


def image_page(starts: list[list], image: int):
    """(batch index, page) of an image number, or None when it can't be placed
    for sure. The JavaScript copy in site/index.html must stay identical (tests/pages.json)."""
    best = None
    for index, (_media, start) in enumerate(starts):
        if start is not None and start <= image:
            best = index
    if best is None:
        return None
    if best + 1 < len(starts):
        following = starts[best + 1][1]
        if following is None or image >= following:
            return None
    return best, image - starts[best][1] + 1


def load_batches(store: Path) -> dict[int, list[list]]:
    latest: dict[int, dict] = {}
    for path in sorted(store.glob("*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item["id"] not in latest or not item.get("error"):
                latest[item["id"]] = item
    return {rid: batch_starts(item["batches"]) for rid, item in latest.items() if item.get("batches")}


def load_catalogue(path: Path) -> dict[int, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for county, record_id, call, title, years, _level in data["rows"]:
        ys = [int(y) for y in re.findall(r"\b(1[4-9]\d\d|20\d\d)\b", years)]
        out[record_id] = {"title": title, "end_year": max(ys) if ys else None}
    return out


def load_exclusions(path: Path) -> tuple[set[int], set[tuple[int, str]]]:
    if not path.exists():
        return set(), set()
    data = json.loads(path.read_text(encoding="utf-8"))
    records = {int(r) for r in data.get("records", [])}
    entries = {(int(e["record_id"]), fold(e["name"]).strip()) for e in data.get("entries", [])}
    return records, entries


def load_rows(store: Path, catalogue: dict[int, dict], cutoff: int, excluded_records: set[int],
              excluded_entries: set[tuple[int, str]]):
    """Return rows (JSON strings), their record ids, name keys, place counts, per-county progress and drop counts.

    Two passes keep memory low: the first only notes which line holds each
    register's latest usable result (the last one, unless it is an error);
    the second re-reads the files and turns just those lines into rows, one
    register at a time. Rows come out in the order registers first appear,
    as before."""
    paths = sorted(store.glob("*/*.jsonl"))
    chosen: dict[int, tuple[int, int, bool]] = {}  # record id -> (path index, line number, is error)
    county_of: dict[int, str] = {}
    last_run: dict[str, str] = {}
    for path_index, path in enumerate(paths):
        county = path.parent.name
        last_run[county] = max(last_run.get(county, ""), path.stem)
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            error = bool(item.get("error"))
            if item["id"] not in chosen or not error:
                chosen[item["id"]] = (path_index, line_no, error)
                county_of[item["id"]] = county
            del item
    wanted: dict[int, dict[int, int]] = defaultdict(dict)
    for record_id, (path_index, line_no, _error) in chosen.items():
        wanted[path_index][line_no] = record_id

    dropped = {"privacy_cutoff": 0, "excluded": 0}
    progress = {c: {"done": 0, "errors": 0, "entries": 0, "last_run": r} for c, r in last_run.items()}
    for record_id, (_p, _l, error) in chosen.items():
        stats = progress[county_of[record_id]]
        stats["done"] += 1
        stats["errors"] += error
    prefixes: dict[int, str] = {}
    places: dict[str, dict[str, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    per_record: dict[int, list[tuple[str, tuple[str, ...]]]] = {}
    for path_index, lines in wanted.items():
        text = paths[path_index].read_text(encoding="utf-8").splitlines()
        for line_no, record_id in lines.items():
            item = json.loads(text[line_no])
            per_record[record_id] = register_rows(record_id, item, catalogue.get(record_id, {}), cutoff,
                                                  excluded_records, excluded_entries, dropped, prefixes, places)
            progress[county_of[record_id]]["entries"] += len(per_record[record_id])
            del item
        del text

    rows: list[str] = []
    row_record = array("i")
    name_keys: list[tuple[str, ...]] = []
    for record_id in chosen:  # first-appearance order
        for row, nk in per_record.pop(record_id, []):
            rows.append(row)
            row_record.append(record_id)
            name_keys.append(nk)
    return rows, row_record, name_keys, places, progress, dropped, prefixes


def register_rows(record_id, item, info, cutoff, excluded_records, excluded_entries, dropped, prefixes, places):
    """One register's rows as (JSON string, name keys); also counts its place keys."""
    kept: list[tuple[dict, set[str]]] = []
    for file in item.get("files", []):
        for entry in file["entries"]:
            name = entry.get("name", "")
            nk = keys(name)
            if not nk:
                continue
            if record_id in excluded_records or (record_id, fold(name).strip()) in excluded_entries:
                dropped["excluded"] += 1
                continue
            sort_date = normalize_date(entry.get("date", ""), entry.get("year", ""))
            year = int(sort_date[:4]) if sort_date else info.get("end_year")
            if year is not None and year > cutoff:
                dropped["privacy_cutoff"] += 1
                continue
            kept.append((entry, nk))
    prefix = image_prefix([entry.get("image", "") for entry, _ in kept])
    if prefix:
        prefixes[record_id] = prefix
    out = []
    for entry, nk in kept:
        g = entry.get
        image = g("image", "")
        if prefix and image.startswith(prefix):
            image = "~" + image[len(prefix):]
        row = [record_id, g("name", ""), g("place", ""), g("event", ""), g("date", ""), g("year", ""), g("act", ""),
               g("page", ""), image]
        out.append((json.dumps(row, ensure_ascii=False, separators=(",", ":")), tuple(nk)))
        for k in keys(g("place", "")):
            places[k[:MIN_TOKEN]][k][record_id] += 1
    return out


def image_prefix(images: list[str]) -> str:
    """Common start of a register's image names, cut before the trailing digits
    (PH-FD-00006-209-5(PR6)_0002.TIFF -> PH-FD-00006-209-5(PR6)_). Empty when
    not worth it, or when any name starts with "~" (the stored marker)."""
    images = [i for i in images if i]
    if not images or any(i.startswith("~") for i in images):
        return ""
    prefix = os.path.commonprefix(images).rstrip("0123456789")
    return prefix if len(prefix) >= 6 else ""


def build(rows: list[str], toks: list, max_bytes: int) -> dict[str, list[int]]:
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


def write_gz(path: Path, text: str) -> int:
    data = gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)
    path.write_bytes(data)
    return len(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", type=Path, default=ROOT / "idx_store" / "names")
    parser.add_argument("--out", type=Path, default=ROOT / "site" / "data" / "names")
    parser.add_argument("--catalogue", type=Path, default=ROOT / "site" / "data" / "catalogue.json")
    parser.add_argument("--exclusions", type=Path, default=ROOT / "privacy" / "exclusions.json")
    parser.add_argument("--batches-store", type=Path, default=ROOT / "idx_store" / "batches",
                        help="batches.py output (scanned batch PDFs per register)")
    parser.add_argument("--privacy-years", type=int, default=100)
    parser.add_argument("--max-kb", type=int, default=3000, help="split name shards above this raw size")
    args = parser.parse_args()

    catalogue = load_catalogue(args.catalogue)
    cutoff = time.gmtime().tm_year - args.privacy_years
    excluded_records, excluded_entries = load_exclusions(args.exclusions)
    rows, row_record, name_keys, places, progress, dropped, prefixes = load_rows(
        args.store, catalogue, cutoff, excluded_records, excluded_entries)
    records = sum(p["done"] for p in progress.values())

    if args.out.exists():
        shutil.rmtree(args.out)
    for sub in ("n", "p", "r"):
        (args.out / sub).mkdir(parents=True)
    sizes = {"n": 0, "p": 0, "r": 0}

    name_manifest: dict[str, list[int]] = {}
    shards = build(rows, name_keys, args.max_kb * 1000)
    del name_keys
    for key, members in sorted(shards.items()):
        size = write_gz(args.out / "n" / f"{key}.gz", "[" + ",".join(rows[i] for i in members) + "]")
        name_manifest[key] = [len(members), size]
        sizes["n"] += size

    by_record: dict[int, list[int]] = defaultdict(list)
    for index, record_id in enumerate(row_record):
        by_record[record_id].append(index)
    register_counts = {}
    for record_id, members in by_record.items():
        sizes["r"] += write_gz(args.out / "r" / f"{record_id}.gz", "[" + ",".join(rows[i] for i in members) + "]")
        register_counts[record_id] = len(members)

    place_manifest = {}
    for prefix, table in sorted(places.items()):
        payload = {k: sorted([rid, n] for rid, n in refs.items()) for k, refs in sorted(table.items())}
        size = write_gz(args.out / "p" / f"{prefix}.gz", json.dumps(payload, separators=(",", ":")))
        place_manifest[prefix] = size
        sizes["p"] += size

    prefix_text = json.dumps({str(k): v for k, v in sorted(prefixes.items())}, ensure_ascii=False, separators=(",", ":"))
    (args.out / "prefixes.json").write_text(prefix_text, encoding="utf-8")
    sizes["prefixes"] = len(gzip.compress(prefix_text.encode("utf-8"), 9))  # served gzip'd by Pages
    registers_with_rows = set(row_record)
    batches = {rid: starts for rid, starts in load_batches(args.batches_store).items() if rid in registers_with_rows}
    batch_text = json.dumps({str(k): v for k, v in sorted(batches.items())}, separators=(",", ":"))
    (args.out / "batches.json").write_text(batch_text, encoding="utf-8")
    sizes["batches"] = len(gzip.compress(batch_text.encode("utf-8"), 9))
    total = sum(sizes.values())
    (args.out / "index.json").write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "records": records,
        "entries": len(rows),
        "dropped": dropped,
        "privacy_cutoff_year": cutoff,
        "progress": progress,
        "min_token": MIN_TOKEN,
        "format": 2,
        "columns": ["record_id", "name", "place", "event", "date", "year", "act", "page", "image"],
        "shards": name_manifest,
        "places": place_manifest,
        "registers": register_counts,
    }, separators=(",", ":")), encoding="utf-8")

    catalogue_total = len(catalogue) or 1
    projected = total * catalogue_total / max(records, 1)
    print(f"{len(rows)} entries from {records} registers ({dropped['privacy_cutoff']} after the {cutoff} privacy cutoff "
          f"and {dropped['excluded']} excluded left out)")
    print("size MB: " + ", ".join(f"{k}={v / 1e6:.1f}" for k, v in sizes.items())
          + f", total={total / 1e6:.1f}; projected for all {catalogue_total} registers ≈ {projected / 1e6:.0f}")
    if total > SIZE_LIMIT:
        sys.exit(f"Output is {total / 1e6:.0f} MB, over the {SIZE_LIMIT / 1e6:.0f} MB budget for GitHub Pages")
    if projected > SIZE_LIMIT:
        print(f"::warning::Projected full index ≈ {projected / 1e6:.0f} MB exceeds the {SIZE_LIMIT / 1e6:.0f} MB budget")


if __name__ == "__main__":
    main()
