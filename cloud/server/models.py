"""Pydantic request/response schemas for the cloud chat API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SessionStatusName = Literal["creating", "idle", "running", "failed", "closed"]
GtStatusName = Literal["off", "ready", "unavailable", "pending"]
RoleName = Literal["user", "agent", "system"]
FinishReason = Literal[
    "reply", "question", "step_limit", "time_limit", "stopped", "error",
    "submitted",
]
#: why a session is ``closed``: an explicit close, the idle TTL reaper, or a
#: failure. ``null`` while the session is still alive.
ClosedReason = Literal["user", "expired", "failed"]
Delivery = Literal["turn_started", "queued_for_running_turn"]
FileStatus = Literal["added", "modified", "deleted"]
EdgeKind = Literal["import", "gt_call", "gt_ref", "gt_import"]


class SessionCreate(BaseModel):
    repo: str = Field(..., description="GitHub repo URL (https://github.com/owner/repo)")
    ref: str = Field("main", description="Git ref — branch, tag, or SHA")
    model: str = Field(..., description="LiteLLM model identifier")
    gt_mode: str = Field("off", description="GT mode: off | advisory | engine")
    step_limit: int = Field(60, ge=1, le=500, description="Max model calls per turn")
    #: per-turn wall-clock budget. Unset means "use TURN_WALL_SECONDS"
    #: (default 900), so the server default is configurable in one place.
    wall_seconds: int | None = Field(
        None, ge=60, le=3600, description="Per-turn wall-clock budget in seconds"
    )
    temperature: float = Field(0.0, ge=0.0, le=2.0)


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
    current_turn_id: str | None = None
    closed_reason: ClosedReason | None = None


class MessageMeta(BaseModel):
    finish_reason: FinishReason | None = None
    n_calls: int | None = None
    cost: float | None = None
    patch_sha256: str | None = None
    files_changed: list[str] | None = None


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


class MessageAccepted(BaseModel):
    message: Message
    delivery: Delivery


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
    finish_reason: str = ""
    patch_sha256: str | None = None
    gt_status: str = "off"
    model: str = ""


class SessionEvent(BaseModel):
    id: int
    type: str
    timestamp: float
    data: dict[str, Any] = Field(default_factory=dict)
