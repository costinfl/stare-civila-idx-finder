"""Check names_export.image_page against tests/pages.json (run: python tests/test_pages.py)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import names_export  # noqa: E402

cases = json.loads((ROOT / "tests" / "pages.json").read_text(encoding="utf-8"))
failures = 0
for starts, image, expected in cases:
    got = names_export.image_page(starts, image)
    if (list(got) if got else None) != expected:
        failures += 1
        print(f"FAIL {starts} image {image}: expected {expected}, got {got}")
print(f"{len(cases) - failures}/{len(cases)} image pages match (Python)")
sys.exit(1 if failures else 0)
