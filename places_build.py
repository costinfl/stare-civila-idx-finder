"""Build site/data/places.json: other names of Romanian settlements, from Wikidata (CC0).

It asks Wikidata's main-graph SPARQL endpoint (query-main.wikidata.org; QLever
as a fallback), one county at a time, for every village, constituent locality,
town, municipality and commune located in a county of Romania (plus
Bucharest), with
their labels and aliases in languages historically used in Romania. Names that
only repeat the Romanian name (ignoring diacritics) are dropped; Romanian
aliases are kept as older spellings. Entries with the same Romanian name in the
same county are merged (a city is often both a municipality and a locality).

    python places_build.py            # ~43 small queries, a few seconds apart

Output: {"generated_at", "source", "languages", "places": [[qid, ro_name, county, {lang: [names]}], ...]}
"""

from __future__ import annotations

import json
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site" / "data" / "places.json"
# The classic query.wikidata.org may be rate-limited during incidents; the
# main-graph endpoint and QLever serve the same Wikidata data.
ENDPOINTS = ["https://query-main.wikidata.org/sparql", "https://qlever.dev/api/wikidata"]
PAUSE = 5  # seconds between queries
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


def sparql(query: str) -> list[dict]:
    """Run a query, trying each endpoint in turn; back off and retry on errors (timeouts, 429s)."""
    last_error: Exception | None = None
    for attempt in range(3):
        for endpoint in ENDPOINTS:
            url = endpoint + "?" + urllib.parse.urlencode({"query": PREFIXES + query})
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    return json.loads(response.read())["results"]["bindings"]
            except Exception as error:
                last_error = error
                print(f"  {endpoint.split('/')[2]}: {error}", flush=True)
        time.sleep(60 * (attempt + 1))
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
    for scope, (_, name) in zip(scopes, county_list + [(BUCHAREST, "București")]):
        time.sleep(PAUSE)
        found = collect(sparql(names_query(scope)), county_names)
        items.update(found)
        print(f"{name}: {len(found)} items", flush=True)
    places: dict[tuple[str, str], dict] = {}
    for (item_id, county), item in items.items():
        ro_names = sorted(item["names"].get("ro", set()), key=lambda n: (len(n), n))
        if not ro_names:
            continue
        # The shortest Romanian label is the name; other Romanian forms are aliases.
        ro = ro_names[0]
        entry = places.setdefault((county, ro), {"qid": item_id, "names": {}})
        for lang, names in item["names"].items():
            for name in names:
                if fold(name) != fold(ro):
                    entry["names"].setdefault(lang, set()).add(name)
    rows = []
    for (county, ro), entry in sorted(places.items(), key=lambda x: (x[0][0], fold(x[0][1]))):
        names = {lang: sorted(v) for lang in LANGUAGES if (v := entry["names"].get(lang))}
        rows.append([entry["qid"], ro, county, names])
    OUT.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Wikidata (CC0), https://www.wikidata.org/",
        "languages": LANGUAGES,
        "places": rows,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with_names = sum(1 for r in rows if r[3])
    print(f"{len(rows)} places ({with_names} with other names) -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
