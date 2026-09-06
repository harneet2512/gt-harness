"""Client-side adapters that report a local coding agent's activity to the cloud UI.

Nothing in this package is imported by ``cloud.server``. The modules here run on
a *user's own machine*, next to Claude Code, Codex or any other agent, and post
to the cloud deployment over HTTPS. They depend on the standard library only, so
a person can copy a single file onto a machine that has no virtualenv.
"""
