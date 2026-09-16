"""Build tests/fixtures/benchmarks/model_command_labels.json.

`labels` carries one row per recorded model command, in corpus order:
  family : which runner the command invokes, or null when it invokes none
  kind   : plain | compound | fragment, from the documented
           `test_command_shape` contract (one runner + benign segments)
  reason : the rule that decided `kind`

`sample` carries a hand-read subset: for each, `expected_scope` is what a
human reading the command says the run's extent is, written down before the
classifier was consulted on that row.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"D:\gt-context-plan")
import verif  # noqa: E402

CORPUS = r"D:\gt-context-plan\tests\fixtures\command_corpus\model_test_commands.json"
OUT = r"D:\gt-context-plan\tests\fixtures\benchmarks\model_command_labels.json"

# index -> (expected extent, why)
SAMPLE = {
    # --- cargo ---
    91: ("scoped", "--test tests selects one integration target plus a name filter"),
    799: ("scoped", "-p pest_meta --lib plus a single test-name filter"),
    806: ("scoped", "-p boa_engine --test evaluation_cancellation plus a filter"),
    818: ("scoped", "-p oxvg_optimiser selects one workspace package"),
    824: ("scoped", "-p pest_meta --lib plus a name filter"),
    846: ("suite", "--workspace --all-features selects everything"),
    # --- go ---
    270: ("scoped", "./object names one package"),
    290: ("scoped", "./evaluator/... names one package subtree"),
    292: ("scoped", "./parser/... plus a -run regexp"),
    831: ("unknown", "the package set comes from a backtick command substitution "
                     "evaluated at run time; the extent is not readable from the text"),
    832: ("scoped", "one fully qualified import path"),
    1235: ("scoped", "./evaluator plus -run TestRequire"),
    # --- jsrunner ---
    355: ("suite", "npx jest with no selection"),
    356: ("suite", "npx jest with no selection"),
    363: ("suite", "npx jest with no selection"),
    383: ("scoped", "one test file plus -t name filter"),
    836: ("scoped", "two explicit spec files"),
    914: ("suite", "npx vitest run with no selection"),
    # --- make ---
    263: ("suite", "make test with no target selection"),
    835: ("scoped", "make test with an extra per-file target"),
    885: ("suite", "make test with no target selection"),
    886: ("suite", "make test with no target selection"),
    887: ("suite", "make test with no target selection"),
    888: ("suite", "make test with no target selection"),
    # --- npm / package-manager lifecycle ---
    205: ("suite", "pnpm test inside the ark/json-schema package, no selection"),
    260: ("suite", "npm test with no selection"),
    261: ("suite", "npm test with no selection"),
    335: ("suite", "npm test -- --runInBand passes a runner flag, not a selection"),
    966: ("scoped", "npm test -- <file> -t <name>"),
    972: ("scoped", "npm test -- <file> -t <name>"),
    # --- pytest ---
    407: ("scoped", "one test file plus -k filter"),
    539: ("suite", "python -m pytest -q with no selection"),
    574: ("scoped", "one ::nodeid"),
    788: ("scoped", "one test file"),
    1100: ("suite", "python -m pytest -v with no selection"),
    1179: ("scoped", "two explicit test files"),
}


def family(cmd):
    segs = verif.segments(cmd)[0] or []
    for seg in segs:
        if not verif.is_runner(seg):
            continue
        words = verif.program_words(seg)
        head = words[0]
        if head in ("python", "python3", "python2", "pytest", "py.test", "tox", "nox", "stestr"):
            return "pytest"
        if head == "go":
            return "go"
        if head == "cargo":
            return "cargo"
        if head == "deno":
            return "deno"
        if head == "make":
            return "make"
        if head in ("npm", "pnpm", "yarn", "bun", "npx", "bunx", "pnpx"):
            positional = [w for w in words[1:] if not w.startswith("-")]
            if positional and verif._base(positional[0]) in verif.RUNNER_NAMES:
                return "jsrunner"
            return "npm"
        if head in verif.RUNNER_NAMES:
            return "jsrunner"
    return None


def main():
    corpus = json.load(open(CORPUS, encoding="utf-8"))
    labels = []
    for index, cmd in enumerate(corpus):
        kind, reason = verif.label(cmd)
        labels.append({"index": index, "family": family(cmd),
                       "kind": kind, "reason": reason})
    sample = []
    for index in sorted(SAMPLE):
        expected, why = SAMPLE[index]
        sample.append({"index": index, "command": corpus[index],
                       "family": labels[index]["family"],
                       "kind": labels[index]["kind"],
                       "expected_scope": expected, "rationale": why})
    json.dump({"source": "tests/fixtures/command_corpus/model_test_commands.json",
               "labels": labels, "sample": sample},
              open(OUT, "w", encoding="utf-8"), indent=1)
    print("labels", len(labels), "sample", len(sample), "bytes", os.path.getsize(OUT))


if __name__ == "__main__":
    main()
