# Stare Civilă IDX Finder

**Suggested GitHub repository:** `stare-civila-idx-finder`

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

