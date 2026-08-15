# ARB retrieval evaluation

Runs:

- GT production retrieval: GitHub Actions `31442710368`, commit `2724211`'s
  predecessor `d97014e` (the GT adapter and index runtime used by the run).
- Official lexical control: GitHub Actions `31444984704`, baseline path fix at
  commit `be23369`.
- Dataset: pinned ARB source commit `07014c986f3deadb1548c62b32c0ffbe6a81465d`.
- Inputs: 427 gold-free rows, 345 positive rows and 82 natural no-gold rows.

## Runtime and substrate

The GT run processed all 427 rows. 419 source-backed snapshots had a valid
SQLite graph, and 8 were rejected for incomplete source coverage. 320 rows
produced ranked candidates and 284 produced a bounded certified payload. 143
rows abstained. Mean graph indexing time was 30.76 seconds per snapshot and
mean query/ranking time was 27 ms. These are substrate and delivery metrics,
not retrieval-quality scores.

## Gold-aware comparison

Gold was never provided to GT. For measurement only, the GT receipt paths were
joined by `sample_id` with the official baseline detail files after both runs
completed. The table uses file-path overlap and is therefore a diagnostic
comparison of the production output, not a claim that GT ran with gold.

| Arm | Positive rows | Recall@20 | MRR |
| --- | ---: | ---: | ---: |
| Official lexical all-files control | 345 | 0.4940 | 0.1574 |
| GT ranked candidates (up to 12) | 345 | 0.0766 | 0.0392 |
| GT delivered payload (up to 3) | 345 | 0.0193 | 0.0174 |

GT ranked MRR by task family:

| Task family | Rows | Lexical MRR | GT ranked MRR |
| --- | ---: | ---: | ---: |
| code2test | 106 | 0.0663 | 0.0000 |
| comment2context | 80 | 0.1530 | 0.0250 |
| edit2ripple | 58 | 0.2428 | 0.0197 |
| trace2code | 101 | 0.2075 | 0.1026 |

The 82 abstention rows have no gold by design, so they do not receive Recall
or MRR. Their separate safety result is that GT did not invent repository
evidence for them.

## Diagnosis

The graph substrate is no longer the blocker. The dominant failure is a
retrieval/query contract miss: the ARB adapter passes instruction text and
active paths into the generic repository-intelligence interface, while the
current interface has no typed task-family or relation-need input. That is
particularly visible in `code2test` (the active implementation file is known,
but the required test file is not selected) and `edit2ripple` (caller/ripple
relations are not selected from the active edit). This is a `QUERY_MISS` /
`RETRIEVAL_MISS`, not an indexing failure.

The bounded delivery policy further lowers measured payload recall from 0.0766
to 0.0193 because only the top three candidates that pass the certified
confidence/relevance gates can reach the model. Increasing that cap blindly
would be over-retrieval, not a fix. The next change must make the evidence need
typed and relation-aware, then rerun the same pinned cases.

## Decision

GT is **not complete for final model evaluation**. Runtime integrity and graph
construction pass. Retrieval quality fails the promotion gate against the
official lexical control, so DeepSWE and Terminal-Bench must remain blocked.
No end-to-end model claim is made from this run.
