"""Local UI and proxy for collecting Romanian National Archives IDX PDFs."""

from __future__ import annotations

import html
import io
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath


ARCHIVE = "https://descopera.arhivelenationale.ro"
AJAX = ARCHIVE + "/wp-admin/admin-ajax.php?action=get_advanced_search"
COUNT_HINT = "56435"
PAGE_SIZE = 100
USER_AGENT = "StareCivilaIDXFinder/1.1 (+https://github.com/costinfl/stare-civila-idx-finder)"
REQUEST_INTERVAL = 1.25
_rate_lock = threading.Lock()
_last_request = 0.0

# Archive codes used in the Cotă field. București is a municipality, searched
# once with its archive code MB, although the navigation tree has two branches.
COUNTIES = [
    {"name": "Alba", "prefix": "AB-"},
    {"name": "Arad", "prefix": "AR-"},
    {"name": "Argeș", "prefix": "AG-"},
    {"name": "Bacău", "prefix": "BC-"},
    {"name": "Bihor", "prefix": "BH-"},
    {"name": "Bistrița-Năsăud", "prefix": "BN-"},
    {"name": "Botoșani", "prefix": "BT-"},
    {"name": "Brăila", "prefix": "BR-"},
    {"name": "Brașov", "prefix": "BV-"},
    {"name": "București", "prefix": "MB-"},
    {"name": "Buzău", "prefix": "BZ-"},
    {"name": "Călărași", "prefix": "CL-"},
    {"name": "Caraș-Severin", "prefix": "CS-"},
    {"name": "Cluj", "prefix": "CJ-"},
    {"name": "Constanța", "prefix": "CT-"},
    {"name": "Covasna", "prefix": "CV-"},
    {"name": "Dâmbovița", "prefix": "DB-"},
    {"name": "Dolj", "prefix": "DJ-"},
    {"name": "Galați", "prefix": "GL-"},
    {"name": "Giurgiu", "prefix": "GR-"},
    {"name": "Gorj", "prefix": "GJ-"},
    {"name": "Harghita", "prefix": "HR-"},
    {"name": "Hunedoara", "prefix": "HD-"},
    {"name": "Ialomița", "prefix": "IL-"},
    {"name": "Iași", "prefix": "IS-"},
    {"name": "Ilfov", "prefix": "IF-"},
    {"name": "Maramureș", "prefix": "MM-"},
    {"name": "Mehedinți", "prefix": "MH-"},
    {"name": "Mureș", "prefix": "MS-"},
    {"name": "Neamț", "prefix": "NT-"},
    {"name": "Olt", "prefix": "OT-"},
    {"name": "Prahova", "prefix": "PH-"},
    {"name": "Sălaj", "prefix": "SJ-"},
    {"name": "Satu Mare", "prefix": "SM-"},
    {"name": "Sibiu", "prefix": "SB-"},
    {"name": "Suceava", "prefix": "SV-"},
    {"name": "Teleorman", "prefix": "TR-"},
    {"name": "Timiș", "prefix": "TM-"},
    {"name": "Tulcea", "prefix": "TL-"},
    {"name": "Vâlcea", "prefix": "VL-"},
    {"name": "Vaslui", "prefix": "VS-"},
    {"name": "Vrancea", "prefix": "VN-"},
]
COUNTY_BY_PREFIX = {item["prefix"]: item for item in COUNTIES}


def archive_get(url: str, timeout: int = 60) -> tuple[bytes, str]:
    # Keep every request to the Archives host at a measured, shared pace.
    global _last_request
    with _rate_lock:
        wait_for = REQUEST_INTERVAL - (time.monotonic() - _last_request)
        if wait_for > 0:
            time.sleep(wait_for)
        _last_request = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def parse_search_rows(fragment: str) -> list[dict[str, str]]:
    """Parse the site's compact list-row HTML without third-party packages."""
    rows: list[dict[str, str]] = []
    row_start = re.compile(r"<div\s+data-idx=['\"](\d+)['\"]\s+class=['\"]tblRow['\"]>", re.I)
    matches = list(row_start.finditer(fragment))
    for index, match in enumerate(matches):
        # Bound a row by the next row's start rather than counting closing divs:
        # the thumb and title cells also contain nested markup.
        end = matches[index + 1].start() if index + 1 < len(matches) else len(fragment)
        block = fragment[match.end():end]

        title_cell = re.search(r"class=['\"][^'\"]*\btblCol_title\b[^'\"]*['\"]>(.*?)</div>", block, re.I | re.S)
        if not title_cell:
            continue
        title_html = title_cell.group(1)
        title_match = re.search(r"<a\s+href=['\"]([^'\"]*?/cota/\?cid=\d+)['\"][^>]*>(.*?)</a>", title_html, re.I | re.S)
        if not title_match:
            continue
        call_match = re.search(r"Cota UD:</b>\s*(.*?)(?:<|$)", title_html, re.I | re.S)
        level_match = re.search(r"class=['\"][^'\"]*\btblCol_nivel\b[^'\"]*['\"]>(.*?)</div>", block, re.I | re.S)
        date_match = re.search(r"class=['\"][^'\"]*\btblCol_date_extreme\b[^'\"]*['\"]>(.*?)</div>", block, re.I | re.S)

        def plain(value: str) -> str:
            return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()

        href = urllib.parse.urljoin(ARCHIVE, html.unescape(title_match.group(1)))
        rows.append({
            "record_id": match.group(1),
            "title": plain(title_match.group(2)),
            "call_number": plain(call_match.group(1)) if call_match else "",
            "description_level": plain(level_match.group(1)) if level_match else "",
            "date_extremes": plain(date_match.group(1)) if date_match else "",
            "record_url": href,
        })
    return rows


def search_url(prefix: str, title_term: str, pdf_text: str, page: int) -> str:
    terms: list[tuple[str, str]] = [
        ("1", "Contains", title_term),
        ("2", "StartsWith", prefix),
    ]
    if pdf_text:
        terms.append(("-999005", "Contains", pdf_text))
    params: list[tuple[str, str]] = []
    for index, (field_id, operator, value) in enumerate(terms):
        params.extend([
            (f"searchParams[ts][{index}][FieldId]", field_id),
            (f"searchParams[ts][{index}][Negate]", "false"),
            (f"searchParams[ts][{index}][Operator]", operator),
            (f"searchParams[ts][{index}][Value]", value),
        ])
    params.extend([
        ("searchParams[pg]", str(page)),
        ("searchParams[pgs]", str(PAGE_SIZE)),
        ("searchParams[so]", "AND"),
        ("searchParams[sm]", "0"),
        ("searchParams[srt]", "0"),
        ("searchParams[sro]", "1"),
        ("searchParams[tabletype]", "list"),
        ("searchParams[count]", COUNT_HINT),
    ])
    return AJAX + "&" + urllib.parse.urlencode(params)


def search_page(prefix: str, title_term: str, pdf_text: str, page: int) -> list[dict[str, str]]:
    payload, _ = archive_get(search_url(prefix, title_term, pdf_text, page))
    response = json.loads(payload.decode("utf-8-sig"))
    if not response.get("success"):
        raise RuntimeError("Archive search did not return success")
    fragment = response.get("data", {}).get("htmlResult", "")
    return parse_search_rows(fragment)


def attachments_for(record_id: str) -> list[dict[str, str]]:
    page, _ = archive_get(f"{ARCHIVE}/cota/?cid={record_id}")
    source = page.decode("utf-8", errors="replace")
    attachments: list[dict[str, str]] = []
    # Match the viewer anchor itself. Its file_item wrapper contains nested
    # divs, so stopping at the first </div> would truncate before file_title.
    pattern = re.compile(
        r"<a\b(?=[^>]*\bclass=['\"][^'\"]*\bopen_PDF_viewer\b)[^>]*\bhref=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        re.I | re.S,
    )
    for match in pattern.finditer(source):
        link = match.group(1)
        block = match.group(2)
        filename_match = re.search(r"class=['\"]file_title['\"]\s+data-title=['\"]([^'\"]+)['\"]", block, re.I)
        if not filename_match:
            continue
        filename = html.unescape(filename_match.group(1))
        if "idx" not in filename.casefold() or not filename.casefold().endswith(".pdf"):
            continue
        size_match = re.search(r"\[\s*([^\]]+)\s*\]", block)
        attachments.append({
            "filename": filename,
            "size": plain_text(size_match.group(1)) if size_match else "",
            "pdf_url": urllib.parse.urljoin(ARCHIVE, html.unescape(link)),
        })
    return attachments


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "DecoperaArhiveIndex/1.0"

    def send_bytes(self, status: int, data: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: int, payload: dict) -> None:
        self.send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/browse", "/browse.html"):
            page_name = "browse.html" if path in ("/browse", "/browse.html") else "index.html"
            try:
                with open(page_name, "rb") as page:
                    self.send_bytes(200, page.read(), "text/html; charset=utf-8")
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/counties":
            self.send_json(200, {"counties": COUNTIES})
        else:
            self.send_json(404, {"error": "Not found"})

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("Request too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/search-page":
                self.handle_search_page(body)
            elif path == "/api/attachments":
                self.handle_attachments(body)
            elif path == "/api/download-zip":
                self.handle_zip(body)
            else:
                self.send_json(404, {"error": "Not found"})
        except Exception as error:  # Return upstream/network errors to the UI.
            self.send_json(502, {"error": str(error)})

    def handle_search_page(self, body: dict) -> None:
        prefix = body.get("prefix", "")
        if prefix not in COUNTY_BY_PREFIX:
            self.send_json(400, {"error": "Unknown county prefix"})
            return
        title_term = str(body.get("title_term", "stare civila")).strip()
        pdf_text = str(body.get("pdf_text", "")).strip()
        cota_prefix = str(body.get("cota_prefix", "")).strip().upper() or prefix
        page = max(1, min(int(body.get("page", 1)), 20000))
        if not cota_prefix.startswith(prefix) or len(cota_prefix) > 100:
            self.send_json(400, {"error": "The Cotă prefix must begin with the selected county code"})
            return
        if not title_term:
            self.send_json(400, {"error": "Enter a title search term"})
            return
        rows = search_page(cota_prefix, title_term, pdf_text, page)
        self.send_json(200, {"rows": rows, "page": page, "page_size": PAGE_SIZE})

    def handle_attachments(self, body: dict) -> None:
        rows = body.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 10:
            self.send_json(400, {"error": "Request up to 10 records at a time"})
            return
        output = []
        for record in rows:
            record_id = str(record.get("record_id", ""))
            if not record_id.isdigit():
                continue
            attachments = attachments_for(record_id)
            if attachments:
                output.append({**record, "attachments": attachments})
        self.send_json(200, {"records": output})

    def handle_zip(self, body: dict) -> None:
        files = body.get("files", [])
        if not isinstance(files, list) or not files or len(files) > 1000:
            self.send_json(400, {"error": "Select between 1 and 1,000 IDX PDFs"})
            return
        archive = io.BytesIO()
        manifest: list[str] = []
        used_names: set[str] = set()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zipped:
            for item in files:
                url = str(item.get("pdf_url", ""))
                parsed = urllib.parse.urlparse(url)
                if parsed.scheme != "https" or parsed.netloc != "descopera.arhivelenationale.ro" or not parsed.path.startswith("/api/media/file"):
                    continue
                filename = PurePosixPath(str(item.get("filename", "index.pdf"))).name
                filename = re.sub(r"[^\w.() -]+", "_", filename, flags=re.UNICODE).strip(" .") or "index.pdf"
                archive_name = filename
                suffix = 2
                while archive_name.casefold() in used_names:
                    stem, dot, ext = filename.rpartition(".")
                    archive_name = f"{stem or filename}_{suffix}{dot}{ext}" if dot else f"{filename}_{suffix}"
                    suffix += 1
                used_names.add(archive_name.casefold())
                data, _ = archive_get(url, timeout=180)
                zipped.writestr(archive_name, data)
                manifest.append(f"{archive_name}\t{item.get('record_url', '')}\t{item.get('call_number', '')}")
            if manifest:
                zipped.writestr("INDEX.txt", "Filename\tRecord URL\tCall number\n" + "\n".join(manifest) + "\n")
        self.send_bytes(200, archive.getvalue(), "application/zip", {"Content-Disposition": 'attachment; filename="stare-civila-idx-finder-files.zip"'})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{threading.current_thread().name}] {self.address_string()} - {fmt % args}")


def main() -> None:
    host = "127.0.0.1"
    port = 8765
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Open http://{host}:{port} in your browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
