"""Check batches.py's back-off on server errors, offline (run: python tests/test_batches_gate.py)."""
import sys
import threading
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import batches  # noqa: E402
import server  # noqa: E402

server.REQUEST_INTERVAL = 0.05
batches.PAUSE_SECONDS = 0.3
batches.QUIET_SECONDS = 0.8
failures = 0


def check(name, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else ': ' + detail}")


def http(code):
    return urllib.error.HTTPError("https://example.invalid", code, "", {}, None)


def fresh(workers=3):
    batches.GATE = batches.Gate()
    batches.GATE.configure(workers)


class Recorder:
    """A fake request: records start times and how many run at once; fails on chosen calls."""

    def __init__(self, fail=(), duration=0.1):
        self.fail, self.duration = dict(fail), duration
        self.calls, self.starts, self.open, self.peak = 0, [], 0, 0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.calls += 1
            call = self.calls
            self.starts.append(time.monotonic())
            self.open += 1
            self.peak = max(self.peak, self.open)
        try:
            time.sleep(self.duration)
            if call in self.fail:
                raise self.fail[call]
            return call
        finally:
            with self.lock:
                self.open -= 1


# A 500 is retried once, after the pause, and then succeeds.
fresh()
fake = Recorder(fail={1: http(500)})
started = time.monotonic()
result = batches.fetch(fake)
check("500 then success is retried", result == 2 and fake.calls == 2, f"result {result}, calls {fake.calls}")
check("retry waits for the pause", fake.starts[1] - started >= batches.PAUSE_SECONDS, f"{fake.starts[1] - started:.2f}s")
check("after an error only 1 request at a time", batches.GATE.allowed == 1, f"allowed {batches.GATE.allowed}")

# Two 500s in a row: the error reaches the caller (the register is recorded as an error).
fresh()
fake = Recorder(fail={1: http(500), 2: http(503)})
try:
    batches.fetch(fake)
    check("two 500s raise", False, "no error raised")
except urllib.error.HTTPError as error:
    check("two 500s raise", error.code == 503 and fake.calls == 2, f"code {error.code}, calls {fake.calls}")

# A 404 is not retried and does not slow anything down.
fresh()
fake = Recorder(fail={1: http(404)})
try:
    batches.fetch(fake)
except urllib.error.HTTPError:
    pass
check("404 is not retried", fake.calls == 1, f"calls {fake.calls}")
check("404 does not back off", batches.GATE.allowed == 3 and batches.GATE.pause_until == 0.0)

# Timeouts count as server trouble.
check("timeout is transient", batches.transient(TimeoutError("The read operation timed out")))
check("URLError is transient", batches.transient(urllib.error.URLError("handshake")))

# Without errors, 3 requests run at once; after one error, never more than 1.
fresh()
fake = Recorder(duration=0.2)
threads = [threading.Thread(target=batches.fetch, args=(fake,)) for _ in range(6)]
[t.start() for t in threads]
[t.join() for t in threads]
check("up to 3 at once when quiet", fake.peak == 3, f"peak {fake.peak}")
gaps = [b - a for a, b in zip(fake.starts, fake.starts[1:])]
check("request starts stay spaced", min(gaps) >= server.REQUEST_INTERVAL - 0.005, f"min gap {min(gaps):.3f}")

fresh()
batches.fetch(Recorder(fail={1: http(500)}))  # trips the back-off
fake = Recorder(duration=0.1)
threads = [threading.Thread(target=batches.fetch, args=(fake,)) for _ in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
check("1 at a time after an error", fake.peak == 1, f"peak {fake.peak}")

# After a quiet stretch, back to 3 at once.
time.sleep(batches.QUIET_SECONDS)
fake = Recorder(duration=0.2)
threads = [threading.Thread(target=batches.fetch, args=(fake,)) for _ in range(6)]
[t.start() for t in threads]
[t.join() for t in threads]
check("back to 3 after the quiet period", batches.GATE.allowed == 3 and fake.peak == 3,
      f"allowed {batches.GATE.allowed}, peak {fake.peak}")

print(f"{'all' if not failures else failures} gate checks {'pass' if not failures else 'failed'}")
sys.exit(1 if failures else 0)
