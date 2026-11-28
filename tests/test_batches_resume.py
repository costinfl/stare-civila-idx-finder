"""--retry-errors must only retry registers that never succeeded, whatever the order
the run files are read in (run: python tests/test_batches_resume.py)."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import batches  # noqa: E402
import names  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else ': ' + detail}")


def write(folder, stamp, lines):
    (folder / f"{stamp}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")


ok = lambda rid: {"id": rid, "batches": [[1, "a_01.PDF", None, 1]], "bytes": 0}
err = lambda rid: {"id": rid, "error": "HTTP Error 500: "}

for order in ("success later", "success earlier"):
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        # 1: failed, then succeeded; 2: only failed; 3: only succeeded; 4: succeeded, then failed.
        first, second = ("20261122T000000Z", "20261124T000000Z") if order == "success later" else ("20261124T000000Z", "20261122T000000Z")
        write(folder, first, [err(1), err(2), ok(3), ok(4)])
        write(folder, second, [ok(1), err(2), err(4)])
        retry_done = names.load_done(folder, retry_errors=True)
        plain_done = names.load_done(folder, retry_errors=False)
        status = batches.register_status(folder)
        check(f"{order}: retry skips registers that succeeded once", retry_done == {1, 3, 4}, str(sorted(retry_done)))
        check(f"{order}: without --retry-errors everything tried is done", plain_done == {1, 2, 3, 4}, str(sorted(plain_done)))
        check(f"{order}: stats count only never-succeeded as errors", [r for r, good in status.items() if not good] == [2], str(status))

print(f"{'all' if not failures else failures} resume checks {'pass' if not failures else 'failed'}")
sys.exit(1 if failures else 0)
