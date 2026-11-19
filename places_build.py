"""Build site/data/places.json: other names of Romanian settlements, from Wikidata (CC0).

It asks Wikidata's main-graph SPARQL endpoint (query-main.wikidata.org; QLever
as a fallback), one county at a time, for every village, constituent locality,
town, municipality and commune located in a county of Romania (plus
Bucharest), with
their labels and aliases in languages historically used in Romania. Names that
only repeat the Romanian name (ignoring diacritics) are dropped; Romanian
aliases are kept as older spellings. Entries with the same Romanian name in the
same county are merged (a city is often both a municipality and a locality).

    python places_build.py            # ~43 small queries, one a minute (~45 min)
                                      # rerun to resume: idx_store/places_cache/ keeps each county

Output: {"generated_at", "source", "languages", "places": [[qid, ro_name, county, {lang: [names]}], ...]}
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site" / "data" / "places.json"
CACHE = ROOT / "idx_store" / "places_cache"  # per-county results, so a rate-limited run can resume
# The classic query.wikidata.org may be rate-limited during incidents; the
# main-graph endpoint and QLever serve the same Wikidata data.
ENDPOINTS = ["https://query-main.wikidata.org/sparql", "https://qlever.dev/api/wikidata"]
PAUSE = 65  # seconds between queries: WDQS may allow only 1 request a minute during incidents
PREFIXES = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""
USER_AGENT = "StareCivilaIDXFinder/1.1 (+https://github.com/costinfl/stare-civila-idx-finder)"
# village, constituent locality, municipality of Romania, town in Romania, commune of Romania
CLASSES = "wd:Q532 wd:Q15921247 wd:Q640364 wd:Q16858213 wd:Q659103"
LANGUAGES = ["ro", "hu", "de", "sr", "sh", "hr", "sk", "cs", "uk", "rue", "pl", "bg", "tr", "crh", "tt", "yi", "he", "la", "el", "hy", "rmy"]
BUCHAREST = "Q19660"


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn").casefold().strip()


# Attribution order when Wikidata files a name under several languages (aliases are
# often copied across languages): Hungarian and German first, Romanian last.
PRIORITY = ["hu", "de", "la", "sk", "cs", "sr", "sh", "hr", "pl", "uk", "rue", "bg", "tr", "crh", "tt", "yi", "he", "el", "hy", "rmy", "ro"]
NOISE = ("opstina", "gmina", "comuna ", "municipiul", "orasul ", "okreg", "district", "county", "kreis ", "kozseg", "megye")
GERMAN = re.compile(r"(ß|ä|stadt|burg|dorf|berg|markt|feld|hausen|kirch|wald|bach|heim|au$)", re.I)


def squash(name: str) -> str:
    return "".join(c for c in fold(name) if c.isalnum())


def same_name(a: str, b: str) -> bool:
    return squash(a) == squash(b)


def is_clean(name: str) -> bool:
    """Latin script, no disambiguation (parentheses, commas) or administrative words."""
    if any(c in name for c in "(),/") or not name.strip() or re.fullmatch(r"[A-Z0-9-]+", name):
        return False
    if any(unicodedata.category(c).startswith("L") and "LATIN" not in unicodedata.name(c, "") for c in name):
        return False
    folded = fold(name) + " "
    return not any(word in folded for word in NOISE)


def labels_query() -> str:
    langs = ", ".join(f'"{lang}"' for lang in LANGUAGES)
    return f"""SELECT ?item ?label WHERE {{
  VALUES ?cls {{ {CLASSES} }}
  ?item wdt:P31 ?cls ; wdt:P17 wd:Q218 ; rdfs:label ?label .
  FILTER(LANG(?label) IN ({langs}))
}}"""


def sparql(query: str) -> list[dict]:
    """Run a query, trying each endpoint in turn; back off and retry on errors (timeouts, 429s)."""
    last_error: Exception | None = None
    for attempt in range(6):
        for endpoint in ENDPOINTS:
            url = endpoint + "?" + urllib.parse.urlencode({"query": PREFIXES + query})
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    return json.loads(response.read())["results"]["bindings"]
            except Exception as error:
                last_error = error
                print(f"  {endpoint.split('/')[2]}: {error}", flush=True)
        time.sleep(90 + 30 * attempt)
    raise RuntimeError(f"every SPARQL endpoint failed: {last_error}")


def counties() -> list[tuple[str, str]]:
    rows = sparql('SELECT ?c ?l WHERE { ?c wdt:P31 wd:Q1776764 . ?c rdfs:label ?l FILTER(lang(?l)="ro") }')
    known = {fold(c["name"]): c["name"] for c in server.COUNTIES}
    out = []
    for row in rows:
        label = row["l"]["value"].removeprefix("Județul ").removeprefix("Judeţul ")
        name = known.get(fold(label))
        if name:
            out.append((row["c"]["value"].rsplit("/", 1)[-1], name))
    return sorted(out, key=lambda x: x[1])


def names_query(scope: str) -> str:
    langs = ", ".join(f'"{lang}"' for lang in LANGUAGES)
    return f"""SELECT DISTINCT ?item ?county ?name WHERE {{
  VALUES ?cls {{ {CLASSES} }}
  {scope}
  ?item wdt:P31 ?cls .
  {{ ?item rdfs:label ?name }} UNION {{ ?item skos:altLabel ?name }}
  FILTER(LANG(?name) IN ({langs}))
}}"""


def collect(rows: list[dict], county_names: dict[str, str]) -> dict[tuple[str, str], dict]:
    """(item id, county name) -> {"names": {lang: set}}"""
    items: dict[tuple[str, str], dict] = {}
    for row in rows:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        county = county_names.get(row["county"]["value"].rsplit("/", 1)[-1])
        if not county:
            continue
        item = items.setdefault((qid, county), {"names": {}})
        item["names"].setdefault(row["name"]["xml:lang"], set()).add(row["name"]["value"].strip())
    return items


def main() -> None:
    county_list = counties()
    county_names = dict(county_list)
    county_names[BUCHAREST] = "București"
    scopes = [f"BIND(wd:{qid} AS ?county) ?item wdt:P131+ ?county ." for qid, _ in county_list]
    scopes.append(f"VALUES ?item {{ wd:{BUCHAREST} }} BIND(wd:{BUCHAREST} AS ?county)")
    items: dict[tuple[str, str], dict] = {}
    CACHE.mkdir(parents=True, exist_ok=True)
    for scope, (qid, name) in zip(scopes, county_list + [(BUCHAREST, "București")]):
        cached = CACHE / f"{qid}.json"
        from_cache = cached.exists()
        if from_cache:
            rows = json.loads(cached.read_text(encoding="utf-8"))
        else:
            time.sleep(PAUSE)
            rows = sparql(names_query(scope))
            cached.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        found = collect(rows, county_names)
        items.update(found)
        print(f"{name}: {len(found)} items{' (cached)' if from_cache else ''}", flush=True)
    # Official labels (not aliases) per item and language: the Romanian label is
    # the place's name, and a name is attributed to the language it labels.
    labels_file = CACHE / "_labels.json"
    if labels_file.exists():
        label_rows = json.loads(labels_file.read_text(encoding="utf-8"))
    else:
        time.sleep(PAUSE)
        label_rows = sparql(labels_query())
        labels_file.write_text(json.dumps(label_rows, ensure_ascii=False), encoding="utf-8")
    labels: dict[str, dict[str, str]] = {}
    for row in label_rows:
        labels.setdefault(row["item"]["value"].rsplit("/", 1)[-1], {})[row["label"]["xml:lang"]] = row["label"]["value"].strip()

    places: dict[tuple[str, str], dict] = {}
    for (item_id, county), item in items.items():
        ro = labels.get(item_id, {}).get("ro")
        if not ro or not is_clean(ro):
            continue
        entry = places.setdefault((county, ro), {"qid": item_id, "names": {}})
        label_lang = {name: lang for lang, name in labels.get(item_id, {}).items() if lang != "ro"}
        all_names = {name for names in item["names"].values() for name in names}
        for name in all_names:
            if not is_clean(name) or same_name(name, ro):
                continue
            langs = {lang for lang, names in item["names"].items() if name in names}
            # Prefer the language whose official label this is, else the priority order.
            lang = label_lang.get(name)
            if not lang and "de" in langs and GERMAN.search(fold(name)):
                lang = "de"  # e.g. Kronstadt, Neumarkt: also filed under hu by Wikidata
            lang = lang or next(l for l in PRIORITY if l in langs)
            entry["names"].setdefault(lang, set()).add(name)
        # Drop a name kept in two languages after merging items (keep the higher-priority one).
        seen: set[str] = set()
        for lang in PRIORITY:
            for name in sorted(entry["names"].get(lang, set())):
                key = squash(name)
                if key in seen:
                    entry["names"][lang].discard(name)
                seen.add(key)
    rows = []
    for (county, ro), entry in sorted(places.items(), key=lambda x: (x[0][0], fold(x[0][1]))):
        names = {lang: sorted(v) for lang in PRIORITY if (v := entry["names"].get(lang))}
        rows.append([entry["qid"], ro, county, names])
    OUT.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Wikidata (CC0), https://www.wikidata.org/",
        "languages": LANGUAGES,
        "places": rows,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with_names = sum(1 for r in rows if r[3])  # noqa: E501
    print(f"{len(rows)} places ({with_names} with other names) -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
