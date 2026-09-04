"""Pydantic request/response schemas for the cloud coding agent API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    repo: str = Field(..., description="GitHub repo URL (https://github.com/owner/repo)")
    ref: str = Field("main", description="Git ref — branch, tag, or SHA")
    task: str = Field(..., description="Task description (like a GitHub issue body)")
    model: str = Field("deepseek/deepseek-v4-flash", description="LiteLLM model identifier")
    gt_mode: str = Field("advisory", description="GT mode: advisory | engine | off")
    step_limit: int = Field(100, ge=1, le=500, description="Max agent steps")
    temperature: float = Field(0.0, ge=0.0, le=2.0)


class SessionStatus(BaseModel):
    id: str
    status: str  # pending | running | completed | failed | stopped
    repo: str
    ref: str
    task: str
    model: str
    gt_mode: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    steps: int = 0
    cost: float = 0.0


class SessionResult(BaseModel):
    id: str
    patch: str | None = None
    receipt: dict | None = None
    trajectory: dict | None = None
    terminal_outcome: str = ""


class SessionEvent(BaseModel):
    id: int
    session_id: str
    type: str  # assistant | tool_call | tool_result | steering | lifecycle | error
    data: dict
    timestamp: float


class SteeringMessage(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
