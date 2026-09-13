# Documentation-only environment definition.
#
# pier does NOT build this file when [environment].docker_image is set in
# task.toml: with an agent install spec, pier generates its own Dockerfile of
# the form `FROM <docker_image>` + agent install layers (see
# pier/environments/agent_setup.py::write_agent_dockerfile). This file exists
# because DockerEnvironment._validate_definition() requires a Dockerfile or
# docker-compose.yaml to exist in the environment/ directory.
#
# The effective agent image is the pinned SWE-bench-Live instance image below
# (identical to [environment].docker_image in task.toml).
FROM starryzhang/sweb.eval.x86_64.cyclotruc_1776_gitingest-94@sha256:0698979e10aee6ee1b46607d7c86e857218d03df3e62524abb00ea8bffeba394

WORKDIR /testbed
