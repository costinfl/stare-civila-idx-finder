"""Check names_export.normalize_date / event_code against tests/derived.json (run: python tests/test_derived.py)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import names_export  # noqa: E402

cases = json.loads((ROOT / "tests" / "derived.json").read_text(encoding="utf-8"))
failures = 0
for date, year, event, act, title, sort_date, code in cases:
    got = (names_export.normalize_date(date, year), names_export.event_code(event, act, title))
    if got != (sort_date, code):
        failures += 1
        print(f"FAIL {[date, year, event, act, title]}: expected {(sort_date, code)}, got {got}")
print(f"{len(cases) - failures}/{len(cases)} derived values match (Python)")
sys.exit(1 if failures else 0)
