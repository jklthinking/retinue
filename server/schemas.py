"""Request body schemas for the HTTP routers.

Kept in definition order from the original ``server/app.py``; the routers
import these rather than redeclaring them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


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
    source: str = Field(default="local", pattern=r"^(local|internal)$")
    source_kind: str = Field(
        default="runtime",
        pattern=r"^(local|workspace|repo|runtime|external)$",
    )
    snapshot: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    owners: list[str] = []


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

class KnowledgeBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = "corpus"
    location: str = ""
    docs: int = 0
    size_bytes: int = 0
    notes: str = ""


class TodoGrantBody(BaseModel):
    actor_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TodoProposalBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    notes: str = Field(default="", max_length=4000)
    owner_username: str | None = Field(default=None, min_length=2, max_length=64)
    due_at: str | None = None
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
    remind_at: str | None = None
    source_session_id: int | None = Field(default=None, ge=1)
    source_message_id: str | None = Field(default=None, max_length=128)
    source_channel: str | None = Field(default=None, max_length=64)
    source_backlink: str | None = Field(default=None, max_length=512)


class TodoUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=4000)
    due_at: str | None = None


class TodoSnoozeBody(BaseModel):
    due_at: str = Field(min_length=1, max_length=32)
    remind_at: str | None = None


class TodoReminderBody(BaseModel):
    scheduled_for: str = Field(min_length=1, max_length=64)
    channel: str = Field(default="pending", max_length=32)
