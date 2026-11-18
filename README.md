# Stare Civilă IDX Finder

**Live catalogue (GitHub Pages):** https://costinfl.github.io/stare-civila-idx-finder/

## Static catalogue (GitHub Pages)

The archive sends no CORS headers, so a static site cannot query it live. Instead `crawl.py` builds a catalogue once and the Pages site (`site/`) searches it entirely in the browser — visitors send no requests to the Archives.

How the catalogue is found without opening record pages: IDX PDFs are text reports with fixed column headers (`Nume`, `Localitate`, `Tip eveniment`, `Imagine PDF`), and the archive's **Conținut text fișiere atașate UD** search (`FieldId=-999005`) indexes that text. Searching it for `Tip eveniment` (plus `Imagine PDF`, which a few Alba indexes use) per county prefix returns exactly the records that carry an IDX file — about 500 listing requests in total, one every 1.25 s.

```powershell
python crawl.py              # crawl counties not yet in idx_store/, then export site/data/catalogue.json
python crawl.py --refresh    # re-crawl everything
python crawl.py --only PH-   # one county
python crawl.py --export     # rebuild site/data from idx_store/ only
```

`idx_store/` (git-ignored) holds the per-county listings so an interrupted crawl resumes. The **Refresh catalogue** workflow re-runs the crawl on demand and commits the new catalogue; **Deploy site to GitHub Pages** publishes `site/` on every change to `main`. One-time setup: *Settings → Pages → Build and deployment → Source: GitHub Actions*.

### Name index

The site's **Names** tab searches the entries inside the IDX PDFs (name, place, event, date, act, register page, image number). It is built in two steps:

1. `names.py` opens each catalogue record's page, downloads its IDX PDF(s) and extracts the table with `idxparse.py`, which reads word positions from poppler's `pdftotext -tsv` (install `poppler-utils`, or poppler on Windows). Each run appends `<store>/<county>/<run>.jsonl` and resumes from what is already stored. At two or more requests per register, the ~47,600 registers take roughly three to four days of continuous, paced crawling.
2. `names_export.py` turns the store into gzip shards keyed by name-token prefix (`site/data/names/`, git-ignored). A search downloads only the shard(s) for its rarest word.

The **Crawl IDX names** workflow runs every 6 hours for about 5 hours, committing progress to the `names-data` branch after each 55-minute chunk; each Pages deploy rebuilds the shards from that branch, so the Names tab grows as the crawl advances.

```powershell
python names.py --only PH- --limit 20       # local sample into idx_store/names
python names.py --stats                     # progress, no requests
python names_export.py                      # build site/data/names for local preview
python -m http.server -d site 8000          # preview at http://localhost:8000
```

Note: București's archive code in the Cotă field is `MB`, not `B`; the local tool now uses `MB-`.

## Local live-search tool

The earlier empty-file conclusion was based on the wrong field: `Denumire fișier atașat` did not find the example record. Your example at [record 13913618](https://descopera.arhivelenationale.ro/cota/?cid=13913618) confirms that attached files are visible on the record page; its IDX file is `PH-FD-00006-209-5(PR6)_IDX.pdf`.

This project now provides a local page that searches the archive county by county, opens each matching record page, and collects attached PDF links whose filenames contain `IDX`. It can optionally narrow the archive search with a term in **Conținut text fișiere atașate UD**.

`index.csv` is the output schema template; running a search in the page and selecting **Download CSV** creates the populated catalogue.

## Run

Requires Python 3.10 or newer; there are no third-party packages.

```powershell
python server.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in a browser. The page starts with all 42 county/municipality archive codes selected and “stare civila” in the title field. Leave the optional PDF text field blank to collect all matching IDX PDFs, or enter a word such as `Popescu` to narrow the search. To check a single unit, select its county and enter a specific Cotă prefix such as `PH-F-00006-209-5`; that runs only the selected county. Searches run sequentially, and can be stopped after the current request.

When results are collected, use **Download CSV** for the record/file catalogue or **Download IDX PDFs as ZIP** to gather the PDFs for local searching. The ZIP also includes an `INDEX.txt` linking each file to its catalogue record and call number.

## Search fields

- `Titlu` (`FieldId=1`) contains the title term.
- `Cotă` (`FieldId=2`) starts with the county prefix, such as `BZ-` for Buzău.
- Optional `Conținut text fișiere atașate UD` (`FieldId=-999005`) contains the PDF text term.
- On each returned record, the page reads attached file metadata and keeps PDF filenames containing `IDX`.

Each search request asks for up to 100 results and paginates until it reaches the end of that county's matches. All requests to the Archives host are paced at no more than one every 1.25 seconds, including search pages, record details, and PDF downloads. Because the catalogue's result rows do not include attachment links, a broad search must open each matching record page to inspect its files; it can take much longer than 2–3 minutes. Use a PDF text term or a specific Cotă prefix to narrow a run.

The catalogue endpoints used are:

- `https://descopera.arhivelenationale.ro/wp-admin/admin-ajax.php?action=get_advanced_search`
- `https://descopera.arhivelenationale.ro/cota/?cid={record_id}`
- `https://descopera.arhivelenationale.ro/api/media/file?id={record_id}&type=fisiere&mediaId={media_id}`
Open [http://127.0.0.1:8765/browse](http://127.0.0.1:8765/browse) to load a downloaded ZIP into the PDF content browser. It extracts each PDF's selectable text into visual table rows and an in-memory SQLite database, then lets you search, sort, and filter by file, page, line, or text. ZIP contents remain in the browser and are cleared when the tab is closed or reloaded. The browser fetches JSZip, PDF.js, and SQLite WebAssembly from their CDNs the first time; image-only scans need OCR before they contain searchable text.
