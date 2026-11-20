"""Build site/data/genealogy.json: each county's link to the Archives' own
"Genealogie - Registre parohiale si de stare civila" predefined search.

Those searches list every parish and civil-status register of a county (most
have no IDX name index, only scans), so the site links to them instead of
copying them. One request to the predefined-searches page; whitespace inside
the encoded search values is trimmed (one county's link had a trailing space
that broke it).

    python genealogy_links.py

Output: {"source": url, "counties": {"PH-": "https://descopera.arhivelenationale.ro/cautare-campuri/?ts=...", ...}}
"""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.parse
import unicodedata
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site" / "data" / "genealogy.json"
PAGE = server.ARCHIVE + "/cautari-predefinite/"


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn").casefold().strip()


def clean(url: str) -> str:
    """Trim whitespace in the base64-encoded search terms and re-encode them."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    fixed = []
    for key, value in query:
        if key == "ts":
            terms = json.loads(base64.b64decode(value + "=" * (-len(value) % 4)))
            for term in terms:
                term["Value"] = str(term.get("Value", "")).strip()
            value = base64.b64encode(json.dumps(terms, separators=(",", ":")).encode()).decode()
        fixed.append((key, value))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(fixed)))


def main() -> None:
    page, _ = server.archive_get(PAGE)
    source = page.decode("utf-8", errors="replace")
    start = source.find("Genealogie")
    section = source[start:source.find("</table>", start)]
    by_name = {fold(c["name"]): c["prefix"] for c in server.COUNTIES}
    by_name[fold("Municipiul București")] = "MB-"
    links: dict[str, str] = {}
    for href, name in re.findall(r'href="([^"]+cautare-campuri[^"]+)">\s*([^<]+?)\s*</a>', section):
        prefix = by_name.get(fold(html.unescape(name)))
        if prefix and prefix not in links:
            links[prefix] = clean(html.unescape(href))
    missing = [c["name"] for c in server.COUNTIES if c["prefix"] not in links]
    OUT.write_text(json.dumps({"source": PAGE, "counties": dict(sorted(links.items()))}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"{len(links)} county links -> {OUT.relative_to(ROOT)}" + (f"; missing: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
