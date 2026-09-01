# GT Review Inbox (gt.review_inbox.v1)

Machine-readable review transport on `refs/heads/gt-review-inbox`. Never merged to main.

## Layout

```
inbox/
  INDEX.json
  <ticket>/
    <packet_id>.json
tools/
  commit_review_packet.py
```

## Packet schema (gt.review_packet.v1)

| Field | Type | Notes |
| --- | --- | --- |
| schema | string | Always `gt.review_packet.v1` |
| packet_id | string | Stable id within ticket (e.g. `finder-f1`) |
| ticket | string | Linear ticket (e.g. `HAR-63`) |
| pr | int | GitHub PR number |
| head_sha | string | Exact candidate SHA reviewed |
| source.system | string | `codex-check` \| `gt-ci` \| `coordinator` |
| source.check | string | Check or review pass name |
| kind | string | `finding` \| `check_outcome` |
| severity | string | e.g. `substance`, `mechanics` |
| status | string | `open` \| `fixed` \| `adjudicated-false-positive` \| `adjudicated-directed-fix` |
| file | string | Primary file path |
| line | int | Primary line (0 if N/A) |
| message | string | Short finding title |
| detail | string | Full finding body |
| supersedes | string \| null | Prior packet_id this replaces |
| created_at | string | ISO-8601 UTC |
| packet_digest_sha256 | string | Integrity binding (see below) |

## Digest

`packet_digest_sha256` = SHA-256 over canonical JSON of the packet **excluding** the digest field.

Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.

## INDEX.json (gt.review_inbox.v1)

Lists all live packet IDs and per-ticket index. Updated atomically with each new packet commit.

## Writer rule

When any external review event happens (Finder finding, Bugbot outcome, coordinator escalation), commit the packet to this ref at the same time it is posted elsewhere. Packets are immutable; state changes use a new packet with `supersedes`.

## Reader rule

On ref movement: read new/changed packets, verify digests, adjudicate, mint verdicts on HAR-57 + ticket comment, write adjudication packet back to inbox.

## Commit helper

```bash
python tools/commit_review_packet.py \
  --ticket HAR-63 --packet-id finder-f1 --pr 25 \
  --head-sha <sha> --system codex-check --check phase-a-re-review \
  --kind finding --message "..." --detail "..."
```
