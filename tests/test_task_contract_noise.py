from __future__ import annotations

import gt_engine.task_contract as tc


def test_workflow_noise_is_filtered():
    assert tc._is_workflow_noise("read and analyze the repository carefully")
    assert tc._is_workflow_noise(
        "learn or recall the knowledge regarding the common weakness enumeration (CWE)"
    )
    assert tc._is_workflow_noise("Input Validation & Injection")
    assert tc._is_workflow_noise(
        "CWE-116: Improper Encoding or Escaping of Output - Leads to injection issues"
    )


def test_real_requirements_survive_noise_filter():
    assert not tc._is_workflow_noise(
        "create a /app/report.jsonl file in /app folder and report the code vulnerability"
    )
    assert not tc._is_workflow_noise("Make all test cases pass")
    assert not tc._is_workflow_noise(
        "fix the code vulnerability by modify the code file accordingly"
    )


def test_markdown_candidates_skip_catalog_and_workflow_rows():
    text = (
        "You need to identify and fix the vulnerability according to CWE.\n"
        "1. read and analyze the repository carefully\n"
        "2. learn or recall the knowledge regarding CWE\n"
        "3. identify the code vulnerability in /app/bottle.py\n"
        "create a /app/report.jsonl file in /app folder and report it.\n"
        "It should contain vulnerable items.\n"
        "1. Input Validation & Injection\n"
        "CWE-116: Improper Encoding or Escaping of Output\n"
    )
    candidates = tc._markdown_candidates(text)
    joined = "\n".join(t for _, t in candidates).lower()
    assert "read and analyze the repository" not in joined
    assert "learn or recall" not in joined
    assert "input validation & injection" not in joined
    assert "cwe-116" not in joined
    assert "create a /app/report.jsonl file" in joined
    assert "vulnerable items" in joined


def test_extract_task_contract_prefers_engine_rows_over_noise():
    text = (
        "You must fix the compute() bug in src/mod.py.\n"
        "1. read and analyze the repository carefully\n"
        "create a /app/report.jsonl file in /app folder.\n"
    )
    contract = tc.extract_task_contract(text)
    texts = " ".join(o.text for o in contract.obligations).lower()
    assert "read and analyze the repository carefully" not in texts
    assert "report.jsonl" in texts


def test_terminal_bench_harness_scaffolding_is_not_a_task_obligation():
    text = """Please solve this issue: Write me data.comp that's compressed such that
running cat data.comp | /app/decomp gives exactly data.txt.
You can generate data.comp any way you want, but data.comp must be at most 2500 bytes.

You can execute bash commands and edit files to implement the necessary changes.

## Recommended Workflow
1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again

## Command Execution Rules
1. You issue at least one command
2. The system executes the command(s) in a subshell
"""

    contract = tc.extract_task_contract(text)
    obligations = [item.text.lower() for item in contract.obligations]

    assert len(obligations) == 2
    assert any("gives exactly data.txt" in item for item in obligations)
    assert any("at most 2500 bytes" in item for item in obligations)
    assert all("workflow" not in item for item in obligations)
    assert all("subshell" not in item for item in obligations)
