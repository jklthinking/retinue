"""Request body schemas for the HTTP routers.

Kept in definition order from the original ``server/app.py``; the routers
import these rather than redeclaring them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoginBody(BaseModel):
    username: str
    password: str


class ActorBody(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    kind: str = Field(pattern=r"^(human|agent)$")
    display_name: str = ""
    role: str = Field(default="", max_length=128)
    goal: str = Field(default="", max_length=500)
    runtime: str = ""
    model: str = ""
    node: str = ""


class ActorUpdateBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: str | None = Field(default=None, max_length=128)
    goal: str | None = Field(default=None, max_length=500)
    runtime: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    node: str | None = Field(default=None, max_length=64)

class TaskCreateBody(BaseModel):
    title: str
    holder: str | None = None  # None + open_dispatch → publisher keeps the baton
    dept: str | None = None
    priority: str = "none"
    acceptance: list[str] = []
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    due_at: str | None = None  # calendar-day deadline, YYYY-MM-DD
    note: str = "task created"
    open_dispatch: bool = False
    squad_id: str | None = Field(default=None, max_length=64)
    pipeline: list[PipelineStageBody] | None = None
    # Channel credentials only: the channel-internal user identity the card is
    # opened for. Rejected for user/agent principals so provenance stays
    # channel-attested.
    source_user: str | None = Field(default=None, max_length=128)


class IntakeMessageBody(BaseModel):
    """One normalized inbound channel message (generic webhook adapter)."""

    sender_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    message_id: str = Field(min_length=1, max_length=128)
    chat_id: str | None = Field(default=None, max_length=128)
    received_at: str | None = Field(default=None, max_length=64)


class EnrollBody(BaseModel):
    """Executor self-registration handshake: node fingerprint + capability
    profile. Carries intent only; approval is a separate admin decision."""

    fingerprint: str = Field(min_length=8, max_length=128)
    requested_actor_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(default="", max_length=128)
    runtime: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=64)
    node_id: str = Field(default="", max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=50)


class EnrollDecisionBody(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str = Field(default="", max_length=500)
    actor_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )


class ChannelTokenBody(BaseModel):
    channel_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    label: str = Field(default="", max_length=128)


class ChannelUserBody(BaseModel):
    channel_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    channel_user_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(default="", max_length=128)


class TaskUpdateBody(BaseModel):
    status: str | None = None
    holder: str | None = None
    dept: str | None = Field(default=None, min_length=1, max_length=64)
    blocked_reason: str | None = None
    next_holder: str | None = None
    due_at: str | None = None  # set to YYYY-MM-DD, or "" to clear
    priority: str | None = None
    acceptance: list[str] | None = None
    refs: list[str] = []
    note: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    lease_term: int | None = Field(default=None, ge=1)
    evidence: dict[str, Any] | None = None


class TaskDependencyBody(BaseModel):
    prerequisite_id: str = Field(pattern=r"^task-[0-9]{8}-[0-9]{3}$")
    kind: str = Field(default="blocks", pattern=r"^blocks$")
    note: str = Field(default="dependency added", min_length=1, max_length=240)


class TaskDependencyRemoveBody(BaseModel):
    note: str = Field(default="dependency removed", min_length=1, max_length=240)


class ClaimBody(BaseModel):
    note: str = "接单"


class AttemptBody(BaseModel):
    outcome: str = Field(pattern=r"^(succeeded|failed|cancelled)$")
    started_at: dt.datetime
    ended_at: dt.datetime
    reason: str | None = Field(default=None, max_length=240)
    exit_status: int | None = Field(
        default=None, ge=-(2**31), le=2**31 - 1
    )
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    lease_term: int | None = Field(default=None, ge=1)
    trigger_source: str | None = Field(
        default=None,
        pattern=r"^(claim|retry|human|sweep|precheck|worker)$",
    )
    session_ref: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9._:-]{1,128}$"
    )
    checkpoint_ref: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9._:-]{1,128}$"
    )
    failure_class: str | None = Field(
        default=None,
        pattern=r"^(transient|semantic|precheck)$",
    )
    workdir_key: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=128
    )


class TaskHeartbeatBody(BaseModel):
    lease_term: int = Field(ge=1)
    started: bool = False
    workdir_key: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=128
    )


class PrecheckItemBody(BaseModel):
    item: str = Field(min_length=1, max_length=240)
    passed: bool
    feedback: str = Field(default="", max_length=240)


class PrecheckBody(BaseModel):
    lease_term: int = Field(ge=1)
    checks: list[PrecheckItemBody] = Field(min_length=1, max_length=32)


class EscalateBody(BaseModel):
    note: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=240)
    lease_term: int | None = Field(default=None, ge=1)


class RetryBody(BaseModel):
    note: str = Field(min_length=1, max_length=240)
    workdir_key: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=128
    )


class NodeAttemptBody(AttemptBody):
    duty: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)


class ReviewCommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=1200)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    artifact_ref: str | None = Field(default=None, max_length=1024)


class ReviewReplyBody(BaseModel):
    body: str = Field(min_length=1, max_length=1200)
    decision: str = Field(pattern=r"^(accepted|needs_info|declined)$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    evidence_refs: list[str] = []


class PipelineStageBody(BaseModel):
    name: str
    holder: str
    gate: str = Field(default="auto", pattern=r"^(auto|review|queen)$")


class StageDoneBody(BaseModel):
    note: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: dict[str, Any] | None = None


class StageRejectBody(BaseModel):
    note: str


class DecideBody(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str = ""


class DispatchBody(BaseModel):
    intent: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    priority: str = "none"
    acceptance: list[str] = []


class SquadBody(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(default="", max_length=128)
    leader_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    members: list[str] = Field(default_factory=list, max_length=32)


class SquadMemberBody(BaseModel):
    actor_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TaskSquadBody(BaseModel):
    squad_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    note: str = Field(default="addressed to squad", min_length=1, max_length=240)


class SquadRouteBody(BaseModel):
    member_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    note: str = Field(default="squad leader route", min_length=1, max_length=240)


class DispatchScheduleBody(BaseModel):
    schedule_key: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    fire_at: str = Field(min_length=1, max_length=64)
    holder: str | None = None
    open_dispatch: bool = True
    squad_id: str | None = Field(default=None, max_length=64)
    dept: str | None = None
    priority: str = "none"
    acceptance: list[str] = []
    note: str = ""
    repeat_seconds: int | None = Field(default=None, ge=60)


class DispatchEventBody(BaseModel):
    source: str = Field(pattern=r"^(alert|callback)$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    title: str = Field(min_length=1, max_length=256)
    holder: str | None = None
    open_dispatch: bool = True
    squad_id: str | None = Field(default=None, max_length=64)
    dept: str | None = None
    priority: str = "none"
    acceptance: list[str] = []
    note: str = ""


class PipelineTemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    stages: list[PipelineStageBody]
    match_terms: list[str] = []
    acceptance: list[str] = []


class CardPipelineNodeBody(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=256)
    holder: str | None = None
    open_dispatch: bool = False
    squad_id: str | None = Field(default=None, max_length=64)
    dept: str | None = None
    priority: str = "none"
    acceptance: list[Any] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class CardPipelineTemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    nodes: list[CardPipelineNodeBody] = Field(min_length=1)


class CardPipelineInstantiateBody(BaseModel):
    instance_key: str | None = Field(default=None, max_length=128)


class MetricsBody(BaseModel):
    actor_id: str
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    runtime: str = ""
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class SessionMessageBody(BaseModel):
    role: str = Field(pattern=r"^(user|assistant|system)$")
    text: str = Field(min_length=1, max_length=4000)
    at: dt.datetime | None = None


class SessionSyncBody(BaseModel):
    actor_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    runtime: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    external_id: str = Field(min_length=1, max_length=256)
    title: str = Field(default="", max_length=256)
    summary: str = Field(default="", max_length=2000)
    privacy: str = Field(default="metadata", pattern=r"^(metadata|summary|full)$")
    cursor: int = Field(ge=0)
    message_count: int = Field(default=0, ge=0)
    messages: list[SessionMessageBody] = Field(default_factory=list, max_length=80)
    started_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    task_id: str | None = Field(default=None, pattern=r"^task-\d{8}-\d{3,}$")
    resume_capable: bool = False


class SessionCaptureBody(BaseModel):
    title: str = Field(default="", max_length=256)


class SessionCaptureExportBody(BaseModel):
    target_path: str = Field(default="", max_length=512)


class SessionTaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    dept: str = Field(min_length=1, max_length=64)
    holder: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    priority: str = "none"
    acceptance: list[str] = Field(default_factory=list, max_length=12)


class UserBody(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8)
    role: str = Field(pattern=r"^(admin|member|viewer)$")
    display_name: str = ""
    actor_id: str | None = None


class TokenBody(BaseModel):
    actor_id: str
    label: str = ""
    # None issues a non-expiring credential, which stays the default so that
    # existing operator habits keep working; a bounded lifetime is opt-in.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class TokenRotateBody(BaseModel):
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class OnboardingBody(BaseModel):
    actor_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=128)
    role: str = Field(default="", max_length=128)
    goal: str = Field(default="", max_length=500)
    runtime: str = ""
    model: str = ""
    node: str = ""
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8)
    label: str = ""


class NodeTokenBody(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    label: str = ""


class NodeAdmissionBody(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    label: str = ""


class SkillBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    category: str = ""
    enabled: bool = True
    owners: list[str] = []


class SkillBindBody(BaseModel):
    skill_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool = True


class SkillBindingUpdateBody(BaseModel):
    enabled: bool


class SkillImportBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    category: str = ""
    source: str = Field(default="local", pattern=r"^(local|kingdom)$")
    source_kind: str = Field(
        default="runtime",
        pattern=r"^(local|workspace|repo|runtime|external)$",
    )
    snapshot: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    owners: list[str] = []


class SkillSyncItem(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    category: str = ""
    snapshot: dict[str, str] = Field(default_factory=dict)


class SkillSyncBody(BaseModel):
    """Node/runtime skill inventory push; actor is the authenticated principal."""

    node_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    skills: list[SkillSyncItem] = Field(default_factory=list, max_length=200)


class HeartbeatBody(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    label: str = ""
    hostname: str = ""
    platform: str = ""
    uptime_seconds: int = 0
    load: list[float] = []
    disk: dict[str, Any] = {}
    memory: dict[str, Any] = {}
    services: list[dict[str, Any]] = []


class NodeRuntimeItemBody(BaseModel):
    runtime: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    command: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    available: bool = True
    # How the probe found the executable, never where. Validated as a slug
    # rather than a closed set so a newer probe reporting a source this build
    # has not heard of is recorded rather than rejected; nodes run mixed
    # versions. Omitted by older probes, which only ever searched PATH.
    source: str = Field(default="path", pattern=r"^[a-z][a-z0-9-]{0,31}$")


class DataDirItemBody(BaseModel):
    runtime: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    # The same tilde-relative form the local scan prints ("~/.codex/sessions"):
    # it names a runtime's conventional data directory without naming a user
    # or a machine. The pattern rejects absolute paths at the schema edge.
    path_hint: str = Field(pattern=r"^~(/[A-Za-z0-9._-]+)+$", max_length=256)
    last_changed_at: dt.datetime | None = None


class RuntimeProbeBody(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    runtimes: list[NodeRuntimeItemBody] = Field(default_factory=list, max_length=64)
    # None (field absent) means an older probe that only reports executables;
    # its rows must read as "local history unknown", never as "no local
    # history". An empty list means this probe checked and found nothing.
    data_dirs: list[DataDirItemBody] | None = Field(default=None, max_length=64)


class LiveTmuxRefBody(BaseModel):
    session_id: str = Field(pattern=r"^\$[0-9]+$")
    window_id: str = Field(pattern=r"^@[0-9]+$")
    pane_id: str = Field(pattern=r"^%[0-9]+$")
    session_name: str = Field(
        default="", max_length=80, pattern=r"^[^/\\\x00-\x1f\x7f]*$"
    )
    window_name: str = Field(
        default="", max_length=80, pattern=r"^[^/\\\x00-\x1f\x7f]*$"
    )


class LiveSessionPaneBody(BaseModel):
    endpoint_id: str = Field(pattern=r"^tmux-[a-f0-9]{24}$")
    generation: str = Field(pattern=r"^[a-f0-9]{32}$")
    backend: str = Field(pattern=r"^tmux$")
    runtime: str = Field(default="", pattern=r"^(?:|[a-z0-9]+(?:-[a-z0-9]+)*)$")
    input_mode: str = Field(default="", pattern=r"^(?:|codex-prompt)$")
    actor_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    live_session_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
    )
    task_id: str | None = Field(default=None, pattern=r"^task-[0-9]{8}-[0-9]{3,}$")
    explicit_binding: bool = False
    binding_source: str = Field(pattern=r"^(explicit|process|heuristic)$")
    binding_confidence: int = Field(ge=0, le=100)
    occupant_verified: bool = False
    state: str = Field(pattern=r"^(unknown|idle|busy|waiting|blocked|disconnected)$")
    state_source: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    state_confidence: int = Field(ge=0, le=100)
    command: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._-]*$")
    cwd_hint: str = Field(default="", max_length=128, pattern=r"^[^/\\\x00-\x1f\x7f]*$")
    display_location: str = Field(
        default="", max_length=256, pattern=r"^[^/\\\x00-\x1f\x7f]*$"
    )
    tmux: LiveTmuxRefBody
    # Diagnostic claim from the probe only.  The Hub always recomputes the
    # authoritative value from actor/task/generation/occupant checks.
    control_eligible: bool = False


class LiveSessionProbeBody(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    backend: str = Field(pattern=r"^tmux$")
    server_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    available: bool
    status: str = Field(pattern=r"^(ok|no-server|unavailable)$")
    panes: list[LiveSessionPaneBody] = Field(default_factory=list, max_length=256)
    ignored_rows: int = Field(default=0, ge=0, le=10000)


class ControlCreateBody(BaseModel):
    verb: str = Field(pattern=r"^(tell|peek|interrupt)$")
    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$",
    )
    lines: int | None = Field(default=None, ge=1, le=100)
    idempotency_key: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$"
    )

    @model_validator(mode="after")
    def validate_verb_payload(self):
        if self.verb == "tell" and (self.message is None or self.lines is not None):
            raise ValueError("tell requires message and forbids lines")
        if self.verb == "peek" and self.message is not None:
            raise ValueError("peek forbids message")
        if self.verb == "peek" and self.lines is None:
            self.lines = 20
        if self.verb == "interrupt" and (
            self.message is not None or self.lines is not None
        ):
            raise ValueError("interrupt forbids message and lines")
        return self


class ControlPullBody(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    limit: int = Field(default=8, ge=1, le=32)


class ControlAckBody(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    envelope_id: str = Field(pattern=r"^ctl-[a-f0-9]{24}$")
    generation: str = Field(pattern=r"^[a-f0-9]{32}$")
    outcome: str = Field(pattern=r"^(delivered|failed)$")
    result: str = Field(
        default="",
        max_length=8000,
        pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$",
    )
    detail: str = Field(
        default="", max_length=240, pattern=r"^[^\x00-\x1f\x7f]*$"
    )

class KnowledgeBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = "corpus"
    location: str = ""
    docs: int = 0
    size_bytes: int = 0
    notes: str = ""


class TodoGrantBody(BaseModel):
    actor_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TodoProposalChildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    notes: str = Field(default="", max_length=4000)
    due_at: str | None = None
    event_on: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100, strict=True)


class TodoProposalBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    notes: str = Field(default="", max_length=4000)
    owner_username: str | None = Field(default=None, min_length=2, max_length=64)
    due_at: str | None = None
    event_on: str | None = None
    parent_id: str | None = Field(default=None, max_length=40)
    children: list[TodoProposalChildBody] = Field(default_factory=list)
    remind_at: str | None = None
    source_session_id: int | None = Field(default=None, ge=1)
    source_message_id: str | None = Field(default=None, max_length=128)
    source_channel: str | None = Field(default=None, max_length=64)
    source_backlink: str | None = Field(default=None, max_length=512)
    dedup_key: str | None = Field(default=None, max_length=128)


class TodoRejectBody(BaseModel):
    note: str = Field(default="", max_length=240)


class TodoCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    notes: str = Field(default="", max_length=4000)
    due_at: str | None = None
    event_on: str | None = None
    parent_id: str | None = Field(default=None, max_length=40)
    progress: int | None = Field(default=None, ge=0, le=100, strict=True)
    remind_at: str | None = None
    source_session_id: int | None = Field(default=None, ge=1)
    source_message_id: str | None = Field(default=None, max_length=128)
    source_channel: str | None = Field(default=None, max_length=64)
    source_backlink: str | None = Field(default=None, max_length=512)


class TodoUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=4000)
    due_at: str | None = None
    event_on: str | None = None
    parent_id: str | None = Field(default=None, max_length=40)
    progress: int | None = Field(default=None, ge=0, le=100, strict=True)


class TodoProgressBody(BaseModel):
    percent: int = Field(ge=0, le=100, strict=True)
    note: str = Field(min_length=1, max_length=240)


class TodoSnoozeBody(BaseModel):
    due_at: str = Field(min_length=1, max_length=32)
    remind_at: str | None = None


class TodoReminderBody(BaseModel):
    scheduled_for: str = Field(min_length=1, max_length=64)
    channel: str = Field(default="pending", max_length=32)

class DistillCandidateBody(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    source_session_id: int | None = Field(default=None, ge=1)
    origin_ref: str | None = Field(default=None, max_length=512)
    cooldown_hours: int | None = Field(default=None, ge=0, le=24 * 365)


class DistillRejectBody(BaseModel):
    decision_note: str = Field(min_length=1, max_length=240)


# Quota telemetry is a deliberately closed schema: no vendor payloads or
# credential fields can survive validation, including inside windows/balance.
class QuotaValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value):
        import re
        def inspect(item):
            if isinstance(item, dict):
                for child in item.values():
                    inspect(child)
            elif isinstance(item, list):
                for child in item:
                    inspect(child)
            elif isinstance(item, str) and re.search(
                r"(?i)bearer\s|sk-[a-z0-9_-]{16,}|(?:rtn|rts|rtd)_[a-z0-9_-]{30,}|"
                r"ghp_[a-z0-9]{20,}|AKIA[0-9A-Z]{16}|xai-[a-z0-9_-]{16,}|eyJ[a-z0-9_-]+\.eyJ|-----BEGIN|"
                r"(?:token|cookie|password|secret|authorization)\s*[:=]", item
            ):
                raise ValueError("credential-like quota content is forbidden")
        inspect(value)
        return value


class QuotaWindow(QuotaValue):
    key: str = Field(min_length=1, max_length=128)
    label: str = Field(max_length=128)
    period: str = Field(max_length=32)
    used_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    used: float | None = Field(default=None, allow_inf_nan=False)
    limit: float | None = Field(default=None, allow_inf_nan=False)
    unit: str = Field(max_length=32)
    resets_at: str | None = Field(default=None, max_length=64)
    raw_reset: str | float | None = None

    @model_validator(mode="after")
    def validate_reset(self):
        if isinstance(self.raw_reset, str) and len(self.raw_reset) > 128:
            raise ValueError("raw_reset too long")
        if isinstance(self.raw_reset, float):
            import math
            if not math.isfinite(self.raw_reset):
                raise ValueError("invalid raw_reset")
        if self.resets_at is not None:
            quota_timestamp(self.resets_at)
        return self


def quota_timestamp(value: str) -> dt.datetime:
    if "T" not in value:
        raise ValueError("ISO timestamp required")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return stamp.astimezone(dt.timezone.utc)


class QuotaBalance(QuotaValue):
    amount: float = Field(allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class QuotaProvider(QuotaValue):
    provider: str = Field(max_length=32)
    kind: str = Field(pattern=r"^(subscription|api)$")
    status: str = Field(pattern=r"^(ok|error|expired|not_configured|consent_missing)$")
    plan: str | None = Field(default=None, max_length=64)
    account_fp: str | None = Field(default=None, pattern=r"^(?:[a-f0-9]{12,64})?$")
    source: str = Field(pattern=r"^(api|cli)$")
    windows: list[QuotaWindow] = Field(default_factory=list, max_length=50)
    balance: QuotaBalance | None = None
    error: str | None = Field(default=None, max_length=256)
    fetched_at: str = Field(max_length=64)

    @model_validator(mode="after")
    def validate_provider(self):
        from node.quota_probe import PROVIDERS
        if self.provider not in PROVIDERS:
            raise ValueError("unknown provider")
        quota_timestamp(self.fetched_at)
        return self


class QuotaReportBody(QuotaValue):
    node: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    collected_at: str = Field(max_length=64)
    providers: list[QuotaProvider] = Field(max_length=10)
    refresh_request_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def validate_report(self):
        quota_timestamp(self.collected_at)
        names = [item.provider for item in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("providers must be unique")
        return self


class QuotaRefreshBody(QuotaValue):
    providers: list[str] | None = Field(default=None, min_length=1, max_length=10)
    nodes: list[str] | None = Field(default=None, min_length=1, max_length=64)
    request_key: str | None = Field(default=None, pattern=r"^[a-f0-9-]{16,64}$")

    @model_validator(mode="after")
    def validate_scope(self):
        import re
        from node.quota_probe import PROVIDERS
        if self.providers is not None and (len(set(self.providers)) != len(self.providers) or any(p not in PROVIDERS for p in self.providers)):
            raise ValueError("invalid providers")
        if self.nodes is not None and (len(set(self.nodes)) != len(self.nodes) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", p) or len(p) > 64 for p in self.nodes)):
            raise ValueError("invalid nodes")
        return self


class QuotaRefreshClaimBody(QuotaValue):
    node: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
