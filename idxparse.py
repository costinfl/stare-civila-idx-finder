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

# Header label -> (first header word, field key)
COLUMNS = [
    ("Nume", "name"),
    ("Localitate", "place"),
    ("Tip", "event"),
    ("Data", "date"),
    ("An", "year"),
    ("Nr", "act"),
    ("Pag.", "page"),
    ("Imagine", "image"),
]
WRAPPING = {"name", "image"}
LINE_TOLERANCE = 3.0
COLUMN_SLACK = 6.0


def words_by_page(pdf: bytes) -> list[list[tuple[float, float, str]]]:
    out = subprocess.run(["pdftotext", "-tsv", "-", "-"], input=pdf, capture_output=True, check=True).stdout
    pages: list[list[tuple[float, float, str]]] = []
    for row in csv.DictReader(io.StringIO(out.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "1":
            pages.append([])
        elif row["level"] == "5" and pages:
            pages[-1].append((float(row["left"]), float(row["top"]), row["text"]))
    return pages


def header_columns(words: list[tuple[float, float, str]]) -> tuple[list[tuple[float, str]], float] | None:
    """Find the header row on a page and return [(x, key)] plus its top."""
    nume = [w for w in words if w[2] == "Nume"]
    if not nume:
        return None
    top = min(w[1] for w in nume)
    line = sorted((w for w in words if abs(w[1] - top) <= LINE_TOLERANCE), key=lambda w: w[0])
    columns: list[tuple[float, str]] = []
    used: set[str] = set()
    texts = [w[2] for w in line]
    for i, (x, _, text) in enumerate(line):
        for label, key in COLUMNS:
            if text != label or key in used:
                continue
            # "An act" / "Nr act": require the following word to avoid matching stray text.
            if key in ("year", "act") and (i + 1 >= len(texts) or texts[i + 1] != "act"):
                continue
            columns.append((x, key))
            used.add(key)
    return (sorted(columns), top) if len(columns) >= 3 else None


def column_for(x: float, columns: list[tuple[float, str]]) -> str:
    key = columns[0][1]
    for start, name in columns:
        if x + COLUMN_SLACK >= start:
            key = name
    return key


def parse_pdf(pdf: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    columns: list[tuple[float, str]] | None = None
    for words in words_by_page(pdf):
        found = header_columns(words)
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
