from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PIN = "4967e0080cef47f614b1761a3152b784c0355a30"


def _operative_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.lstrip() and not line.lstrip().startswith("#")
    ]


def test_active_workflows_do_not_build_or_select_a_vendored_gt_index():
    forbidden = ("vendor/gt-index-src", "vendor/gt-index-linux-amd64")
    violations = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(_operative_lines(path.read_text(encoding="utf-8")), 1):
            if any(token in line for token in forbidden):
                violations.append(f"{path.name}:{number}: {line}")
    message = "operative workflows still bypass the pinned producer:\n" + "\n".join(violations)
    assert not violations, message


def test_active_external_producer_workflows_use_the_single_immutable_pin_and_helper():
    required = (
        ".github/workflows/tb2_miniswe_gt_single.yml",
        ".github/workflows/tb2_miniswe_product.yml",
        ".github/workflows/tb2_miniswe_ox_alpha_diagnostic.yml",
        ".github/workflows/tb2_miniswe_central.yml",
        ".github/workflows/deepswe_gt_harness_product.yml",
        ".github/workflows/arb_gt_retrieval.yml",
    )
    for relative in required:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert f"ref: {PIN}" in text, relative
        assert "path: .gt-index-source" in text, relative
        assert "bash scripts/build_external_gt_index.sh" in text, relative
