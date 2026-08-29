# Overnight Codex prompt: GT learning-driven overhaul

You are working unattended in `harneet2512/gt-harness`. No human is watching this run. Safety, evidence, and a clean handoff matter more than finishing every item.

## Goal

Carry out a major GT overhaul by learning from the mechanisms described as gnx in `instinct_work/00-comparison.md`. Do not copy another system's source, schema, names, or surface area. Preserve GT's strengths in evidence, verification, receipts, replay, and safe failure. Improve only mechanisms supported by the specifications and tests.

## Read before acting

1. Read this file fully.
2. Read `instinct_work/README.md`.
3. Read `instinct_work/00-comparison.md`.
4. Read `instinct_work/01-overhaul-plan.md`.
5. Read `instinct_work/02-trust-calibration.md`.
6. Inspect the current source and tests named by those files. Treat repository code as authoritative over damaged or aspirational docs.
7. Create or append `instinct_work/NOTES.md` with baseline commit, worktree status, tool versions, baseline commands, and results.

Do not edit code until all seven steps are complete.

## Expected baseline

The worktree should be based on `eb8714e8b739e37f39e2a6a3e95fe41c7a1db739` with the intended central-agent work from `2bf3f4954b123c222b7f6c2b98761654ef2ef007` reapplied. Confirm the relevant `select_catalog` code exists before changing anything. If ancestry or expected files do not match, write the mismatch to `instinct_work/NOTES.md` and stop. Do not repair history.

## Strict order of work

Follow `instinct_work/01-overhaul-plan.md` in this order:

0. Baseline and safety receipt.
1. Register direct feature 18 for `select_catalog` and close its attribution path.
2. Add richer first-party symbol/candidate contracts, but only with provenance current output can support.
3. Add SQLite `vec0` ANN candidate generation followed by exact hybrid rescore and deterministic fallback.
4. Add unweighted Leiden communities for inclusive and verified-only projections. Never use continuous trust weights.
5. Add witnessed process objects and feed bounded process items into the planning call.
6. Implement the first-party trust-calibration framework and run available external-oracle measurements.
7. Run isolated ablations, integrated tests, and closeout checks.

Do not skip ahead around a failure. A blocked item blocks dependent items. Independent later work is allowed only when the specification explicitly makes it independent and `NOTES.md` explains why proceeding is safe.

## Scope

You may change only:

- `gt_engine/` modules required by the numbered plan;
- `eval/gt_central_agent.py` for catalog planning and receipts;
- named or newly focused tests and fixtures under `tests/`;
- narrowly required optional dependency declarations in `pyproject.toml`;
- calibration/benchmark scripts under `scripts/`;
- versioned calibration or benchmark outputs under the repository's existing artifact/report conventions;
- `instinct_work/NOTES.md`.

Do not change README files outside `instinct_work/`, CI/workflows, release metadata, unrelated benchmarks, providers, model routing, or deployment code.

## Absolute prohibitions

- Do not touch any file under `vendor/`. You may read it and run its tests.
- Do not rewrite, reset, rebase, amend, squash, or otherwise change history.
- Do not force-push. Do not push at all unless the human later asks.
- Do not change anything outside the named scope.
- Do not copy another system's node tables, code, identifiers, UI, wiki, or query surface.
- Do not add points-to analysis, full taint analysis, Cypher, a new graph store, or a store migration.
- Do not turn scalar confidence into truth or use continuous trust weighting.
- Do not silently relax tests, delete fixtures, lower thresholds, mark tests xfail/skip, or catch errors merely to make the suite green.
- Do not install or execute unreviewed repository scripts with network or credential side effects.
- Do not expose secrets, modify credentials, or contact external parties.

If a required resolver change can only be made under `vendor/`, specify the exact additive output/schema contract needed in `instinct_work/NOTES.md`, mark the item blocked, and continue only with work that does not pretend the missing data exists.

## Test and commit protocol

Work in small atomic steps. Before every commit:

1. Run the focused tests for the changed mechanism.
2. Run `python -m pytest -q`.
3. Run `(cd vendor/gt-index-src && go test ./...)` when Go is available.
4. Run `git diff --check`.
5. Run `git diff --name-only -- vendor/`; it must print nothing.
6. Review `git diff --stat` and `git diff` for unrelated changes, generated junk, secrets, and accidental broad formatting.
7. Record commands and outcomes in `instinct_work/NOTES.md`.

Commit only if all applicable tests pass. If the baseline already had a failure, a commit is allowed only when the same command shows no new failures and the unchanged baseline failure is precisely documented. Never commit a new failure.

Each commit must contain one coherent mechanism and its tests. Use clear messages such as:

- `Register catalog selection as direct feature 18`
- `Add explicit resolution uncertainty contract`
- `Persist ANN candidates for exact hybrid rescore`
- `Add verified and inclusive Leiden projections`
- `Feed witnessed processes into catalog planning`
- `Add reproducible trust calibration report`

Do not bundle cleanup, formatting, or unrelated repairs.

## Defined safe stops

When any case below occurs, do not guess. Append a note to `instinct_work/NOTES.md` containing the item, command, exact error, files inspected, current commit, worktree status, and the smallest decision a human must make. Then stop the affected work before mutation or commit.

- Baseline commit or expected central-agent code is missing.
- The worktree contains unexplained changes.
- Two specs conflict or a decision has more than one plausible load-bearing interpretation.
- A migration could lose or reinterpret stored data.
- A required change falls under `vendor/` or outside the named scope.
- A dependency requires changing stores, broad CI work, privileged installation, credentials, or network access not already configured.
- A test fails and the failure cannot be tied to a pre-existing baseline failure.
- ANN results cannot be checked against exact search or fail the declared recall threshold.
- Community output is nondeterministic under fixed graph and seed.
- A process object would need an unwitnessed edge to appear continuous.
- Trust tier would require inference from legacy scalar confidence.
- An external oracle is unavailable or indeterminate. Record the case as indeterminate; never relabel it from GT's own output.
- A command could delete data, rewrite history, force push, publish, deploy, spend money, or contact a person.
- Tests hang, disk space is low, the environment becomes unstable, or repeated retries would be speculative.

On a safe stop, leave the worktree understandable. Do not discard pre-existing human changes. Do not create a commit for partial code that fails tests. A clean contract, fixture, or report-only commit is acceptable only if independently complete and green.

## Feature-specific non-negotiables

- Feature 18 counts valid catalog selection and delivery, not tool presence.
- Call candidates remain visible; a chosen target never erases alternatives.
- Agent-facing uncertainty includes candidate count, uniqueness in scope, dynamic-dispatch possibility, export status, and provenance.
- `vec0` generates candidates only. Existing exact hybrid logic owns final ranking.
- SQLite stays the durable store and a deterministic no-extension fallback remains tested.
- Leiden uses an unweighted inclusive projection and an unweighted verified-only projection. Community membership is navigation, never proof.
- Every process step cites existing node, edge, and evidence IDs. Gaps remain gaps.
- Tier is derived from resolution mechanism and uncertainty, not score thresholds.
- Publish trust error as errors/labeled, rate, 95% Wilson interval, and oracle coverage per language and method. Keep correctness, persistence, and behavioral corroboration separate.

## End-of-run handoff

Before stopping for any reason:

1. Run the broadest safe tests available and record them.
2. Confirm `git diff --name-only -- vendor/` prints nothing.
3. Confirm no changes exist outside the named scope. If they do, stop and document them; do not erase unexplained human work.
4. Append a final section to `instinct_work/NOTES.md` with:
   - starting and ending commit;
   - commits created, in order;
   - files changed by each commit;
   - tests run and exact outcomes;
   - measurements and artifact paths;
   - completed plan items;
   - blocked or incomplete items and why;
   - decisions still required;
   - any uncommitted changes and their purpose;
   - the exact next safe command.
5. Run `git status --short` and include its output in the note.

Do not claim completion unless every acceptance criterion in the relevant numbered item passed. A precise partial result with a safe stop is a successful unattended run; a plausible workaround that hides uncertainty is not.
