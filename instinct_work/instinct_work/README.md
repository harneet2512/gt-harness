# Instinct work queue

This directory is the handoff boundary for substantial GT changes.

- Read files in numeric order.
- Each numbered file is a specification, not permission to widen scope.
- Status values are `ready`, `in progress`, `blocked`, and `done`.
- Record decisions, safe stops, failed commands, and unfinished work in `NOTES.md`.
- `CODEX_PROMPT.md` is the unattended execution prompt. It does not override the numbered specs.

| File | Status | Purpose |
|---|---|---|
| `00-comparison.md` | ready | What GT should learn from gnx and why |
| `01-overhaul-plan.md` | ready | Ordered implementation plan and checks |
| `02-trust-calibration.md` | ready | Measured trust and false-confidence program |
| `CODEX_PROMPT.md` | ready | Safe overnight execution instructions |
