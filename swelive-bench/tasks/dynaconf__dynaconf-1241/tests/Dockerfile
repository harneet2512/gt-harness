# Verifier image: the pinned SWE-bench-Live instance image with the hidden
# grading assets baked in. tests/ is the build context (pier passes it as
# environment_dir for the separate verifier environment). The agent never
# sees this container: [verifier].environment_mode = "separate" spins a fresh
# container so /testbed is pristine at the base commit; the only input from
# the agent phase is the collected /logs/artifacts/model.patch.
FROM starryzhang/sweb.eval.x86_64.dynaconf_1776_dynaconf-1241@sha256:c5930010f69611e20ec838903de6d76a8bd61284ffdfb6237fbbc36d881bfed8

COPY test.sh /tests/test.sh
COPY run_tests.sh /tests/run_tests.sh
COPY grade.py /tests/grade.py
COPY spec.json /tests/spec.json
COPY test_patch.diff /tests/test_patch.diff
RUN chmod +x /tests/test.sh /tests/run_tests.sh

WORKDIR /testbed
