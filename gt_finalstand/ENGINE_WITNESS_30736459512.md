# ENGINE witness — 10-task matched comparison

Baseline: Mini-SWE 2.2.8 / DeepSeek V4 Flash / temp 1.0 / step 100 (frozen; not rerun). Ten tasks only; no general efficacy claim.

| task | base rwd | engine rwd | calls B/E | actions B/E | pre-edit B/E | raw B/E | gt B/E | visible B/E | decisions (engine) | fallback |
|---|---|---|---|---|---|---|---|---|---|---|
| fix-code-vulnerability | 1.0 | graded | 33/49 | 33/49 | 8/22 | 44123/97205 | 0/97205 | 44123/97205 | {"pass_through": 48} | 0 |
| portfolio-optimization | 1.0 | graded | 26/31 | 30/31 | 16/21 | 22561/29882 | 0/29882 | 22561/29882 | {"pass_through": 30} | 0 |
| modernize-scientific-stack | 1.0 | graded | 8/8 | 16/9 | 9/8 | 12083/10423 | 0/10423 | 12083/10423 | {"pass_through": 8} | 0 |
| headless-terminal | 1.0 | graded | 86/42 | 86/42 | 62/26 | 119530/51932 | 0/51932 | 119530/51932 | {"pass_through": 41} | 0 |
| llm-inference-batching-scheduler | 1.0 | graded | 41/49 | 42/49 | 4/16 | 82083/73245 | 0/73245 | 82083/73245 | {"pass_through": 48} | 0 |
| break-filter-js-from-html | 1.0 | graded | 12/28 | 16/35 | 5/7 | 18921/37560 | 0/37560 | 18921/37560 | {"pass_through": 34} | 0 |
| write-compressor | 1.0 | graded | 16/27 | 17/29 | 8/23 | 18715/18774 | 0/18774 | 18715/18774 | {"pass_through": 29} | 0 |
| gpt2-codegolf | 0.0 | ? | 59/0 | 59/0 | 12/0 | 325302/0 | 0/0 | 325302/0 | {} | 0 |
| schemelike-metacircular-eval | 1.0 | graded | 100/100 | 125/100 | 101/8 | 94003/114559 | 0/114559 | 94003/114559 | {"pass_through": 100} | 0 |
| cobol-modernization | 1.0 | graded | 39/72 | 59/74 | 40/41 | 47028/56114 | 0/56114 | 47028/56114 | {"pass_through": 73} | 0 |
