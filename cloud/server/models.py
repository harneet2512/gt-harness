"""Pydantic request/response schemas for the cloud chat API."""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

SessionStatusName = Literal["creating", "idle", "running", "failed", "closed"]
GtStatusName = Literal["off", "ready", "unavailable", "pending"]
#: The GT modes a session may ask for. These are **members of
#: ``gt_engine.gt_session.GTMode``** (``off|shadow|advisory|assistive|
#: enforced``), because ``runner._install_gt`` passes the value straight to
#: ``GTMode(gt_mode)``. ``shadow`` is deliberately not offered: it runs the
#: engine without letting it affect the agent, which is a benchmark mode, not
#: a product one.
#:
#: HAR-84 G-02: ``"engine"`` used to be documented here and offered by the UI.
#: It was **never** a ``GTMode`` member, so every ``engine`` session raised
#: ``ValueError: 'engine' is not a valid GTMode`` on its first turn and
#: silently degraded to ``gt_status: unavailable``. It is not accepted any
#: more — an unknown mode is a 422 at creation instead of a broken session.
GtModeName = Literal["off", "advisory", "assistive", "enforced"]
RoleName = Literal["user", "agent", "system"]
#: ``primary`` is a session a user created; ``worker`` is one a session
#: spawned with ``POST /api/sessions/{id}/agents`` (see ``Session.parent_id``).
#: ``external`` is an agent we do **not** run — a local Claude Code or Codex
#: session (or one of *their* subagents) that registers itself and pushes its
#: own events at us. It is a worker we never execute: same row, same mirror
#: path, no workspace, no sandbox, no model call, no concurrency slot.
SessionRole = Literal["primary", "worker", "external"]
#: how many tasks one spawn call may carry
MAX_TASKS_PER_SPAWN = 4
FinishReason = Literal[
    "reply", "question", "step_limit", "time_limit", "stopped", "error",
    "submitted",
    #: the server restarted while this turn was running; recover() closes it
    "interrupted",
]

#: control characters are never legal in a git ref, and a ref that starts with
#: ``-`` would be read as a flag by ``git clone``/``git fetch``
_CONTROL_CHARS = frozenset(chr(c) for c in [*range(0x20), 0x7F])


def _clean_ref(value: str) -> str:
    """Validate a git ref: non-blank, no control characters, not a flag."""
    if not value or not value.strip():
        raise ValueError("ref must not be blank")
    if any(ch in _CONTROL_CHARS for ch in value):
        raise ValueError("ref must not contain control characters")
    if value.strip() != value:
        raise ValueError("ref must not have leading or trailing whitespace")
    if value.startswith("-"):
        # `git clone --branch <ref>` would read it as a flag.
        raise ValueError("ref must not start with '-'")
    return value
#: why a session is ``closed``: an explicit close, the idle TTL reaper, or a
#: failure. ``null`` while the session is still alive.
ClosedReason = Literal["user", "expired", "failed"]
#: ``spawned`` means the message was a ``/spawn`` command: no turn was
#: started and the message in the response is the server's system note.
Delivery = Literal["turn_started", "queued_for_running_turn", "spawned"]
FileStatus = Literal["added", "modified", "deleted"]
EdgeKind = Literal["import", "gt_call", "gt_ref", "gt_import"]


class SessionCreate(BaseModel):
    repo: str = Field(..., description="GitHub repo URL (https://github.com/owner/repo)")
    ref: str = Field(
        "main", max_length=256, description="Git ref — branch, tag, or full SHA"
    )
    model: str = Field(..., min_length=1, description="LiteLLM model identifier")
    gt_mode: GtModeName = Field(
        "off", description="GT mode: off | advisory | assistive | enforced"
    )
    step_limit: int = Field(60, ge=1, le=500, description="Max model calls per turn")
    #: per-turn wall-clock budget. Unset means "use TURN_WALL_SECONDS"
    #: (default 900), so the server default is configurable in one place.
    wall_seconds: int | None = Field(
        None, ge=60, le=3600, description="Per-turn wall-clock budget in seconds"
    )
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    #: an opening message. When set, the first turn starts by itself as soon
    #: as the workspace is ready — no second call, no polling for ``idle``.
    first_message: str | None = Field(
        None,
        max_length=100_000,
        description="Optional opening message; starts the first turn itself",
    )

    @field_validator("first_message")
    @classmethod
    def _check_first_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("first_message must not be blank")
        return value

    @field_validator("ref")
    @classmethod
    def _check_ref(cls, value: str) -> str:
        return _clean_ref(value)

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        # Blank is a 422 here; a *syntactically* fine model that the provider
        # does not serve is a 400 from the creation preflight (see
        # ``runner.check_model``), because only the provider can say so.
        if not value.strip():
            raise ValueError("model must not be blank")
        return value


class WorkerReport(BaseModel):
    """What a worker told its parent when a turn of its own ended."""

    finish_reason: FinishReason | str
    #: the opening of the worker's reply, bounded for a list view
    reply_excerpt: str = ""
    patch_sha256: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    #: whether this worker's patch has been applied to the parent workspace
    applied: bool = False


class Session(BaseModel):
    id: str
    status: SessionStatusName
    repo: str
    ref: str
    model: str
    gt_mode: str
    gt_status: GtStatusName
    #: why GT is unavailable, in the indexer's own words; null when it is not.
    #: Survives a reload, unlike the ``gt_unavailable`` lifecycle event.
    gt_error: str | None = None
    created_at: float
    updated_at: float
    last_message: str | None = None
    turns: int = 0
    steps: int = 0
    cost: float = 0.0
    #: wall-clock seconds summed over finished turns. Cost is always 0.0 with
    #: ``MSWEA_COST_TRACKING=ignore_errors``, so this is the honest budget line.
    total_wall_seconds: float = 0.0
    #: GroundTruth typed actions this session has run, summed over its turns
    gt_actions: int = 0
    current_turn_id: str | None = None
    closed_reason: ClosedReason | None = None
    #: the session that spawned this one; null for a primary session
    parent_id: str | None = None
    role: SessionRole = "primary"
    #: the task a worker was spawned with; null on a primary session
    task: str | None = None
    #: a worker's last report to its parent; null until it has finished a turn
    report: WorkerReport | None = None
    #: when this worker's patch was applied to the parent workspace
    applied_at: float | None = None
    #: EXTERNAL agents only — what kind of agent is reporting in
    #: (``claude-code`` | ``codex`` | ``other``; a free lowercase slug)
    agent_kind: str | None = None
    #: EXTERNAL agents only — set when this agent is a **subagent of another
    #: external agent**. ``parent_id`` still points at the owning *session*,
    #: so nesting is a second edge, not a different parent.
    parent_agent_id: str | None = None
    #: EXTERNAL agents only — the absolute path the external agent says it
    #: runs in. Display only: the server never touches the filesystem with it.
    external_cwd: str | None = None
    #: EXTERNAL agents only — the human-readable name of the agent, so a
    #: reload of ``GET /sessions/{id}/agents`` can still label the card.
    label: str | None = None
    #: EXTERNAL agents only — one line saying what this agent is doing right
    #: now, as it last reported it. Display only.
    activity: str | None = None
    #: EXTERNAL agents only — cumulative tokens the client has reported.
    #: ``null`` when it never reported any: a token count is never
    #: synthesised, because a made-up number is worse than a blank.
    tokens: int | None = None


class MessageMeta(BaseModel):
    finish_reason: FinishReason | None = None
    n_calls: int | None = None
    cost: float | None = None
    patch_sha256: str | None = None
    files_changed: list[str] | None = None
    #: set on a ``role: "agent"`` message a WORKER reported into its parent's
    #: conversation; absent on the parent's own agent replies
    agent_id: str | None = None


class Message(BaseModel):
    id: str
    session_id: str
    turn_id: str | None = None
    role: RoleName
    content: str
    created_at: float
    meta: MessageMeta = Field(default_factory=MessageMeta)


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """``"   "`` is not a message (HAR-84 G-12).

        ``min_length`` alone only caught ``""``, so whitespace started a real
        turn and burned model calls and a concurrency slot.
        """
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class MessageAccepted(BaseModel):
    message: Message
    delivery: Delivery


class AgentSpawn(BaseModel):
    """``POST /api/sessions/{id}/agents`` — one worker per task."""

    tasks: list[str] = Field(..., min_length=1, max_length=MAX_TASKS_PER_SPAWN)
    #: default: the parent's model / gt_mode
    model: str | None = None
    gt_mode: GtModeName | None = None

    @field_validator("tasks")
    @classmethod
    def _check_tasks(cls, value: list[str]) -> list[str]:
        cleaned = [task for task in value if task and task.strip()]
        if len(cleaned) != len(value):
            raise ValueError("a task must not be blank")
        if any(len(task) > 100_000 for task in cleaned):
            raise ValueError("a task must be at most 100000 characters")
        return cleaned


class AgentsSpawned(BaseModel):
    workers: list[Session] = Field(default_factory=list)


class AgentApplied(BaseModel):
    """The result of merging a worker's diff into the parent workspace."""

    worker_id: str
    files: list[str] = Field(default_factory=list)
    patch_sha256: str | None = None


# --------------------------------------------------------------------------
# external agents: agents we do not run
# --------------------------------------------------------------------------
#: what an ``agent_kind`` may look like. A free string on purpose (the owner
#: will point this at things we have not heard of yet), but a bounded, boring
#: one: it is rendered as a CSS class and a badge, and it is client-supplied.
AGENT_KIND_RE = re.compile(r"^[a-z0-9-]+$")
MAX_AGENT_KIND_CHARS = 32
MAX_AGENT_LABEL_CHARS = 80
MAX_AGENT_CWD_CHARS = 512
MAX_FINISH_SUMMARY_CHARS = 4_000
#: one line of "what it is doing right now", for the fleet list
MAX_ACTIVITY_CHARS = 200

#: ingest event caps. Every one of these TRUNCATES rather than rejects: a
#: chatty adapter losing a whole batch to a 422 is worse than a clipped line.
MAX_INGEST_TEXT_CHARS = 20_000
MAX_INGEST_TOOL_NAME_CHARS = 64
MAX_INGEST_COMMAND_CHARS = 4_000
MAX_INGEST_OUTPUT_CHARS = 8_000
MAX_INGEST_NOTE_CHARS = 4_000
#: files per event, and characters per path
MAX_INGEST_FILES = 50
MAX_INGEST_PATH_CHARS = 512
#: events per ``POST /api/external-agents/{id}/events`` body
MAX_INGEST_BATCH = 100

ExternalAgentState = Literal["working", "idle", "done", "error"]
ExternalFinishStatus = Literal["done", "error"]


def _clip(value: str | None, limit: int) -> str | None:
    return value if value is None else value[:limit]


def _clip_files(files: list[str] | None) -> list[str] | None:
    """Bound the LIST here; the paths themselves are the runner's business.

    Truncation, never rejection: a client that names 80 files still has 50
    useful ones. The per-path cap is deliberately NOT applied here — an
    over-long path must be *dropped* by ``runner._clean_file``, not silently
    clipped into a 512-character label that names a file nobody touched.
    """
    if files is None:
        return None
    return [
        str(path) for path in files[:MAX_INGEST_FILES] if isinstance(path, str)
    ]


class ExternalChildCreate(BaseModel):
    """``POST /api/external-agents/{id}/children`` — a subagent of the caller.

    The same body as :class:`ExternalAgentCreate` minus ``parent_agent_id``,
    which is not a parameter here: the parent IS the token's own agent. A
    token that could name its own parent could graft a card anywhere in the
    tree, and the token is the only thing this route authenticates.
    """

    agent_kind: str = Field(..., max_length=MAX_AGENT_KIND_CHARS)
    label: str = Field(..., max_length=MAX_AGENT_LABEL_CHARS)
    task: str | None = Field(None, max_length=100_000)
    #: the absolute path the agent runs in, as *it* reported it. Display only.
    cwd: str | None = Field(None, max_length=MAX_AGENT_CWD_CHARS)

    @field_validator("agent_kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        kind = value.strip().lower()
        if not AGENT_KIND_RE.match(kind):
            raise ValueError("agent_kind must match [a-z0-9-]+")
        return kind

    @field_validator("label")
    @classmethod
    def _check_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("label must not be blank")
        return value.strip()


class ExternalAgentCreate(ExternalChildCreate):
    """``POST /api/sessions/{id}/external-agents`` — register, never execute."""

    #: the external agent this one is a subagent of, if any
    parent_agent_id: str | None = Field(None, max_length=64)


class ExternalAgentRegistered(BaseModel):
    """The registration answer: the row, and how to push events into it."""

    agent: Session
    #: a stateless JWT scoped to this agent — the ONLY credential the events
    #: and finish endpoints accept, and it is not accepted anywhere else
    ingest_token: str
    ingest_url: str


class IngestAssistant(BaseModel):
    type: Literal["assistant"]
    text: str = ""
    #: the client's own clock. Advisory: the server always stamps its own.
    ts: float | None = None

    @field_validator("text")
    @classmethod
    def _clip_text(cls, value: str) -> str:
        return value[:MAX_INGEST_TEXT_CHARS]


class IngestToolCall(BaseModel):
    type: Literal["tool_call"]
    name: str = ""
    command: str | None = None
    files: list[str] | None = None
    #: the fleet list's "doing right now" line, so a tool call can set it
    #: without a second event
    activity: str | None = None
    ts: float | None = None

    @field_validator("activity")
    @classmethod
    def _clip_activity(cls, value: str | None) -> str | None:
        return _clip(value, MAX_ACTIVITY_CHARS)

    @field_validator("name")
    @classmethod
    def _clip_name(cls, value: str) -> str:
        return value[:MAX_INGEST_TOOL_NAME_CHARS]

    @field_validator("command")
    @classmethod
    def _clip_command(cls, value: str | None) -> str | None:
        return _clip(value, MAX_INGEST_COMMAND_CHARS)

    @field_validator("files")
    @classmethod
    def _bound_files(cls, value: list[str] | None) -> list[str] | None:
        return _clip_files(value)


class IngestToolResult(BaseModel):
    type: Literal["tool_result"]
    name: str = ""
    ok: bool = True
    output: str | None = None
    files: list[str] | None = None
    ts: float | None = None

    @field_validator("name")
    @classmethod
    def _clip_name(cls, value: str) -> str:
        return value[:MAX_INGEST_TOOL_NAME_CHARS]

    @field_validator("output")
    @classmethod
    def _clip_output(cls, value: str | None) -> str | None:
        return _clip(value, MAX_INGEST_OUTPUT_CHARS)

    @field_validator("files")
    @classmethod
    def _bound_files(cls, value: list[str] | None) -> list[str] | None:
        return _clip_files(value)


class IngestStatus(BaseModel):
    type: Literal["status"]
    state: ExternalAgentState
    note: str | None = None
    #: what the agent is doing right now, for the fleet list
    activity: str | None = None
    #: cumulative tokens the client has spent. Monotonic on the server: a
    #: value below the stored one is ignored rather than moving it backwards.
    tokens: int | None = Field(None, ge=0)
    ts: float | None = None

    @field_validator("note")
    @classmethod
    def _clip_note(cls, value: str | None) -> str | None:
        return _clip(value, MAX_INGEST_NOTE_CHARS)

    @field_validator("activity")
    @classmethod
    def _clip_activity(cls, value: str | None) -> str | None:
        return _clip(value, MAX_ACTIVITY_CHARS)


#: There is deliberately no ``subagent`` event type: a nested subagent
#: registers as its own external agent with ``parent_agent_id`` set, so it is
#: a row and a card like any other rather than a special frame.
IngestEvent = Annotated[
    IngestAssistant | IngestToolCall | IngestToolResult | IngestStatus,
    Field(discriminator="type"),
]


class IngestBatch(BaseModel):
    events: list[IngestEvent] = Field(..., max_length=MAX_INGEST_BATCH)


class IngestAccepted(BaseModel):
    """How many of the batch's events actually reached the parent's stream."""

    accepted: int = 0


class ExternalAgentFinish(BaseModel):
    status: ExternalFinishStatus
    #: The closing summary. Like every other string on the ingest path this
    #: **truncates rather than rejects** (HAR-84, found live): a summary one
    #: character over the cap used to 422, and because ``finish`` is the only
    #: way a card settles, the agent stayed ``running`` for ever with its last
    #: activity reading "Finished". A summary is a courtesy; the lifecycle is
    #: not, so the summary must never be able to block it.
    summary: str | None = None

    @field_validator("summary")
    @classmethod
    def _clip_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value[:MAX_FINISH_SUMMARY_CHARS]


class DiffFile(BaseModel):
    path: str
    status: FileStatus
    additions: int = 0
    deletions: int = 0
    #: this file's own ``diff --git`` block, carved out of the combined patch
    patch: str = ""


class SessionDiff(BaseModel):
    patch: str = ""
    files: list[DiffFile] = Field(default_factory=list)
    base_sha: str = ""
    #: the three below are present only for ``/diff?through_event=N``
    #: id of the ``tool_result`` event this diff is the state after (0: none yet)
    as_of_event: int | None = None
    #: always false — this is a stored snapshot, not the UI's reconstruction
    approximate: bool | None = None
    #: only present (and true) when the stored patch hit the 512 KB cap
    truncated: bool | None = None


class TreeFile(BaseModel):
    path: str
    size: int = 0


class SessionTree(BaseModel):
    base_sha: str = ""
    files: list[TreeFile] = Field(default_factory=list)


class GraphNode(BaseModel):
    #: identical to ``path``; the UI keys its layout off ``id``
    id: str
    path: str
    size: int = 0
    #: file extension without the dot ("py", "tsx", …), "" when there is none
    lang: str = ""
    #: first path segment, "" for a file at the repo root
    dir: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    #: how many underlying relations (imports, or GT symbol edges) collapsed here
    weight: int = 1


class SessionGraph(BaseModel):
    base_sha: str = ""
    #: true when GT-derived edges are included
    gt: bool = False
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    #: only present (and true) when the graph was capped to the busiest files
    truncated: bool | None = None


class TurnReceipt(BaseModel):
    turn_id: str
    started_at: float
    finished_at: float | None = None
    n_calls: int = 0
    cost: float = 0.0
    #: how long the turn actually took, start to finish
    wall_seconds: float = 0.0
    #: GroundTruth typed actions this turn ran (one ``gt_action`` frame each)
    gt_actions: int = 0
    #: of those, the ones that answered: ``semantics == "exact"`` and
    #: ``match_count > 0``. An exact abstention over an empty scope is a GT
    #: action, not an answer.
    gt_exact_matches: int = 0
    finish_reason: str = ""
    patch_sha256: str | None = None
    gt_status: str = "off"
    model: str = ""


class SessionEvent(BaseModel):
    id: int
    type: str
    timestamp: float
    data: dict[str, Any] = Field(default_factory=dict)
