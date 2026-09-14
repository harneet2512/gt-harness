"""Churn-governor contract tests.

Threshold semantics mirror run 34656860834: churn onset ~turn 30, two edit
transactions in 840 commands, 54% archaeology commands. The governor must
steer early, abort long before the deadline, and stay quiet on a healthy
edit/test cadence.
"""
from __future__ import annotations

import json
from pathlib import Path

from gt_engine.churn_governor import ChurnGovernor

_FIXTURES = Path(__file__).parent / "fixtures" / "smoke20_recorded"


def _feed(g: ChurnGovernor, commands: list[str], productive: set[int] | None = None):
    productive = productive or set()
    signals = []
    for i, cmd in enumerate(commands):
        signals.append(g.observe(cmd, productive=i in productive))
    return signals


def test_healthy_run_stays_quiet():
    g = ChurnGovernor()
    commands = ["ls", "cat src/a.py", "edit a.py", "pytest -q"] * 20
    signals = _feed(g, commands)
    assert set(signals) <= {""}
    assert not g.aborted


def test_reads_alone_do_not_abort_indefinitely_but_eventually_stall():
    """Pure reading is not churn, but 50 actions without a single edit or
    check is a doomed run even when the reads are task files."""
    g = ChurnGovernor()
    commands = ["cat file%d.py" % i for i in range(60)]
    signals = _feed(g, commands)
    assert signals[-1] == "abort"
    assert g.aborted


def test_source_exploration_stall_steers_before_abort():
    """The delegation-task pattern: 50 consecutive reads/searches of source
    files - not GT archaeology - stalled to an abort with zero steers. Any
    sustained unproductive stall earns a steer; an abort may only fire after
    a steer had its grace turns to work."""
    g = ChurnGovernor()
    commands = ["cat src/file%d.py" % i for i in range(60)]
    signals = _feed(g, commands)
    assert "steer" in signals
    assert signals.index("steer") < signals.index("abort")
    assert g.steers_issued >= 1


def test_stall_abort_requires_prior_steer():
    """An abort with steers_issued == 0 is a policy bug, not a tuning issue:
    the governor may only kill a stall it already tried to correct. With
    steering disabled by configuration the plain limit is the only guard."""
    g = ChurnGovernor()
    _feed(g, ["cat src/f%d.py" % i for i in range(55)])
    assert g.aborted
    assert g.steers_issued >= 1

    unsteered = ChurnGovernor(max_steers=0)
    signals = _feed(unsteered, ["cat src/f%d.py" % i for i in range(55)])
    assert signals[-1] == "abort"
    assert unsteered.steers_issued == 0


def test_archaeology_steer_then_abort_mirrors_the_paid_run():
    """The paid-run pattern: exploration, then gt-state/trajectory mining
    with no edits. Steer first, abort when the loop survives the steer."""
    g = ChurnGovernor()
    commands = (
        ["ls", "cat src/node_visitor.py", "cat src/context.py"] * 3
        + ["cat gt-state/deliveries/abc.json",
           "python3 -c 'import json;print(json.load(open(\"/logs/agent/miniswe_trajectory.json\")))'",
           "gt-evidence read 4bb060e 0 8192",
           "grep -rn GT_PERSISTENT_PLAN /logs/agent"] * 20
    )
    signals = _feed(g, commands)
    assert "steer" in signals
    assert signals.index("steer") < signals.index("abort")
    assert g.aborted
    # The abort lands near turn ~60-90, not at turn 769.
    assert signals.index("abort") < 100
    assert g.steers_issued >= 1


def test_edit_after_steer_prevents_abort():
    """A steer that works - the agent goes back to editing - must not abort.
    The churn block is long enough to trip the steer (~25 stalls) but ends
    before the post-steer abort (~40), because the agent recovers."""
    g = ChurnGovernor()
    churn = ["cat gt-state/deliveries/x.json", "gt-evidence read a 0 8192"] * 14
    signals = _feed(g, churn)
    assert "steer" in signals
    recovery = ["edit src/fix.py", "pytest -q", "cat src/fix.py"] * 20
    signals += _feed(g, recovery, productive={3 * i for i in range(20)})
    assert "abort" not in signals[-len(recovery):]


def test_identical_command_hammering_aborts_fast():
    g = ChurnGovernor()
    signals = _feed(g, ["cat same_file.py"] * 20)
    assert signals[-1] == "abort"


def test_verification_commands_reset_stall():
    g = ChurnGovernor()
    commands = (
        ["cat file%d.py" % i for i in range(20)]
        + ["pytest -q"]
        + ["cat file%d.py" % i for i in range(20, 40)]
        + ["pytest -q"]
    )
    signals = _feed(g, commands)
    assert "abort" not in signals
    assert g.stall_turns == 0


def test_disabled_governor_never_fires():
    g = ChurnGovernor(disabled=True)
    signals = _feed(g, ["cat gt-state/x"] * 200)
    assert set(signals) == {""}
    assert not g.aborted


def test_abort_is_sticky():
    g = ChurnGovernor()
    _feed(g, ["cat f.py"] * 60)
    assert g.aborted
    assert g.observe("edit f.py", productive=True) == "abort"


def test_probe_exploration_run_is_not_churn_smoke20_fd():
    """Recorded run 34801009507, fd-deterministic-multi-key-sorting.

    The agent methodically probed the Rust API surface: wrote probe crates to
    /tmp (outside the repo diff, so ``changed_files`` was empty and the runtime
    passed productive=False for every command), compiled and ran them, and
    read toolchain sources. The governor counted 50 consecutive "stalls" and
    killed a working run on a task the GT-off baseline solves 3/4.

    Probe engineering - creating files, compiling, executing build artifacts -
    is productive work whether or not it mutates the repo or matches *test.
    The real command stream must not abort even with no productive flags."""
    fixture = json.loads(
        (_FIXTURES / "fd_churn_abort_commands.json").read_text(encoding="utf-8")
    )
    commands = fixture["commands"]
    assert fixture["real_outcome"] == "churn_abort_stall_50"
    g = ChurnGovernor()
    signals = _feed(g, commands)
    assert "abort" not in signals
    assert not g.aborted


def test_build_and_probe_commands_reset_stall():
    """Compile/run probes are verification-shaped work: an agent that builds
    and executes is investigating, not churning. Only pure reading stalls."""
    g = ChurnGovernor()
    commands = (
        ["cat src/file%d.rs" % i for i in range(20)]
        + ["cd /tmp/probe && cat > src/main.rs <<'EOF'\nfn main(){}\nEOF"]
        + ["cargo build 2>&1 | tail -20", "./target/debug/probe"]
        + ["grep -rn 'pub fn' /root/.rustup/toolchains/*/src/std/"]
    )
    signals = _feed(g, commands)
    assert "abort" not in signals
    assert g.stall_turns < 10


def test_pure_reads_without_writes_still_stall_and_abort():
    """The write/build broadening must not hide the original contract: a run
    that only reads - no writes, no builds, no checks - still aborts."""
    g = ChurnGovernor()
    commands = ["cat file%d.py" % i for i in range(60)]
    signals = _feed(g, commands)
    assert signals[-1] == "abort"
    assert g.aborted


def _feed_rc(g: ChurnGovernor, steps: list[tuple[str, int | None]]):
    return [g.observe(cmd, returncode=rc) for cmd, rc in steps]


def test_failing_verification_streak_earns_convergence_steer():
    """Smoke20 pest/bandit-taint/testem-pl: edit -> test -> edit -> test with
    every check failing burns 2-6x tokens while the stall counter stays at
    zero. A streak of failed verifications earns a convergence steer."""
    g = ChurnGovernor(verify_steer_streak=8)
    steps = []
    for i in range(10):
        steps.append((f"python3 - <<'PY'\nedit{i}\nPY", 0))  # a write, not a check
        steps.append(("pytest -q", 1))                      # failing check
    signals = _feed_rc(g, steps)
    assert "verify_steer" in signals
    assert g.verify_steers_issued >= 1
    assert not g.aborted


def test_failing_verification_streak_never_aborts():
    """An agent grinding through a genuinely hard verification is working,
    not churning - killing it repeats the fd mistake. The streak only ever
    steers, and stops after its own small budget."""
    g = ChurnGovernor(verify_steer_streak=5)
    # Vary the invocations - identical-command hammering is a separate,
    # legitimate abort rule this test is not about.
    steps = [(f"pytest tests/test_mod{i}.py -q", 1) for i in range(60)]
    signals = _feed_rc(g, steps)
    assert "abort" not in signals
    assert not g.aborted
    assert signals.count("verify_steer") == g.max_verify_steers


def test_passing_verification_resets_the_streak():
    g = ChurnGovernor(verify_steer_streak=5)
    _feed_rc(g, [("pytest -q", 1)] * 4)
    assert g.verify_fail_streak == 4
    _feed_rc(g, [("pytest -q", 0)])
    assert g.verify_fail_streak == 0


def test_edits_between_failures_do_not_reset_the_streak():
    """edit -> fail -> edit -> fail is the thrash itself; only a passing
    check proves the loop converged."""
    g = ChurnGovernor(verify_steer_streak=6)
    steps = []
    for i in range(3):
        steps += [("apply_patch src/x.py", 0), ("pytest -q", 1)]
    _feed_rc(g, steps)
    assert g.verify_fail_streak == 3


def test_unknown_returncode_does_not_count_as_failure():
    """A verification with no captured exit code (timeout/kill) must not
    feed the streak - conservative, never steer on unknown."""
    g = ChurnGovernor(verify_steer_streak=4)
    _feed_rc(g, [("pytest -q", None)] * 10)
    assert g.verify_fail_streak == 0


def test_js_test_runners_count_as_verification():
    """vitest/jest/deno are the dominant JS runners - a run of `npx vitest`
    must reset the stall counter, while `cat vitest.config.ts` is a read."""
    g = ChurnGovernor()
    _feed(g, ["cat src/f%d.ts" % i for i in range(30)])
    stall_before = g.stall_turns
    assert stall_before >= 25
    g.observe("cd /app/backend && npx vitest run --reporter=dot", returncode=1)
    assert g.stall_turns == 0
    g.observe("cat vitest.config.ts")
    assert g.stall_turns == 1  # a read, not a check run


def test_verify_steers_do_not_consume_stall_steer_budget():
    """The streak has its own budget so convergence nudges cannot starve
    the stall-steering that precedes a churn abort."""
    g = ChurnGovernor(verify_steer_streak=3)
    _feed_rc(g, [(f"pytest tests/test_m{i}.py -q", 1) for i in range(10)])
    assert g.verify_steers_issued >= 1
    assert g.steers_issued == 0  # stall steers untouched


def test_fd_replay_does_not_verify_steer():
    """The fd run never ran a *test command - its verify streak stays zero
    and the new signal cannot regress the fix that let it live."""
    fixture = json.loads(
        (_FIXTURES / "fd_churn_abort_commands.json").read_text(encoding="utf-8")
    )
    g = ChurnGovernor()
    signals = _feed_rc(g, [(c, 0) for c in fixture["commands"]])
    assert "verify_steer" not in signals
    assert "abort" not in signals


def test_every_binder_admissible_runner_counts_as_verification():
    """Any command the plan binder can bind (TEST_RUNNER_RE) must reset the
    stall counter - a bound check run that reads as a stall is the dead-check
    class reappearing inside the governor. Drift between the binder's runner
    set and the governor's was the ./mvnw / vendor/bin/phpunit / bazel gap."""
    from groundtruth.runtime.patterns import TEST_RUNNER_RE
    from gt_engine.churn_governor import _VERIFICATION, _VERIFICATION_EXTRA

    bound = [
        "pytest -q", "python -m pytest tests/", "python -m unittest", "tox",
        "go test ./...", "cargo test", "cargo nextest run",
        "npm test", "npm run test", "yarn test", "pnpm test",
        "bun test", "deno test", "node --test",
        "npx jest", "node_modules/.bin/jest", "npx vitest run", "npx mocha",
        "bundle exec rspec", "rspec spec/",
        "vendor/bin/phpunit", "phpunit", "composer test",
        "mvn test", "./mvnw test", "gradle test", "./gradlew test",
        "sbt test", "sbt 'testOnly *x*'", "mix test", "dotnet test",
        "bazel test //...", "nx test", "turbo run test", "turbo test",
        "make test", "make check", "ctest", "rake test",
        "python manage.py test", "sudo pytest -q", "nice cargo test",
    ]
    for cmd in bound:
        assert TEST_RUNNER_RE.search(cmd), f"binder rejects: {cmd}"
        assert _VERIFICATION.search(cmd) or _VERIFICATION_EXTRA.search(cmd), (
            f"governor counts a binder-admissible runner as a stall: {cmd}"
        )


def test_non_test_build_commands_earn_engineering_credit():
    """Build/compile/package commands are active engineering (fd class):
    they must not read as verification, but they must not stall either."""
    from gt_engine.churn_governor import _BUILD_OR_EXEC, _VERIFICATION

    builds = [
        "cargo build", "go build ./...", "./mvnw package", "./gradlew build",
        "dotnet build", "sbt compile", "mix compile", "bazel build //...",
        "nx build app", "turbo run build", "composer install", "bundle install",
        "deno task build", "mvn package -DskipTests", "npm run build",
    ]
    for cmd in builds:
        assert not _VERIFICATION.search(cmd), f"build reads as test: {cmd}"
        assert _BUILD_OR_EXEC.search(cmd), f"build command stalls: {cmd}"


def test_prose_cannot_fake_verification():
    """`echo pytest` or a cat of a test log is not a check run - the stall
    counter only resets on a runner at a command boundary."""
    g = ChurnGovernor()
    _feed(g, ["cat src/f%d.py" % i for i in range(30)])
    assert g.stall_turns >= 25
    g.observe("echo pytest -q")
    assert g.stall_turns >= 25  # reset must NOT happen
    g.observe("cat pytest.log")
    assert g.stall_turns >= 26
