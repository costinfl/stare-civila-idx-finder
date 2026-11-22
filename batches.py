"""Polite, resumable probe of each register's scanned batch PDFs.

A register's scans are split over batch PDFs (…_01.PDF, …_02.PDF, …) next to
its IDX file. An IDX "Imagine PDF" value such as …_0015.TIFF is image 15 of
the register; to open it in the Archives' own pdf.js viewer at the right
batch and page, the site needs each batch's page count. The Archives' server
ignores Range requests, so this script streams each batch only until it has
read the root page tree (/Type/Pages … /Count N) and the first bookmark,
both written at the start by PDFsharp, then closes the connection. Bookmarks
are named after the scans (…_0201.TIFF), so the first one gives the image
number the batch starts with: image g is in the last batch whose start is
<= g, at page g - start + 1.

All requests share the 1.25 s pace of server.archive_get. Results go to
<store>/<CC>/<run>.jsonl, one line per register:
    {"id": 13640365, "batches": [[399659, "…_01.PDF", 200, 1], [399660, "…_02.PDF", 216, 201], [399661, "…_03.PDF", 125, 417]]}
    (mediaId, file name, page count, image number of its first page; null when not found)

    python batches.py --only IL- --limit 5            # sample
    python batches.py --store names-data/batches --max-minutes 55
    python batches.py --stats
    python batches.py --retry-errors        # once everything has been tried
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import names
import server

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "idx_store" / "batches"
RUN_STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
HEAD_LIMIT = 64 * 1024  # PDFsharp writes both within ~16 KB; other producers give nothing usable
ANCHOR = re.compile(r"<a\b(?=[^>]*\bclass=['\"][^'\"]*\bopen_PDF_viewer\b)[^>]*\bhref=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", re.I | re.S)
TITLE = re.compile(r"class=['\"]file_title['\"]\s+data-title=['\"]([^'\"]+)['\"]", re.I)
COUNT = re.compile(rb"/Count\s+(\d+)")
BOOKMARK = re.compile(rb"/Title\s*(?:<([0-9A-Fa-f\s]+)>|\(([^)]*)\))")


def wait_turn() -> None:
    """Share server.archive_get's pacing (one request per REQUEST_INTERVAL)."""
    with server._rate_lock:
        wait_for = server.REQUEST_INTERVAL - (time.monotonic() - server._last_request)
        if wait_for > 0:
            time.sleep(wait_for)
        server._last_request = time.monotonic()


def batch_pdfs(record_id: int) -> list[tuple[int, str]]:
    """(mediaId, filename) of the register's PDFs other than the IDX, in name order."""
    page, _ = server.archive_get(f"{server.ARCHIVE}/cota/?cid={record_id}")
    found = []
    for href, block in ANCHOR.findall(page.decode("utf-8", errors="replace")):
        title = TITLE.search(block)
        if not title:
            continue
        name = html.unescape(title.group(1))
        if not name.casefold().endswith(".pdf") or "idx" in name.casefold():
            continue
        media = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(href)).query).get("mediaId", [""])[0]
        if media.isdigit():
            found.append((int(media), name))
    return sorted(found, key=lambda b: b[1].casefold())


def root_page_count(head: bytes) -> int | None:
    """/Count of the root /Type/Pages object (the one without /Parent)."""
    for chunk in head.split(b"endobj"):
        if re.search(rb"/Type\s*/Pages\b", chunk) and b"/Parent" not in chunk:
            m = COUNT.search(chunk)
            if m:
                return int(m.group(1))
    return None


def first_image(head: bytes) -> int | None:
    """Image number in the first bookmark named after a scan (…_0201.TIFF -> 201)."""
    for m in BOOKMARK.finditer(head):
        if m.group(1) is not None:
            raw = bytes.fromhex(re.sub(rb"\s", b"", m.group(1)).decode())
            title = raw.decode("utf-16") if raw[:2] in (b"\xfe\xff", b"\xff\xfe") else raw.decode("latin-1")
        else:
            title = m.group(2).decode("latin-1")
        number = re.search(r"_(\d+)(?:\.\w+)?\s*$", title)
        if number:
            return int(number.group(1))
    return None


def page_head(record_id: int, media_id: int) -> tuple[int | None, int | None, int]:
    """Stream the batch until its page count and first image are known; returns (count, start, bytes read)."""
    wait_turn()
    url = f"{server.ARCHIVE}/api/media/file?id={record_id}&type=fisiere&mediaId={media_id}"
    request = urllib.request.Request(url, headers={"User-Agent": server.USER_AGENT, "Accept": "*/*"})
    head = b""
    with urllib.request.urlopen(request, timeout=60) as response:
        while len(head) < HEAD_LIMIT:
            chunk = response.read(16 * 1024)
            if not chunk:
                break
            head += chunk
            count, start = root_page_count(head), first_image(head)
            if count is not None and start is not None:
                return count, start, len(head)  # leaving the with-block closes the connection
    return root_page_count(head), first_image(head), len(head)


def probe(record_id: int) -> dict:
    """The first batch always starts at image 1, so only batches 2..n are read."""
    batches, read = [], 0
    for index, (media_id, name) in enumerate(batch_pdfs(record_id)):
        if index == 0:
            batches.append([media_id, name, None, 1])
            continue
        count, start, n = page_head(record_id, media_id)
        read += n
        batches.append([media_id, name, count, start])
    return {"id": record_id, "batches": batches, "bytes": read}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument("--only", action="append", help="county prefix, e.g. IL- (repeatable)")
    parser.add_argument("--ids", type=int, nargs="*", help="specific record ids (pilot)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample", action="store_true", help="spread --limit evenly over each county")
    parser.add_argument("--max-minutes", type=float)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--retry-errors", action="store_true", help="probe again registers whose last attempt failed")
    args = parser.parse_args()

    catalogue = names.catalogue_by_county()
    if args.stats:
        all_done = all_total = 0
        for county in server.COUNTIES:
            done = {}
            for path in sorted((args.store / names.code(county["prefix"])).glob("*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    done[item["id"]] = item
            total = len(catalogue.get(county["prefix"], []))
            all_done += len(done); all_total += total
            if done:
                errors = sum(1 for i in done.values() if i.get("error"))
                print(f"{county['name']:<16} {len(done):>6}/{total:<6} registers  {errors} errors")
        print(f"{'Total':<16} {all_done:>6}/{all_total:<6} registers")
        return

    wanted = {p.upper() if p.endswith("-") else p.upper() + "-" for p in args.only or []}
    deadline = time.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    for county in server.COUNTIES:
        if wanted and county["prefix"] not in wanted:
            continue
        records = [int(r["record_id"]) for r in catalogue.get(county["prefix"], [])]
        if args.ids:
            records = [r for r in records if r in set(args.ids)]
        folder = args.store / names.code(county["prefix"])
        done = set()
        for path in folder.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("error") and args.retry_errors:
                    done.discard(item["id"])
                else:
                    done.add(item["id"])
        todo = [r for r in records if r not in done]
        if args.limit and len(todo) > args.limit:
            todo = [todo[int(i * len(todo) / args.limit)] for i in range(args.limit)] if args.sample else todo[: args.limit]
        if not todo:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{RUN_STAMP}.jsonl").open("a", encoding="utf-8") as sink:
            for record_id in todo:
                if deadline and time.monotonic() > deadline:
                    print("Time budget reached; run again to resume.")
                    return
                try:
                    result = probe(record_id)
                except Exception as error:
                    result = {"id": record_id, "error": str(error)[:300]}
                sink.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                sink.flush()
                print(f"  {county['name']} {record_id}: {[(b[1][-7:], b[2], b[3]) for b in result.get('batches', [])]} "
                      f"{result.get('bytes', 0) // 1024} KB{' ERROR ' + result['error'] if 'error' in result else ''}", flush=True)


if __name__ == "__main__":
    main()
