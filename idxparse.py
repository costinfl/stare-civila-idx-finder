"""Extract name-index entries from IDX PDFs (Microsoft Access table reports).

Uses poppler's `pdftotext -tsv` for word coordinates: on Windows install
poppler (e.g. `winget install poppler` or conda), on Debian/Ubuntu
`apt install poppler-utils`.

Each page repeats a header row (Nume, Localitate (adresa), Tip eveniment,
Data eveniment, An act, Nr act, Pag. registr, Imagine PDF). Words are assigned
to the column whose header starts nearest to their left. Long names and image
filenames wrap onto continuation lines that only touch those two columns, so
a new entry starts on any line with text in another column.
"""

from __future__ import annotations

import csv
import io
import subprocess

import re

# Header words seen across counties, e.g. "Localitate (adresa)" or
# "Localitatea(Adresa)", "Pag. registr" or "Pagina registru", "Imagine PDF" or
# "ImaginePDF", and "An act" + "Nr act" or one merged "An act/Nr act" column.
HEADER_PATTERNS = [
    (re.compile(r"^Nume$"), "name"),
    (re.compile(r"^Localitate"), "place"),
    (re.compile(r"^Tip$"), "event"),
    (re.compile(r"^Data$"), "date"),
    (re.compile(r"^Pag(\.|ina)"), "page"),
    (re.compile(r"^Imagine"), "image"),
]
FOOTER = re.compile(r"^(Page|Pagina) \d+ (of|din) \d+$")
WRAPPING = {"name", "place", "image"}  # columns whose text can wrap onto extra lines
LINE_TOLERANCE = 3.0
COLUMN_SLACK = 18.0  # data can start left of its header (13-16 pt in Caraș-Severin)


def words_by_page(pdf: bytes) -> list[list[tuple[float, float, str]]]:
    out = subprocess.run(["pdftotext", "-tsv", "-", "-"], input=pdf, capture_output=True, check=True).stdout
    pages: list[list[tuple[float, float, str]]] = []
    widths: dict[tuple[int, float, float], float] = {}
    for row in csv.DictReader(io.StringIO(out.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "1":
            pages.append([])
        elif row["level"] == "5" and pages:
            word = (float(row["left"]), float(row["top"]), row["text"])
            pages[-1].append(word)
            widths[(len(pages), word[0], word[1])] = float(row["width"])
    words_by_page.widths = widths  # used only to split merged header words
    return pages


def header_columns(words: list[tuple[float, float, str]], page_no: int = 0) -> tuple[list[tuple[float, str]], float] | None:
    """Find the header row on a page and return [(x, key)] plus its top."""
    nume = [w for w in words if w[2] == "Nume"]
    if not nume:
        return None
    top = min(w[1] for w in nume)
    line = sorted((w for w in words if abs(w[1] - top) <= LINE_TOLERANCE), key=lambda w: w[0])
    columns: dict[str, float] = {}
    for i, (x, y, text) in enumerate(line):
        following = line[i + 1][2] if i + 1 < len(line) else ""
        for pattern, key in HEADER_PATTERNS:
            if pattern.search(text) and key not in columns:
                columns[key] = x
        if text == "An" and following.startswith("act"):
            columns.setdefault("year", x)
        elif text == "Nr" and following.startswith("act"):
            columns.setdefault("act", x)
        elif text.endswith("An") and len(text) > 2 and following.startswith("act"):
            # Merged "evenimentAn": the act column starts at the "An".
            width = getattr(words_by_page, "widths", {}).get((page_no, x, y))
            if width:
                columns.setdefault("act", x + width * (len(text) - 2) / len(text))
    # A merged "An act/Nr act" column holds act identifiers such as N-1899-1.
    if "year" in columns and "act" not in columns and any(w[2].startswith("act/") for w in line):
        columns["act"] = columns.pop("year")
    ordered = sorted((x, key) for key, x in columns.items())
    return (ordered, top) if len(ordered) >= 3 else None


def column_for(x: float, columns: list[tuple[float, str]]) -> str:
    key = columns[0][1]
    previous = None
    for start, name in columns:
        slack = COLUMN_SLACK if previous is None else min(COLUMN_SLACK, 0.4 * (start - previous))
        if x + slack >= start:
            key = name
        previous = start
    return key


def parse_pdf(pdf: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    columns: list[tuple[float, str]] | None = None
    for page_no, words in enumerate(words_by_page(pdf), start=1):
        found = header_columns(words, page_no)
        header_top = -1.0
        if found:
            columns, header_top = found
        if not columns:
            continue
        body = sorted((w for w in words if w[1] > header_top + LINE_TOLERANCE), key=lambda w: (w[1], w[0]))
        lines: list[list[tuple[float, float, str]]] = []
        for word in body:
            if lines and abs(word[1] - lines[-1][0][1]) <= LINE_TOLERANCE:
                lines[-1].append(word)
            else:
                lines.append([word])
        current: dict[str, list[str]] | None = None
        for line in lines:
            if FOOTER.match(" ".join(w[2] for w in sorted(line))):
                continue
            cells: dict[str, list[str]] = {}
            for x, _, text in sorted(line):
                cells.setdefault(column_for(x, columns), []).append(text)
            starts_entry = current is None or any(key not in WRAPPING for key in cells)
            if starts_entry:
                if current:
                    entries.append(finish(current))
                current = {}
            for key, parts in cells.items():
                current.setdefault(key, []).extend(parts)
        if current:
            entries.append(finish(current))
    return [e for e in entries if e.get("name") and e["name"] != "-"]


def finish(cells: dict[str, list[str]]) -> dict[str, str]:
    entry = {}
    for key, parts in cells.items():
        joined = "".join(parts) if key == "image" else " ".join(parts)
        if joined and joined != "-":
            entry[key] = joined
    return entry


if __name__ == "__main__":
    import json
    import sys
    rows = parse_pdf(open(sys.argv[1], "rb").read())
    print(len(rows), "entries")
    for row in rows[: int(sys.argv[2]) if len(sys.argv) > 2 else 8]:
        print(json.dumps(row, ensure_ascii=False))
