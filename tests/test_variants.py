"""Check names_export.variant_key against tests/variants.json (run: python tests/test_variants.py)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import names_export  # noqa: E402

cases = json.loads((ROOT / "tests" / "variants.json").read_text(encoding="utf-8"))
failures = [(w, k, names_export.variant_key(names_export.fold(w))) for w, k in cases.items()
            if names_export.variant_key(names_export.fold(w)) != k]
for word, expected, got in failures:
    print(f"FAIL {word!r}: expected {expected!r}, got {got!r}")
print(f"{len(cases) - len(failures)}/{len(cases)} variant keys match (Python)")
sys.exit(1 if failures else 0)
