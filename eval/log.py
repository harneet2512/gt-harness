from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class TaskRecord:
    task_id: str
    passed: bool
    iterations: int
    wall_seconds: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    transcript: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None


@dataclass
class RunLog:
    results_root: Path
    benchmark: str
    benchmark_version: str
    model: str
    provider: str
    harness_commit: str
    command: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _started_at: str = ""
    _tasks: list[TaskRecord] = field(default_factory=list)

    @property
    def run_dir(self) -> Path:
        return Path(self.results_root) / self.benchmark / self.model / self.run_id

    def start(self) -> None:
        self._started_at = datetime.now(UTC).isoformat()
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def add_task(self, rec: TaskRecord) -> None:
        transcript_path = self.run_dir / f"{rec.task_id}.transcript.jsonl"
        with transcript_path.open("w", encoding="utf-8") as f:
            for event in rec.transcript:
                f.write(json.dumps(event) + "\n")
        self._tasks.append(rec)

    def finish(self, grader_output_path: Path | None = None) -> Path:
        completed_at = datetime.now(UTC).isoformat()
        passed = sum(1 for t in self._tasks if t.passed)
        total = len(self._tasks) or 1
        manifest = {
            "run_id": self.run_id,
            "benchmark": self.benchmark,
            "benchmark_version": self.benchmark_version,
            "model": self.model,
            "provider": self.provider,
            "harness_commit": self.harness_commit,
            "command": self.command,
            "started_at": self._started_at,
            "completed_at": completed_at,
            "score": passed / total,
            "grader_output_path": (str(grader_output_path)
                                   if grader_output_path else None),
            "tasks": [self._task_summary(t) for t in self._tasks],
            "failed_task_samples": [t.task_id for t in self._tasks
                                    if not t.passed][:10],
        }
        out = self.run_dir / "manifest.json"
        out.write_text(json.dumps(manifest, indent=2))
        return out

    def _task_summary(self, t: TaskRecord) -> dict[str, Any]:
        d = asdict(t)
        d.pop("transcript")
        d["transcript_path"] = f"{t.task_id}.transcript.jsonl"
        return d
