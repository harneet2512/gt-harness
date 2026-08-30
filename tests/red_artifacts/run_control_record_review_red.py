from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.linear_control_record import RecordConflict, append_typed_record


class Store:
    def __init__(self) -> None:
        self.description = ""
        self.revision = 0
        self.lock = Lock()

    def read(self, issue_id: str) -> tuple[str, str]:
        with self.lock:
            return self.description, str(self.revision)

    def compare_and_swap(self, issue_id: str, revision: str, description: str) -> bool:
        with self.lock:
            if str(self.revision) != revision:
                return False
            self.description = description
            self.revision += 1
            return True


failures: list[str] = []
record = "kind: RECEIPT | id: REVIEW | status: PASS\n\nbody\n"
store = Store()
append_typed_record(store, "HAR-34", record)
store.description = store.description.replace("sha256=", "sha256=0", 1)
try:
    append_typed_record(store, "HAR-34", record)
except RecordConflict:
    pass
else:
    failures.append("malformed reserved marker accepted")

attribute = subprocess.check_output(
    ["git", "check-attr", "eol", "--", "scripts/linear_control_record.py"],
    text=True,
).strip()
if not attribute.endswith(": lf"):
    failures.append("source eol unspecified")

if failures:
    sys.stdout.buffer.write(("RED: " + "; ".join(failures) + "\n").encode())
    raise SystemExit(1)
raise SystemExit(0)
