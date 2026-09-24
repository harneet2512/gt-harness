"""Flask-style order API for the canonical static-intelligence fixture.

Deliberately small but shaped like a real service: route decorators,
a before_request middleware hook, Depends-style dependency injection,
a callback dispatcher, a class hierarchy with overrides, and a
request -> sanitizer -> shell taint chain.
"""

import subprocess

from fastapi import Depends
from flask import Flask, request

app = Flask(__name__)


class Store:
    """Dependency-injected record store."""

    def fetch(self, key):
        return {"key": key, "found": True}


class Renderer:
    """Base renderer; subclasses override both methods."""

    def render(self, payload):
        return str(payload)

    def content_type(self):
        return "text/plain"


class JsonRenderer(Renderer):
    def render(self, payload):
        return repr(payload)

    def content_type(self):
        return "application/json"


def get_store():
    return Store()


def audit(event):
    return {"audited": event}


def dispatch(event, callback):
    return callback(event)


def sanitize(raw):
    if raw and raw.startswith("-"):
        return raw[1:]
    return raw


def run_query(command):
    return execute(command)


def execute(command):
    return subprocess.run(command, shell=True)


def format_greeting(name):
    return "hello " + sanitize(name)


@app.before_request
def log_request():
    dispatch("request", audit)
    return None


@app.get("/api/items")
def list_items(store: Store = Depends(get_store)):
    raw = request.args.get("q")
    probe = run_query(raw)
    cleaned = sanitize(raw)
    out = run_query(cleaned)
    return {"out": str(out), "echo": cleaned, "probe": probe}


@app.route("/api/render")
def render_item(store: Store = Depends(get_store)):
    renderer = JsonRenderer()
    payload = store.fetch(request.args.get("key"))
    return renderer.render(payload)
