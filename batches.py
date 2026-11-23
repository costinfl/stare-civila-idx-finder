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

All requests share the 1.25 s pace of server.archive_get: request starts are
at least 1.25 s apart. The server takes 3-5 s to answer each one, so with
--workers N up to N registers are probed at once (each register's own
requests stay sequential); the pace of new requests is unchanged. When the
server answers with a 5xx error or times out, the register is recorded as an
error (left for a later --retry-errors run), every request pauses for 30 s,
and only one request runs at a time until the server has been quiet for 10
minutes. Results go to
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
import socket
import ssl
import urllib.error
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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


PAUSE_SECONDS = 30       # after a server error, no request starts for this long
QUIET_SECONDS = 10 * 60  # error-free time before going back to --workers requests at once


class Gate:
    """Shared limit on open requests that backs off when the server struggles."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.workers = self.allowed = 1
        self.active = 0
        self.pause_until = 0.0
        self.last_error = None

    def configure(self, workers: int) -> None:
        with self.cond:
            self.workers = self.allowed = max(1, workers)

    def request(self, fn, pace: bool = True):
        with self.cond:
            while True:
                now = time.monotonic()
                if self.allowed < self.workers and self.last_error is not None and now - self.last_error >= QUIET_SECONDS:
                    self.allowed = self.workers
                    print(f"Quiet for {QUIET_SECONDS // 60} min; back to {self.workers} at once.", flush=True)
                    self.cond.notify_all()
                if self.active < self.allowed and now >= self.pause_until:
                    break
                self.cond.wait(timeout=max(0.05, min(1.0, self.pause_until - now)))
            self.active += 1
        try:
            if pace:  # server.archive_get spaces its own requests
                wait_turn()
            return fn()
        except Exception as error:
            if transient(error):
                with self.cond:
                    self.last_error = time.monotonic()
                    self.pause_until = self.last_error + PAUSE_SECONDS
                    if self.allowed != 1 and self.workers > 1:
                        print(f"Server error ({error}); pausing {PAUSE_SECONDS} s, 1 request at a time.", flush=True)
                    self.allowed = 1
            raise
        finally:
            with self.cond:
                self.active -= 1
                self.cond.notify_all()


GATE = Gate()


def transient(error: Exception) -> bool:
    """Signs the server is struggling: 5xx, timeouts, dropped or failed connections."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500
    return isinstance(error, (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionError))


def fetch(fn, pace: bool = True):
    """Run one request through the gate. Failures are not retried here: the
    register is recorded as an error and refreshed by a later --retry-errors run."""
    return GATE.request(fn, pace)


def batch_pdfs(record_id: int) -> list[tuple[int, str]]:
    """(mediaId, filename) of the register's PDFs other than the IDX, in name order."""
    page, _ = fetch(lambda: server.archive_get(f"{server.ARCHIVE}/cota/?cid={record_id}"), pace=False)
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
    url = f"{server.ARCHIVE}/api/media/file?id={record_id}&type=fisiere&mediaId={media_id}"

    def read() -> tuple[int | None, int | None, int]:
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

    return fetch(read)

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
    if len(batches) > 1 and any(b[3] is None for b in batches[1:]):
        # Some PDFs have page counts but no bookmarks: then later starts are
        # derived from the counts, which needs the first batch's count too.
        count, _start, n = page_head(record_id, batches[0][0])
        read += n
        batches[0][2] = count
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
    parser.add_argument("--workers", type=int, default=1,
                        help="registers probed at once (request starts stay >= 1.25 s apart)")
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
            GATE.configure(args.workers)
            if not crawl(todo, sink, county["name"], deadline, max(1, args.workers)):
                print("Time budget reached; run again to resume.")
                return


def safe_probe(record_id: int) -> dict:
    try:
        return probe(record_id)
    except Exception as error:
        return {"id": record_id, "error": str(error)[:300]}


def crawl(todo: list[int], sink, county_name: str, deadline: float | None, workers: int) -> bool:
    """Probe registers, at most `workers` at once; False if the time budget ran out.
    No new register starts after the deadline; those already started finish."""
    lock = threading.Lock()

    def record(result: dict) -> None:
        with lock:
            sink.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
            sink.flush()
            print(f"  {county_name} {result['id']}: {[(b[1][-7:], b[2], b[3]) for b in result.get('batches', [])]} "
                  f"{result.get('bytes', 0) // 1024} KB{' ERROR ' + result['error'] if 'error' in result else ''}", flush=True)

    pending, queue, finished = set(), iter(todo), True
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            while len(pending) < workers:
                if deadline and time.monotonic() > deadline:
                    finished = False
                    break
                record_id = next(queue, None)
                if record_id is None:
                    break
                pending.add(pool.submit(safe_probe, record_id))
            if not pending:
                return finished
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                record(future.result())
            if not finished and not pending:
                return False

if __name__ == "__main__":
    main()
