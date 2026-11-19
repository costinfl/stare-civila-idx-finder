# Notice: code and data licences

**Code** (Python scripts, workflows, HTML/JS) in this repository is dedicated to
the public domain under [CC0 1.0](LICENSE).

**Data** is not ours. Everything under `site/data/` (the register catalogue)
and on the `names-data` branch (entries extracted from IDX files), as well as
the name index built from it at deploy time, comes from the National Archives
of Romania's portal [Descoperă Arhivele Naționale](https://descopera.arhivelenationale.ro/),
which distributes it under
[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/):

- **Attribution:** © Arhivele Naționale ale României — descopera.arhivelenationale.ro.
  Every record links back to its page on the portal.
- **Non-commercial:** the data may not be used for commercial purposes.
- **No derivatives:** values are reproduced exactly as published (only the
  format changes: listing pages and IDX PDFs become JSON). Normalised forms
  used for search, sorting and filtering are computed and never shown as
  if they were the original data.

Privacy: entries from the last 100 years are not published, and records or
entries listed in [`privacy/exclusions.json`](privacy/exclusions.json) are
removed at every deploy. Removal requests:
[open an issue](https://github.com/costinfl/stare-civila-idx-finder/issues/new?template=removal-request.yml).

This project is independent and not affiliated with the National Archives of
Romania. If the Archives ask us to stop collecting or to remove data, we will.
