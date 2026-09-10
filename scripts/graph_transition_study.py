"""Measure what a repository transition costs, both ways, on real repositories.

The batch amendment exists to stop an edit paying for a whole re-index. On
arktype a full build was ~115s against an edit interval near 50s, which never
converges: the graph is stale for the rest of the task and every read after the
first edit reports caller coverage unavailable. Whether the amend actually
fixes that is a measurement, and it has to be made on real repositories --
toy-fixture timings and extrapolated median-turn savings are not evidence of an
end-to-end change.

Design, and why each part of it is there:

* One TRANSITION per repository: a deterministic edit to one real source file,
  chosen by the same rule everywhere (the largest supported source file), so
  the edit lands somewhere the resolver has to do work rather than in a leaf
  nobody imports.
* The PARENT graph is built once per repository and reused. It is the pre-edit
  graph the run already holds in both arms, so charging it to either arm would
  measure the same work twice and hide the thing being compared.
* BASELINE is a full rebuild of the edited tree. CANDIDATE is a batch amend
  from the parent. Those are the two ways the run can answer "what does the
  graph say now".
* Repetitions ALTERNATE baseline, candidate, baseline, candidate. A machine
  drifts -- page cache warms, another container starts, the disk is busy -- and
  running one arm to completion and then the other charges that drift entirely
  to whichever arm ran second.
* Every repetition re-checks SEMANTIC PARITY, not just time. A faster answer
  that says something different is not a faster answer.

Peak memory is each run's own VmHWM, polled while the producer is alive.
getrusage(RUSAGE_CHILDREN) would have been easier and would have been wrong: it
is a high-water mark across every child ever reaped, so it only climbs and
cannot attribute a peak to one run.

WHAT THIS DOES NOT MEASURE, stated so the numbers are not read as more than
they are. This is the cost of PRODUCING the graph for one transition, both
ways. The harness's blocked-versus-background split, the checks it runs and the
snapshots it takes are properties of the coordinator and the run loop, not of
the producer, and they need their own measurement against the live path.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path

# Extensions the producer parses, in the order preferred when choosing the file
# to edit. A transition in a language the producer ignores measures nothing.
SOURCE_SUFFIXES = (".py", ".go", ".ts", ".tsx", ".js", ".rs", ".java", ".rb")

# The fact surfaces compared for parity. Content only: the amend retains parent
# row ids by design and a rebuild renumbers, so ids would differ on every run
# without any consumer being able to tell.
PARITY = {
    "nodes": ("SELECT label,name,qualified_name,file_path,start_line,signature,"
              "is_test,language FROM nodes"),
    "edges": ("SELECT e.type,s.qualified_name,s.file_path,t.qualified_name,t.file_path"
              " FROM edges e LEFT JOIN nodes s ON s.id=e.source_id"
              " LEFT JOIN nodes t ON t.id=e.target_id"),
    "properties": ("SELECT p.kind,p.value,n.qualified_name,n.file_path FROM properties p"
                   " JOIN nodes n ON n.id=p.node_id"),
    "assertions": ("SELECT a.expression,tn.qualified_name,gn.qualified_name FROM assertions a"
                   " LEFT JOIN nodes tn ON tn.id=a.test_node_id"
                   " LEFT JOIN nodes gn ON gn.id=a.target_node_id"),
    "resolution_symbols": ("SELECT path,qualified_name,normalized_kind,native_kind,"
                           "language,export_status FROM resolution_symbols"),
    "resolution_callsites": ("SELECT source_file,callee,dispatch_state,candidate_count,"
                             "mechanism,verification_status FROM resolution_callsites"),
}


def _parity_digest(database: Path) -> dict[str, str]:
    """A digest per surface, so a mismatch names which surface moved."""
    import hashlib

    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        present = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        out: dict[str, str] = {}
        for kind, query in PARITY.items():
            if kind not in present:
                out[kind] = "absent"
                continue
            rows = sorted(db.execute(query).fetchall(), key=repr)
            payload = "\n".join(repr(row) for row in rows)
            out[kind] = f"{len(rows)}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
        return out


def _index_command(binary: str, root: str, output: str, *, max_files: int,
                   workers: int) -> list[str]:
    return [binary, "-root", root, "-output", output,
            "-max-files", str(max_files), "-workers", str(workers), "-closure=true"]


def _peak_kb(pid: int) -> int:
    """VmHWM for one process, read while it is alive.

    getrusage(RUSAGE_CHILDREN) is a high-water mark across every child this
    process has ever reaped, so it only ever climbs and cannot attribute a peak
    to one run. VmHWM is per process and monotonic within it, so the last value
    read before exit is that run's peak -- accurate to whatever growth happens
    inside the final polling interval, which is stated rather than hidden.
    """
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def _run(command: list[str]) -> dict:
    """Run one producer invocation and record what it cost.

    The sampling happens on a THREAD and the pipes are drained on this one.
    Polling ``poll()`` in a loop and only calling ``communicate()`` afterwards
    deadlocks the moment the producer writes more than a pipe buffer: the child
    blocks on a full stderr, so it never exits, so ``poll()`` never returns, so
    nothing ever reads the pipe. The producer prints a line per pass per file
    class, and the repositories this study is for are the large ones.
    """
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    peak = {"kb": 0}
    finished = threading.Event()

    def sample() -> None:
        while not finished.is_set():
            peak["kb"] = max(peak["kb"], _peak_kb(process.pid))
            finished.wait(0.05)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        stdout, stderr = process.communicate()
    finally:
        finished.set()
        sampler.join(timeout=1.0)
    peak_kb = peak["kb"]
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    elapsed = time.monotonic() - started
    stderr = completed.stderr or ""
    counts: dict[str, int] = {}
    for line in stderr.splitlines():
        stripped = line.strip()
        for label, key in (("Files:", "files"), ("Nodes:", "nodes"), ("Edges:", "edges")):
            if stripped.startswith(label):
                try:
                    counts[key] = int(stripped.split()[-1])
                except ValueError:
                    pass
    result: dict = {}
    for line in reversed((completed.stdout or "").strip().splitlines()):
        candidate = line.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                result = json.loads(candidate)
            except ValueError:
                result = {}
            break
    return {
        "seconds": round(elapsed, 3),
        "returncode": completed.returncode,
        "peak_mb": round(peak_kb / 1024, 1),
        "peak_basis": "VmHWM sampled every 50ms while the process was alive",
        "counts": counts,
        "amend_result": result,
        "stderr_tail": stderr[-400:] if completed.returncode else "",
    }


def _largest_source(root: Path) -> Path | None:
    """The biggest parseable file, by bytes.

    Deterministic and repository-independent. Editing the biggest file puts the
    transition where the resolver has the most to redo, which is the case the
    amend has to be good at; editing a leaf would flatter it.
    """
    best: tuple[int, Path] | None = None
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True)
    names = (tracked.stdout.decode("utf-8", "replace").split("\0")
             if tracked.returncode == 0 else [])
    for name in names:
        if not name or not name.endswith(SOURCE_SUFFIXES):
            continue
        path = root / name
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size and (best is None or size > best[0]):
            best = (size, path)
    return best[1] if best else None


def _apply_transition(path: Path) -> None:
    """A real edit: one new top-level definition appended to a real file.

    Appending keeps every existing line number stable, so the measurement is of
    the resolver's work rather than of a whole-file reflow, and the new symbol
    is a genuine new resolution target.
    """
    suffix = path.suffix.lower()
    if suffix == ".py":
        addition = "\n\ndef gt_transition_probe():\n    return 1\n"
    elif suffix == ".go":
        addition = "\n\nfunc GTTransitionProbe() int {\n\treturn 1\n}\n"
    elif suffix == ".rs":
        addition = "\n\npub fn gt_transition_probe() -> i32 {\n    1\n}\n"
    elif suffix in {".ts", ".tsx", ".js"}:
        addition = "\n\nexport function gtTransitionProbe() { return 1; }\n"
    elif suffix == ".java":
        addition = "\n// gt_transition_probe\n"
    else:
        addition = "\n\ndef gt_transition_probe\n  1\nend\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(addition)


def study_repository(name: str, source: Path, workspace: Path, binary: str, *,
                     repetitions: int, max_files: int, workers: int) -> dict:
    work = workspace / name
    if work.exists():
        shutil.rmtree(work)
    copy_started = time.monotonic()
    shutil.copytree(source, work, symlinks=True)
    copy_seconds = round(time.monotonic() - copy_started, 2)

    target = _largest_source(work)
    if target is None:
        return {"repo": name, "status": "no_parseable_source"}

    graphs = workspace / f"{name}-graphs"
    graphs.mkdir(parents=True, exist_ok=True)
    parent = graphs / "parent.db"
    parent_run = _run(_index_command(binary, str(work), str(parent),
                                     max_files=max_files, workers=workers))
    if parent_run["returncode"] != 0:
        return {"repo": name, "status": "parent_build_failed", "parent": parent_run}

    _apply_transition(target)
    relative = target.relative_to(work).as_posix()

    runs: list[dict] = []
    parity: list[dict] = []
    for index in range(repetitions * 2):
        # Alternate, starting with baseline. A machine drifts, and running one
        # arm to completion charges that drift entirely to the other.
        arm = "baseline" if index % 2 == 0 else "candidate"
        output = graphs / f"{arm}-{index}.db"
        command = _index_command(binary, str(work), str(output),
                                 max_files=max_files, workers=workers)
        if arm == "candidate":
            command = command + ["-amend-parent", str(parent)]
        measured = _run(command)
        measured.update({"arm": arm, "repetition": index // 2, "ordinal": index})
        runs.append(measured)
        if measured["returncode"] == 0:
            parity.append({"arm": arm, "ordinal": index,
                           "digest": _parity_digest(output)})
        output.unlink(missing_ok=True)

    baseline = [r["seconds"] for r in runs if r["arm"] == "baseline" and not r["returncode"]]
    candidate = [r["seconds"] for r in runs if r["arm"] == "candidate" and not r["returncode"]]
    digests = {json.dumps(item["digest"], sort_keys=True) for item in parity}
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(graphs, ignore_errors=True)
    return {
        "repo": name,
        "status": "measured",
        "edited_file": relative,
        "copy_seconds": copy_seconds,
        "parent_seconds": parent_run["seconds"],
        "parent_counts": parent_run["counts"],
        "runs": runs,
        "baseline_seconds": baseline,
        "candidate_seconds": candidate,
        "baseline_median": _median(baseline),
        "candidate_median": _median(candidate),
        "speedup": (round(_median(baseline) / _median(candidate), 2)
                    if baseline and candidate and _median(candidate) else None),
        # One distinct digest across every run of both arms is the parity claim.
        "semantic_parity": len(digests) == 1,
        "distinct_digests": len(digests),
        "parity": parity if len(digests) != 1 else parity[:1],
    }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="graph-transition-study")
    parser.add_argument("--repos-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--max-files", type=int, default=60000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("repos", nargs="+")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    report = {
        "schema": "gt.graph_transition_study.v1",
        "repetitions": args.repetitions,
        "workers": args.workers,
        "max_files": args.max_files,
        "repositories": [],
    }
    for name in args.repos:
        source = Path(args.repos_root) / name
        started = time.monotonic()
        row = study_repository(name, source, workspace, args.binary,
                               repetitions=args.repetitions,
                               max_files=args.max_files, workers=args.workers)
        row["wall_seconds"] = round(time.monotonic() - started, 1)
        report["repositories"].append(row)
        # Written after every repository: a study that only lands at the end
        # loses everything to one slow repository.
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in row.items() if k not in {"runs", "parity"}}),
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
