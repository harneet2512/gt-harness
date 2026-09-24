"""Fixture-local test shims.

``pyapp/server.py`` deliberately imports ``fastapi``/``flask`` so the
producer's ``framework_surface_resolution_v1`` edges (Depends injection,
route decorators, middleware) have real import surfaces to bind. Those
packages are NOT declared dependencies of the harness, so a clean verify
venv cannot import this module at test time.

pytest loads this conftest before collecting ``pyapp/test_server.py``;
it installs minimal ``sys.modules`` stubs that satisfy the import-time
surface only (``Depends``, ``Flask`` decorators, ``request``). The stubs
never run — the fixture tests exercise pure functions — and they change
nothing statically: ``import fastapi`` still resolves to an external
module in the index, exactly as before.
"""

import sys
import types

if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")
    fastapi.Depends = lambda dependency=None: dependency  # noqa: E731
    sys.modules["fastapi"] = fastapi

if "flask" not in sys.modules:
    flask = types.ModuleType("flask")

    class Flask:  # noqa: D401 - minimal decorator-emitting shim
        def __init__(self, name):
            self.name = name

        def _decorator(self, *args, **kwargs):
            # bare usage (@app.before_request) passes the function itself;
            # parameterized usage (@app.get("/x")) returns a decorator.
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            return lambda fn: fn

        before_request = _decorator
        route = _decorator
        get = _decorator
        post = _decorator
        use = _decorator

    flask.Flask = Flask
    flask.request = types.SimpleNamespace(args={})
    sys.modules["flask"] = flask
