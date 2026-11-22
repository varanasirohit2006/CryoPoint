"""Versioned contracts shared by the compiler, browser, runtime, and API."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterSpec(Contract):
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = "string"
    required: bool = True
    default: Any = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""


class LocatorSpec(Contract):
    strategy: Literal["role", "testid", "label", "placeholder", "text", "css", "xpath"]
    value: str
    name: str | None = None
    tier: int = Field(default=1, ge=1, le=4)
    exact: bool = True


class ConditionSpec(Contract):
    kind: Literal["visible", "text_contains", "text_equals", "value_equals", "attribute_equals", "url_contains", "query_equals", "goal", "language_equals", "title_contains", "url_query_equals", "number_at_most", "number_at_least", "control_activated", "search_submitted", "media_playing"]
    target_index: int | None = None
    objective_index: int | None = Field(default=None, ge=0)
    locator: LocatorSpec | None = None
    locators: list[LocatorSpec] = Field(default_factory=list)
    expected: Any = None
    expected_template: str | None = None
    value_ref: str | None = None
    attribute: str | None = None


class ActionSpec(Contract):
    action: Literal["goto", "click", "fill", "select", "adjust", "press", "scroll", "wait", "handoff"]
    intent: str = ""
    target_index: int | None = None
    locators: list[LocatorSpec] = Field(default_factory=list)
    value: str | None = None
    value_ref: str | None = None
    url: str | None = None
    url_refs: dict[str, str] = Field(default_factory=dict)
    optional: bool = False
    effect: Literal["read", "mutation"] = "read"
    requires_confirmation: bool = False


class ElementFingerprint(Contract):
    index: int
    tag: str = ""
    role: str = ""
    name: str = ""
    testid: str = ""
    label: str = ""
    placeholder: str = ""
    text: str = ""
    context: str = ""
    readonly: bool = False
    options: list[dict[str, str]] = Field(default_factory=list)
    current_value: str | None = None
    checked: bool | None = None
    input_type: str = ""
    parameter_hint: str = ""
    value_text: str = ""
    value_min: float | None = None
    value_max: float | None = None
    value_step: float | None = None
    css: str = ""
    xpath: str = ""
    bounds: dict[str, float] = Field(default_factory=dict)
    goal_control: Literal["query", "submit", "dismiss", "search_link", "filter", "sort", "product", "browse"] | None = None


class Observation(Contract):
    observation_id: str = Field(default_factory=lambda: uuid4().hex)
    url: str
    state_hash: str = ""
    screenshot_base64: str = ""
    screenshot_path: str | None = None
    aria_summary: str = ""
    capture_note: str = ""
    media: list[dict[str, Any]] = Field(default_factory=list)
    submitted_searches: list[dict[str, Any]] = Field(default_factory=list)
    page_issue: dict[str, Any] | None = None
    elements: list[ElementFingerprint] = Field(default_factory=list)


class ActionFrame(Contract):
    action: ActionSpec
    before_url: str = ""
    after_url: str = ""
    before_hash: str = ""
    after_hash: str = ""
    element: ElementFingerprint | None = None
    post_conditions: list[ConditionSpec] = Field(default_factory=list)
    network: list[dict[str, Any]] = Field(default_factory=list)
    screenshot_path: str | None = None
    duration_ms: float = 0
    timestamp: datetime = Field(default_factory=utc_now)
    error: str | None = None
    safe_to_prune: bool = False


class WorkflowStep(Contract):
    step_id: str
    intent: str
    actions: list[ActionSpec]
    post_conditions: list[ConditionSpec] = Field(default_factory=list)
    effect: Literal["read", "mutation"] = "read"


class WorkflowIR(Contract):
    schema_version: int = 1
    runtime_version: int = 1
    workflow_id: str = Field(default_factory=lambda: uuid4().hex)
    version: int = 1
    parent_version: int | None = None
    site_id: str
    goal_id: str
    intent_signature: str
    parameters: dict[str, ParameterSpec]
    steps: list[WorkflowStep]
    goal_conditions: list[ConditionSpec] = Field(default_factory=list)
    source_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    compile_stats: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderResponse(Contract):
    action: ActionSpec | None = None
    done: bool = False
    candidate_id: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    duration_ms: float = 0
    attempts: int = 1
    usage_known: bool = True
    completion_conditions: list[ConditionSpec] = Field(default_factory=list)


class TaskRequest(Contract):
    site_id: Literal["shop", "admin", "amazon_in", "web"]
    goal_id: str = "browser_task"
    task: str = Field(min_length=1, max_length=2000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    provider: Literal["gemini", "ollama", "fixture"] = "gemini"
    mode: Literal["auto", "cold", "hot"] = "auto"
    workflow_id: str | None = None
    compile_workflow: bool = True
    parameter_source: Literal["task", "explicit"] = "task"
    automation_id: str | None = None
    target_url: str | None = None
    interactive: bool = False
    intent_key: str | None = None
    completion_conditions: list[ConditionSpec] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list, max_length=8)


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"


class RunMode(StrEnum):
    ROUTING = "ROUTING"
    COLD = "COLD"
    COMPILING = "COMPILING"
    HOT = "HOT"
    HEALING = "HEALING"


class RunMetrics(Contract):
    execution_strategy: Literal["model", "native", "replay"] = "model"
    duration_ms: float = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_duration_ms: float = 0
    embedding_duration_ms: float = 0
    actions: int = 0
    healed_steps: int = 0
    estimated_cost_usd: float | None = None
    model: str | None = None
    simulated: bool = False
    token_usage_complete: bool = True
    baseline_run_id: str | None = None
    baseline_model: str | None = None
    baseline_tokens: int | None = None
    reference_tokens: int | None = None
    estimated_tokens_saved: int | None = None


class RunRecord(Contract):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    request: TaskRequest
    status: RunStatus = RunStatus.QUEUED
    mode: RunMode = RunMode.ROUTING
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    workflow_id: str | None = None
    workflow_version: int | None = None
    automation_id: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    pending_confirmation: dict[str, Any] | None = None


class RunEvent(Contract):
    run_id: str
    sequence: int
    kind: str
    timestamp: datetime = Field(default_factory=utc_now)
    data: dict[str, Any] = Field(default_factory=dict)
