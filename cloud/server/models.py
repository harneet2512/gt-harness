"""Pydantic request/response schemas for the cloud chat API."""
from __future__ import annotations

from typing import Any, Literal

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
SessionRole = Literal["primary", "worker"]
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
