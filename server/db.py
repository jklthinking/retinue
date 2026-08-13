"""SQLAlchemy models and engine factory.

Storage is SQLite by default; every type used here is portable to
PostgreSQL so the evolution path documented in the README stays real.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    false,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateColumn, CreateIndex
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    """The single applied-version record for the server schema."""

    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class SeqCounter(Base):
    """One named sequence counter, allocated atomically by the database.

    Allocation is a single ``INSERT ... ON CONFLICT DO UPDATE`` inside the
    caller's own transaction, so concurrent processes never observe the same
    value and a rollback withdraws the allocation without leaving a gap.
    """

    __tablename__ = "seq_counters"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


class User(Base):
    """A human account that signs in through the web panel."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member | viewer
    display_name: Mapped[str] = mapped_column(String(128), default="")
    actor_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("actors.id"), nullable=True
    )
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Actor slugs this user has granted ``todo:propose`` (schema v18).
    todo_propose_grants_json: Mapped[str] = mapped_column(Text, default="[]")


class WebSession(Base):
    """A browser session; the cookie stores the raw token, we store its hash."""

    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class ApiToken(Base):
    """Bearer token for an agent (or automation) bound to one actor."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    label: Mapped[str] = mapped_column(String(128), default="")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NULL means the credential does not expire; existing tokens keep working
    # until an administrator revokes or rotates them.
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    actor: Mapped["Actor"] = relationship()


class Actor(Base):
    """A participant that can hold task cards: a human or an agent."""

    __tablename__ = "actors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # protocol slug
    kind: Mapped[str] = mapped_column(String(16), default="agent")  # human | agent
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(128), default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    runtime: Mapped[str] = mapped_column(String(64), default="")  # claude-code, codex, ...
    model: Mapped[str] = mapped_column(String(64), default="")
    node: Mapped[str] = mapped_column(String(64), default="")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Task(Base):
    """One task card. The event chain lives in TaskEvent, append-only."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_lease_expires_at", "lease_expires_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # task-YYYYMMDD-NNN
    title: Mapped[str] = mapped_column(String(256))
    created_by: Mapped[str] = mapped_column(String(64))
    dept: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="none")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    holder: Mapped[str] = mapped_column(String(64), index=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acceptance_json: Mapped[str] = mapped_column(Text, default="[]")
    refs_json: Mapped[str] = mapped_column(Text, default="[]")
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    # Calendar-day deadline (YYYY-MM-DD), outside the folded chain state: a
    # scheduling hint like ``archived``, not a protocol state field.
    due_at: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    open_dispatch: Mapped[bool] = mapped_column(Boolean, default=False)  # 挂单待接
    pipeline_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 流程节点
    pipeline_stage: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    # Orchestration lease (schema v13). Term is Raft-style fencing: it only
    # increases, and a writer bearing a smaller term is refused. Expiry is
    # heartbeat liveness, not a wall-clock cap on long-running work.
    lease_term: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_heartbeat_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_claimed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workdir_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hall_opened_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unclaimed_escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional formation the hall card is addressed to (schema v15).
    squad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Intake provenance (schema v16): which entry channel opened the card and
    # which user identity inside that channel spoke. Provenance metadata like
    # ``due_at``, outside the folded chain state.
    source_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_user: Mapped[str | None] = mapped_column(String(128), nullable=True)

    events: Mapped[list["TaskEvent"]] = relationship(
        order_by="TaskEvent.seq", cascade="all, delete-orphan", lazy="selectin"
    )
    attempts: Mapped[list["TaskAttempt"]] = relationship(
        order_by="TaskAttempt.seq", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def acceptance(self) -> list[str]:
        return json.loads(self.acceptance_json)

    @property
    def refs(self) -> list[str]:
        return json.loads(self.refs_json)


class TaskDependency(Base):
    """One finish-to-start edge: dependent waits for prerequisite to be done."""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        Index(
            "ux_task_dependencies_edge",
            "dependent_id",
            "prerequisite_id",
            "kind",
            unique=True,
        ),
        Index("ix_task_dependencies_dependent", "dependent_id"),
        Index("ix_task_dependencies_prerequisite", "prerequisite_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dependent_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    prerequisite_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="blocks")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class TaskEvent(Base):
    """One append-only chain event; mirrors the YAML protocol chain entry."""

    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    who: Mapped[str] = mapped_column(String(64))
    did: Mapped[str] = mapped_column(Text)
    at: Mapped[str] = mapped_column(String(40))  # ISO 8601, protocol-compatible
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    from_holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), default="task")
    event_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class TaskAttempt(Base):
    """One immutable execution outcome related to a task, ordered by ``seq``.

    Attempts deliberately do not live in ``task_events``: adding one cannot
    change the card, consume a chain sequence, or grant task-write authority.
    """

    __tablename__ = "task_attempts"
    __table_args__ = (
        Index("ux_task_attempts_task_seq", "task_id", "seq", unique=True),
        Index("ux_task_attempts_attempt_key", "attempt_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    attempt_key: Mapped[str] = mapped_column(String(64))
    reporter_kind: Mapped[str] = mapped_column(String(16))  # actor | operator | node
    reporter_id: Mapped[str] = mapped_column(String(64))
    duty: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))  # succeeded | failed | cancelled
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    exit_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[str] = mapped_column(String(40))
    ended_at: Mapped[str] = mapped_column(String(40))
    reported_at: Mapped[str] = mapped_column(String(40))
    lease_term: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workdir_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class WorkdirLock(Base):
    """Exclusive lock so two runs cannot share one work directory."""

    __tablename__ = "workdir_locks"

    workdir_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    holder: Mapped[str] = mapped_column(String(64))
    lease_term: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Squad(Base):
    """A named formation with one leader who may route hall cards to members."""

    __tablename__ = "squads"
    __table_args__ = (Index("ix_squads_leader_id", "leader_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    leader_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class SquadMember(Base):
    """One actor belonging to a formation."""

    __tablename__ = "squad_members"
    __table_args__ = (
        Index("ux_squad_members_pair", "squad_id", "actor_id", unique=True),
        Index("ix_squad_members_actor_id", "actor_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    squad_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class DispatchSchedule(Base):
    """A calendar fire that opens or assigns a card when due."""

    __tablename__ = "dispatch_schedules"
    __table_args__ = (
        Index("ux_dispatch_schedules_key", "schedule_key", unique=True),
        Index("ix_dispatch_schedules_fire_at", "fire_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    fire_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    open_dispatch: Mapped[bool] = mapped_column(Boolean, default=True)
    squad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dept: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="none")
    acceptance_json: Mapped[str] = mapped_column(Text, default="[]")
    note: Mapped[str] = mapped_column(String(240), default="")
    repeat_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_fired_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class DispatchTrigger(Base):
    """Idempotency row for one inbound alert, callback, or schedule fire."""

    __tablename__ = "dispatch_triggers"
    __table_args__ = (
        Index(
            "ux_dispatch_triggers_source_key",
            "source",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class DispatchRequest(Base):
    """One idempotent IM/source event mapped to exactly one task."""

    __tablename__ = "dispatch_requests"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("pipeline_templates.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TokenUsage(Base):
    """Daily de-identified token totals per actor. Usage numbers only —
    session bodies, prompts, and keys are never ingested."""

    __tablename__ = "token_usage"
    __table_args__ = (UniqueConstraint("actor_id", "date", "runtime"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD local
    runtime: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RuntimeSession(Base):
    """A privacy-scoped snapshot of one runtime-owned conversation.

    The runtime remains authoritative. Retinue stores only the level explicitly
    pushed by the operator and never writes back into the runtime transcript.
    """

    __tablename__ = "runtime_sessions"
    __table_args__ = (UniqueConstraint("actor_id", "runtime", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"), index=True)
    runtime: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(256))
    node: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    privacy: Mapped[str] = mapped_column(String(16), default="metadata", index=True)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    task_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("tasks.id"), nullable=True, index=True
    )
    resume_capable: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    synced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SessionCapture(Base):
    """A queued, privacy-preserving export of one runtime session to a local vault."""

    __tablename__ = "session_captures"
    __table_args__ = (UniqueConstraint("session_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("runtime_sessions.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="obsidian")
    title: Mapped[str] = mapped_column(String(256), default="")
    markdown: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    target_path: Mapped[str] = mapped_column(String(512), default="")
    requested_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    exported_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Skill(Base):
    """A capability in the registry; owners are actor slugs."""

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    owners_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(32), default="local")  # local | internal
    # local | workspace | repo | runtime | external — Multica two-level plus import
    source_kind: Mapped[str] = mapped_column(String(32), default="local")
    source_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    imported_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    imported_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SkillBinding(Base):
    """One actor-skill assignment; enablement is independent of the catalog row."""

    __tablename__ = "skill_bindings"
    __table_args__ = (
        Index("ux_skill_bindings_actor_skill", "actor_id", "skill_id", unique=True),
        Index("ix_skill_bindings_skill", "skill_id"),
        Index("ix_skill_bindings_actor", "actor_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("actors.id"), nullable=False
    )
    skill_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skills.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SkillBindingEvent(Base):
    """Append-only binding chain, one actor's history, never rewritten."""

    __tablename__ = "skill_binding_events"
    __table_args__ = (
        Index("ux_skill_binding_events_actor_seq", "actor_id", "seq", unique=True),
        Index("ix_skill_binding_events_skill", "skill_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_id: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_name: Mapped[str] = mapped_column(String(128), default="")
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    who: Mapped[str] = mapped_column(String(64), nullable=False)
    did: Mapped[str] = mapped_column(String(240), nullable=False)
    from_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    to_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Node(Base):
    """An explicitly admitted machine; telemetry only refreshes its facts."""

    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    hostname: Mapped[str] = mapped_column(String(128), default="")
    platform: Mapped[str] = mapped_column(String(256), default="")
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0)
    load_json: Mapped[str] = mapped_column(Text, default="[]")
    disk_json: Mapped[str] = mapped_column(Text, default="{}")
    memory_json: Mapped[str] = mapped_column(Text, default="{}")
    services_json: Mapped[str] = mapped_column(Text, default="[]")
    membership_status: Mapped[str] = mapped_column(
        String(16), default="admitted"
    )  # admitted | retired
    admitted_by: Mapped[str] = mapped_column(String(64), default="local-admin")
    admitted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=True
    )
    retired_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retired_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when a runtime inventory report arrives, even an empty one: NULL
    # means "never probed", which the fleet view must show differently from
    # "probed and found nothing".
    runtimes_probed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set only by probes new enough to check runtime data directories; NULL
    # means this node's probes cannot tell us about local history, which must
    # not be misread as "no local history".
    data_dirs_probed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class NodeRuntime(Base):
    """One CLI runtime reported by a node-scoped, metadata-only probe."""

    __tablename__ = "node_runtimes"
    __table_args__ = (UniqueConstraint("node_id", "runtime"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    runtime: Mapped[str] = mapped_column(String(64), index=True)
    command: Mapped[str] = mapped_column(String(128), default="")
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="path")
    # Tilde-relative hint ("~/.codex/sessions"), the same form the local scan
    # prints; it names a runtime's conventional data directory without naming
    # a user or a machine. NULL when the probe found none or cannot tell —
    # the node's data_dirs_probed_at distinguishes those. Never absolute.
    path_hint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    data_changed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

class NodeToken(Base):
    """A narrow infrastructure credential bound to exactly one node."""

    __tablename__ = "node_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Approval(Base):
    """A queen-gate decision request on one pipeline stage of a task."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    stage_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    token_hash: Mapped[str] = mapped_column(String(128), index=True)  # approve link
    reject_token_hash: Mapped[str] = mapped_column(
        String(128), default="", index=True
    )  # reject link
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChannelToken(Base):
    """A narrow intake credential bound to exactly one entry channel.

    A channel token can only open cards and read the cards its own channel
    opened; it is never an executor identity and cannot claim or write cards.
    """

    __tablename__ = "channel_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChannelUser(Base):
    """Maps a user identity inside an entry channel to a board actor."""

    __tablename__ = "channel_users"
    __table_args__ = (
        Index("ux_channel_users_pair", "channel_id", "channel_user_id", unique=True),
        Index("ix_channel_users_actor_id", "actor_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EnrollApplication(Base):
    """An executor self-registration handshake awaiting an admin decision.

    Until approved the applicant holds no credential and cannot write the
    board; approval creates the actor and issues the executor token once.
    """

    __tablename__ = "enroll_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    requested_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    runtime: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    node_id: Mapped[str] = mapped_column(String(64), default="")
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PipelineTemplate(Base):
    """A reusable flow plus deterministic natural-language match terms."""

    __tablename__ = "pipeline_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    stages_json: Mapped[str] = mapped_column(Text, default="[]")
    match_terms_json: Mapped[str] = mapped_column(Text, default="[]")
    acceptance_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def match_terms(self) -> list[str]:
        return json.loads(self.match_terms_json)

    @property
    def acceptance(self) -> list[str]:
        return json.loads(self.acceptance_json)


class CardPipelineTemplate(Base):
    """Reusable multi-card chain: nodes plus finish-to-start edges."""

    __tablename__ = "card_pipeline_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    spec_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CardPipelineInstance(Base):
    """One instantiation of a card-pipeline template, with a resume cursor."""

    __tablename__ = "card_pipeline_instances"
    __table_args__ = (
        Index(
            "ux_card_pipeline_instances_key",
            "created_by",
            "instance_key",
            unique=True,
        ),
        Index("ix_card_pipeline_instances_template", "template_id"),
        Index("ix_card_pipeline_instances_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("card_pipeline_templates.id"), nullable=False
    )
    instance_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="instantiating")
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class KnowledgeSource(Base):
    """A knowledge asset: vault, wiki, corpus, archive mirror, dataset."""

    __tablename__ = "knowledge_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="corpus")
    location: Mapped[str] = mapped_column(String(256), default="")
    docs: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TodoProposal(Base):
    """An agent- or owner-submitted candidate; not yet a private TodoItem."""

    __tablename__ = "todo_proposals"
    __table_args__ = (
        Index(
            "ux_todo_proposals_owner_dedup",
            "owner_user_id",
            "dedup_key",
            unique=True,
        ),
        Index("ix_todo_proposals_owner_status", "owner_user_id", "status"),
        Index("ix_todo_proposals_proposed_by", "proposed_by"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    remind_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_backlink: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    todo_item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TodoItem(Base):
    """A confirmed private todo. Only the owner reads it by default."""

    __tablename__ = "todo_items"
    __table_args__ = (
        Index("ix_todo_items_owner_status", "owner_user_id", "status"),
        Index("ix_todo_items_owner_due", "owner_user_id", "due_at"),
        Index("ix_todo_items_remind_at", "remind_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")
    due_at: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    remind_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    proposal_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_backlink: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TodoEvent(Base):
    """Append-only audit for a proposal or a confirmed todo."""

    __tablename__ = "todo_events"
    __table_args__ = (
        Index("ux_todo_events_item_seq", "todo_item_id", "seq", unique=True),
        Index("ux_todo_events_proposal_seq", "proposal_id", "seq", unique=True),
        Index("ix_todo_events_item", "todo_item_id"),
        Index("ix_todo_events_proposal", "proposal_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    todo_item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    who: Mapped[str] = mapped_column(String(64), nullable=False)
    who_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    did: Mapped[str] = mapped_column(String(240), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReminderDelivery(Base):
    """Idempotent reminder slot. Delivery channels are a later batch."""

    __tablename__ = "reminder_deliveries"
    __table_args__ = (
        Index("ux_reminder_deliveries_key", "delivery_key", unique=True),
        Index(
            "ux_reminder_deliveries_slot",
            "todo_item_id",
            "scheduled_for",
            "channel",
            unique=True,
        ),
        Index("ix_reminder_deliveries_due", "status", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    todo_item_id: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_for: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), default="pending")
    delivery_key: Mapped[str] = mapped_column(String(192), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    delivered_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TodoTaskLink(Base):
    """Bidirectional backlink from a private todo to a shared board card."""

    __tablename__ = "todo_task_links"
    __table_args__ = (
        Index("ux_todo_task_links_todo", "todo_item_id", unique=True),
        Index("ux_todo_task_links_task", "task_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    todo_item_id: Mapped[str] = mapped_column(String(40), nullable=False)
    task_id: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


def make_session_factory(db_path: Path | str) -> sessionmaker:
    """Open a current database, or initialize a database with no tables.

    Existing databases are never migrated here.  A stale schema is an
    operator decision point and must go through :func:`migrate_database`.
    """
    engine = _make_engine(db_path)
    with engine.connect() as conn:
        is_fresh = not inspect(conn).get_table_names()

    if is_fresh:
        _initialize_database(engine)
    else:
        with engine.connect() as conn:
            current = _current_schema_version(conn)
        _require_current_schema(current)

    return sessionmaker(bind=engine, expire_on_commit=False)


MIGRATION_COMMAND = "python -m server.main --data-dir DATA_DIR migrate"


@dataclass(frozen=True)
class MigrationResult:
    """Versions observed before and after an explicit migration command."""

    from_version: int
    to_version: int


def migrate_database(db_path: Path | str) -> MigrationResult:
    """Explicitly initialize or migrate one database, returning its versions."""
    engine = _make_engine(db_path)
    with engine.connect() as conn:
        is_fresh = not inspect(conn).get_table_names()

    if is_fresh:
        _initialize_database(engine)
        return MigrationResult(0, LATEST_SCHEMA_VERSION)

    # Refuse a newer database before create_all gets any opportunity to write.
    with engine.connect() as conn:
        current = _current_schema_version(conn)
    if current > LATEST_SCHEMA_VERSION:
        _require_current_schema(current)

    # Keep the established evolution mechanism intact: create missing additive
    # tables, baseline an unversioned legacy database, then apply migrations in
    # order.  The only change is that this now runs solely on explicit request.
    Base.metadata.create_all(engine)
    from_version, to_version = _migrate(engine)
    return MigrationResult(from_version, to_version)


def _make_engine(db_path: Path | str):
    """Create the engine and install the SQLite connection policy."""
    url = f"sqlite:///{Path(db_path)}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver glue
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        # WAL keeps readers unblocked but still serializes writers. The SQLite
        # default busy_timeout of 0 fails the second concurrent write straight
        # away with SQLITE_BUSY; wait for the lock instead of surfacing a 500.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _initialize_database(engine) -> None:
    """Create a genuinely empty database directly at the latest schema."""
    Base.metadata.create_all(engine)
    # The versioned mechanism also owns indexes that are not representable as
    # one dialect-neutral metadata declaration.  Running it against the empty
    # schema completes those declarations and stamps the result; there is no
    # pre-existing state for an operator to upgrade.
    _migrate(engine)


def _current_schema_version(conn: Connection) -> int:
    """Read the stored version, or infer the unchanged legacy baseline."""
    inspector = inspect(conn)
    if inspector.has_table(SchemaVersion.__tablename__):
        current = conn.execute(
            select(SchemaVersion.version).where(SchemaVersion.id == 1)
        ).scalar_one_or_none()
        if current is not None:
            return current
    return _baseline_version(conn)


def _require_current_schema(current: int) -> None:
    if current < LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current} is older than required version "
            f"{LATEST_SCHEMA_VERSION}; stop all writers and run "
            f"`{MIGRATION_COMMAND}`"
        )
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current} is newer than supported "
            f"version {LATEST_SCHEMA_VERSION}"
        )


def _migrate(engine) -> tuple[int, int]:
    """Apply only pending migrations, baselining databases from older builds."""
    with engine.begin() as conn:
        stored = conn.execute(
            select(SchemaVersion.version).where(SchemaVersion.id == 1)
        ).scalar_one_or_none()
        current = stored if stored is not None else _baseline_version(conn)
        from_version = current
        if stored is None:
            conn.execute(
                SchemaVersion.__table__.insert().values(id=1, version=current)
            )

        if current > LATEST_SCHEMA_VERSION:
            _require_current_schema(current)

        for migration in SCHEMA_MIGRATIONS:
            if migration.version <= current:
                continue
            _apply_migration(conn, migration)
            conn.execute(
                SchemaVersion.__table__.update()
                .where(SchemaVersion.id == 1)
                .values(version=migration.version)
            )
            current = migration.version
        return from_version, current


@dataclass(frozen=True)
class _ColumnAddition:
    table: str
    column: Column


@dataclass(frozen=True)
class _Migration:
    version: int
    columns: tuple[_ColumnAddition, ...]
    indexes: tuple[tuple[str, str], ...] = ()
    # Tables the migration is responsible for. They are created by the
    # create_all that runs before _migrate; listing them here keeps baseline
    # inference honest for unversioned legacy databases that predate them.
    tables: tuple[str, ...] = ()


SCHEMA_MIGRATIONS = (
    _Migration(
        1,
        (
            _ColumnAddition(
                "tasks",
                Column("progress", Integer, nullable=False, server_default=text("0")),
            ),
            _ColumnAddition(
                "tasks",
                Column(
                    "open_dispatch",
                    Boolean,
                    nullable=False,
                    server_default=false(),
                ),
            ),
        ),
    ),
    _Migration(
        2,
        (
            _ColumnAddition("tasks", Column("pipeline_json", Text, nullable=True)),
            _ColumnAddition(
                "tasks",
                Column(
                    "pipeline_stage", Integer, nullable=False, server_default=text("0")
                ),
            ),
            _ColumnAddition(
                "approvals",
                Column(
                    "reject_token_hash",
                    String(128),
                    nullable=False,
                    server_default=text("''"),
                ),
            ),
        ),
    ),
    _Migration(
        3,
        (
            _ColumnAddition(
                "pipeline_templates",
                Column(
                    "match_terms_json",
                    Text,
                    nullable=False,
                    server_default=text("'[]'"),
                ),
            ),
            _ColumnAddition(
                "pipeline_templates",
                Column(
                    "acceptance_json",
                    Text,
                    nullable=False,
                    server_default=text("'[]'"),
                ),
            ),
        ),
    ),
    _Migration(
        4,
        (
            _ColumnAddition(
                "task_events",
                Column(
                    "event_type",
                    String(32),
                    nullable=False,
                    server_default=text("'task'"),
                ),
            ),
            _ColumnAddition("task_events", Column("event_key", String(64))),
            _ColumnAddition("task_events", Column("parent_key", String(64))),
            _ColumnAddition(
                "task_events",
                Column(
                    "payload_json",
                    Text,
                    nullable=False,
                    server_default=text("'{}'"),
                ),
            ),
        ),
        (("task_events", "ix_task_events_event_key"),),
    ),
    _Migration(
        5,
        (
            _ColumnAddition(
                "nodes",
                Column("runtimes_probed_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "nodes",
                Column("data_dirs_probed_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "node_runtimes",
                Column("path_hint", String(256), nullable=True),
            ),
            _ColumnAddition(
                "node_runtimes",
                Column("data_changed_at", DateTime(timezone=True), nullable=True),
            ),
        ),
    ),
    _Migration(
        6,
        (
            # ``create_all`` creates a missing additive table before migrations
            # run. Listing every column here makes both fresh-schema baselining
            # and upgrades from a stamped v5 database recognize this as one
            # explicit, versioned schema change.
            _ColumnAddition(
                "task_attempts", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "task_attempts", Column("task_id", String(32), nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("seq", Integer, nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("attempt_key", String(64), nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("reporter_kind", String(16), nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("reporter_id", String(64), nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("duty", String(64), nullable=True)
            ),
            _ColumnAddition(
                "task_attempts", Column("outcome", String(16), nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("reason", String(240), nullable=True)
            ),
            _ColumnAddition(
                "task_attempts", Column("exit_status", Integer, nullable=True)
            ),
            _ColumnAddition(
                "task_attempts", Column("started_at", String(40), nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("ended_at", String(40), nullable=False)
            ),
            _ColumnAddition(
                "task_attempts", Column("reported_at", String(40), nullable=False)
            ),
        ),
        (
            ("task_attempts", "ux_task_attempts_task_seq"),
            ("task_attempts", "ux_task_attempts_attempt_key"),
        ),
    ),
    _Migration(
        7,
        (
            _ColumnAddition(
                "task_dependencies", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "task_dependencies",
                Column("dependent_id", String(32), nullable=False),
            ),
            _ColumnAddition(
                "task_dependencies",
                Column("prerequisite_id", String(32), nullable=False),
            ),
            _ColumnAddition(
                "task_dependencies",
                Column(
                    "kind",
                    String(16),
                    nullable=False,
                    server_default=text("'blocks'"),
                ),
            ),
            _ColumnAddition(
                "task_dependencies",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
        ),
        (
            ("task_dependencies", "ux_task_dependencies_edge"),
            ("task_dependencies", "ix_task_dependencies_dependent"),
            ("task_dependencies", "ix_task_dependencies_prerequisite"),
        ),
    ),
    _Migration(
        8,
        (
            _ColumnAddition(
                "actors",
                Column(
                    "role", String(128), nullable=False, server_default=text("''")
                ),
            ),
            _ColumnAddition(
                "actors",
                Column("goal", Text, nullable=False, server_default=text("''")),
            ),
        ),
    ),
    _Migration(
        9,
        (
            _ColumnAddition(
                "nodes",
                Column(
                    "membership_status",
                    String(16),
                    nullable=False,
                    server_default=text("'admitted'"),
                ),
            ),
            _ColumnAddition(
                "nodes",
                Column(
                    "admitted_by",
                    String(64),
                    nullable=False,
                    server_default=text("'migration-v9'"),
                ),
            ),
            _ColumnAddition(
                "nodes",
                Column("admitted_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "nodes", Column("retired_by", String(64), nullable=True)
            ),
            _ColumnAddition(
                "nodes", Column("retired_at", DateTime(timezone=True), nullable=True)
            ),
        ),
    ),
    _Migration(
        10,
        (
            _ColumnAddition(
                "api_tokens",
                Column("expires_at", DateTime(timezone=True), nullable=True),
            ),
        ),
    ),
    _Migration(
        11,
        (
            _ColumnAddition(
                "tasks",
                Column("due_at", Date, nullable=True),
            ),
        ),
    ),
    _Migration(
        12,
        (
            _ColumnAddition(
                "skills",
                Column(
                    "source_kind",
                    String(32),
                    nullable=False,
                    server_default=text("'local'"),
                ),
            ),
            _ColumnAddition(
                "skills",
                Column(
                    "source_snapshot_json",
                    Text,
                    nullable=False,
                    server_default=text("'{}'"),
                ),
            ),
            _ColumnAddition(
                "skills", Column("imported_by", String(64), nullable=True)
            ),
            _ColumnAddition(
                "skills",
                Column("imported_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "skill_bindings", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "skill_bindings", Column("actor_id", String(64), nullable=False)
            ),
            _ColumnAddition(
                "skill_bindings", Column("skill_id", Integer, nullable=False)
            ),
            _ColumnAddition(
                "skill_bindings",
                Column(
                    "enabled", Boolean, nullable=False, server_default=text("1")
                ),
            ),
            _ColumnAddition(
                "skill_bindings",
                Column(
                    "created_by",
                    String(64),
                    nullable=False,
                    server_default=text("''"),
                ),
            ),
            _ColumnAddition(
                "skill_bindings",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "skill_bindings",
                Column("updated_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "skill_binding_events", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("actor_id", String(64), nullable=False),
            ),
            _ColumnAddition(
                "skill_binding_events", Column("seq", Integer, nullable=False)
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("skill_id", Integer, nullable=False),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column(
                    "skill_name",
                    String(128),
                    nullable=False,
                    server_default=text("''"),
                ),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("action", String(16), nullable=False),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("who", String(64), nullable=False),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("did", String(240), nullable=False),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("from_enabled", Boolean, nullable=True),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("to_enabled", Boolean, nullable=True),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column(
                    "payload_json",
                    Text,
                    nullable=False,
                    server_default=text("'{}'"),
                ),
            ),
            _ColumnAddition(
                "skill_binding_events",
                Column("at", DateTime(timezone=True), nullable=False),
            ),
        ),
        (
            ("skill_bindings", "ux_skill_bindings_actor_skill"),
            ("skill_bindings", "ix_skill_bindings_skill"),
            ("skill_bindings", "ix_skill_bindings_actor"),
            ("skill_binding_events", "ux_skill_binding_events_actor_seq"),
            ("skill_binding_events", "ix_skill_binding_events_skill"),
        ),
    ),
    _Migration(
        13,
        (
            _ColumnAddition(
                "tasks",
                Column(
                    "lease_term", Integer, nullable=False, server_default=text("0")
                ),
            ),
            _ColumnAddition(
                "tasks", Column("lease_expires_at", DateTime(timezone=True), nullable=True)
            ),
            _ColumnAddition(
                "tasks",
                Column("lease_heartbeat_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "tasks",
                Column("lease_claimed_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "tasks",
                Column("lease_started_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "tasks",
                Column(
                    "retry_count", Integer, nullable=False, server_default=text("0")
                ),
            ),
            _ColumnAddition(
                "tasks", Column("failure_class", String(32), nullable=True)
            ),
            _ColumnAddition(
                "tasks", Column("workdir_key", String(128), nullable=True)
            ),
            _ColumnAddition(
                "tasks",
                Column("hall_opened_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "tasks",
                Column(
                    "unclaimed_escalated",
                    Boolean,
                    nullable=False,
                    server_default=false(),
                ),
            ),
            _ColumnAddition(
                "task_attempts", Column("lease_term", Integer, nullable=True)
            ),
            _ColumnAddition(
                "task_attempts",
                Column("trigger_source", String(32), nullable=True),
            ),
            _ColumnAddition(
                "task_attempts",
                Column("session_ref", String(128), nullable=True),
            ),
            _ColumnAddition(
                "task_attempts",
                Column("checkpoint_ref", String(128), nullable=True),
            ),
            _ColumnAddition(
                "task_attempts",
                Column("failure_class", String(32), nullable=True),
            ),
            _ColumnAddition(
                "task_attempts",
                Column("workdir_key", String(128), nullable=True),
            ),
            _ColumnAddition(
                "workdir_locks",
                Column("workdir_key", String(128), primary_key=True),
            ),
            _ColumnAddition(
                "workdir_locks", Column("task_id", String(32), nullable=False)
            ),
            _ColumnAddition(
                "workdir_locks", Column("holder", String(64), nullable=False)
            ),
            _ColumnAddition(
                "workdir_locks", Column("lease_term", Integer, nullable=False)
            ),
            _ColumnAddition(
                "workdir_locks",
                Column("acquired_at", DateTime(timezone=True), nullable=False),
            ),
        ),
        (
            ("tasks", "ix_tasks_lease_expires_at"),
            ("workdir_locks", "ix_workdir_locks_task_id"),
        ),
    ),
    _Migration(
        14,
        (),
        tables=("seq_counters",),
    ),
    _Migration(
        15,
        (
            _ColumnAddition(
                "tasks",
                Column("squad_id", String(64), nullable=True),
            ),
            _ColumnAddition("squads", Column("id", String(64), primary_key=True)),
            _ColumnAddition(
                "squads",
                Column(
                    "display_name",
                    String(128),
                    nullable=False,
                    server_default=text("''"),
                ),
            ),
            _ColumnAddition(
                "squads", Column("leader_id", String(64), nullable=False)
            ),
            _ColumnAddition(
                "squads", Column("created_by", String(64), nullable=False)
            ),
            _ColumnAddition(
                "squads",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "squad_members", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "squad_members", Column("squad_id", String(64), nullable=False)
            ),
            _ColumnAddition(
                "squad_members", Column("actor_id", String(64), nullable=False)
            ),
            _ColumnAddition(
                "squad_members",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_schedules", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("schedule_key", String(128), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("title", String(256), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("fire_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("created_by", String(64), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("holder", String(64), nullable=True),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column(
                    "open_dispatch",
                    Boolean,
                    nullable=False,
                    server_default=text("1"),
                ),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("squad_id", String(64), nullable=True),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("dept", String(64), nullable=True),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column(
                    "priority",
                    String(16),
                    nullable=False,
                    server_default=text("'none'"),
                ),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column(
                    "acceptance_json",
                    Text,
                    nullable=False,
                    server_default=text("'[]'"),
                ),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column(
                    "note",
                    String(240),
                    nullable=False,
                    server_default=text("''"),
                ),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("repeat_seconds", Integer, nullable=True),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column(
                    "status",
                    String(16),
                    nullable=False,
                    server_default=text("'pending'"),
                ),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("last_fired_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("last_task_id", String(32), nullable=True),
            ),
            _ColumnAddition(
                "dispatch_schedules",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_triggers", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "dispatch_triggers",
                Column("source", String(16), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_triggers",
                Column("idempotency_key", String(128), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_triggers",
                Column("request_hash", String(64), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_triggers",
                Column("task_id", String(32), nullable=False),
            ),
            _ColumnAddition(
                "dispatch_triggers",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
        ),
        (
            ("squads", "ix_squads_leader_id"),
            ("squad_members", "ux_squad_members_pair"),
            ("squad_members", "ix_squad_members_actor_id"),
            ("dispatch_schedules", "ux_dispatch_schedules_key"),
            ("dispatch_schedules", "ix_dispatch_schedules_fire_at"),
            ("dispatch_triggers", "ux_dispatch_triggers_source_key"),
        ),
        tables=(
            "squads",
            "squad_members",
            "dispatch_schedules",
            "dispatch_triggers",
        ),
    ),
    _Migration(
        16,
        (
            _ColumnAddition("tasks", Column("source_channel", String(64), nullable=True)),
            _ColumnAddition("tasks", Column("source_user", String(128), nullable=True)),
            _ColumnAddition("channel_tokens", Column("id", Integer, primary_key=True)),
            _ColumnAddition(
                "channel_tokens",
                Column("token_hash", String(128), nullable=False),
            ),
            _ColumnAddition(
                "channel_tokens",
                Column("channel_id", String(64), nullable=False),
            ),
            _ColumnAddition(
                "channel_tokens",
                Column("label", String(128), nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "channel_tokens",
                Column("disabled", Boolean, nullable=False, server_default=false()),
            ),
            _ColumnAddition(
                "channel_tokens",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "channel_tokens",
                Column("last_used_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition("channel_users", Column("id", Integer, primary_key=True)),
            _ColumnAddition(
                "channel_users",
                Column("channel_id", String(64), nullable=False),
            ),
            _ColumnAddition(
                "channel_users",
                Column("channel_user_id", String(128), nullable=False),
            ),
            _ColumnAddition(
                "channel_users",
                Column("actor_id", String(64), nullable=False),
            ),
            _ColumnAddition(
                "channel_users",
                Column("display_name", String(128), nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "channel_users",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "enroll_applications", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("fingerprint", String(128), nullable=False),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("requested_actor_id", String(64), nullable=False),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("display_name", String(128), nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("runtime", String(64), nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("model", String(64), nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("node_id", String(64), nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("capabilities_json", Text, nullable=False, server_default=text("'[]'")),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("status", String(16), nullable=False, server_default=text("'pending'")),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("decided_by", String(64), nullable=True),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("decision_note", Text, nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "enroll_applications",
                Column("decided_at", DateTime(timezone=True), nullable=True),
            ),
        ),
        (
            ("channel_tokens", "ix_channel_tokens_token_hash"),
            ("channel_tokens", "ix_channel_tokens_channel_id"),
            ("channel_users", "ux_channel_users_pair"),
            ("channel_users", "ix_channel_users_actor_id"),
            ("enroll_applications", "ix_enroll_applications_fingerprint"),
            ("enroll_applications", "ix_enroll_applications_status"),
        ),
        tables=("channel_tokens", "channel_users", "enroll_applications"),
    ),
    _Migration(
        17,
        (),
        (
            ("card_pipeline_instances", "ux_card_pipeline_instances_key"),
            ("card_pipeline_instances", "ix_card_pipeline_instances_template"),
            ("card_pipeline_instances", "ix_card_pipeline_instances_status"),
        ),
        tables=(
            "card_pipeline_templates",
            "card_pipeline_instances",
        ),
    ),
    _Migration(
        18,
        (
            _ColumnAddition(
                "users",
                Column(
                    "todo_propose_grants_json",
                    Text,
                    nullable=False,
                    server_default=text("'[]'"),
                ),
            ),
            _ColumnAddition(
                "todo_proposals", Column("id", String(40), primary_key=True)
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("owner_user_id", Integer, nullable=False),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("proposed_by", String(64), nullable=False),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("title", String(256), nullable=False),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("notes", Text, nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "todo_proposals", Column("due_at", Date, nullable=True)
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("remind_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("source_session_id", Integer, nullable=True),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("source_message_id", String(128), nullable=True),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("source_channel", String(64), nullable=True),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("source_backlink", String(512), nullable=True),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("dedup_key", String(128), nullable=True),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column(
                    "status",
                    String(16),
                    nullable=False,
                    server_default=text("'pending'"),
                ),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("todo_item_id", String(40), nullable=True),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "todo_proposals",
                Column("updated_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "todo_items", Column("id", String(40), primary_key=True)
            ),
            _ColumnAddition(
                "todo_items",
                Column("owner_user_id", Integer, nullable=False),
            ),
            _ColumnAddition(
                "todo_items",
                Column("title", String(256), nullable=False),
            ),
            _ColumnAddition(
                "todo_items",
                Column("notes", Text, nullable=False, server_default=text("''")),
            ),
            _ColumnAddition(
                "todo_items",
                Column(
                    "status",
                    String(16),
                    nullable=False,
                    server_default=text("'open'"),
                ),
            ),
            _ColumnAddition(
                "todo_items", Column("due_at", Date, nullable=True)
            ),
            _ColumnAddition(
                "todo_items",
                Column("remind_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "todo_items",
                Column("proposal_id", String(40), nullable=True),
            ),
            _ColumnAddition(
                "todo_items",
                Column("source_session_id", Integer, nullable=True),
            ),
            _ColumnAddition(
                "todo_items",
                Column("source_message_id", String(128), nullable=True),
            ),
            _ColumnAddition(
                "todo_items",
                Column("source_channel", String(64), nullable=True),
            ),
            _ColumnAddition(
                "todo_items",
                Column("source_backlink", String(512), nullable=True),
            ),
            _ColumnAddition(
                "todo_items",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "todo_items",
                Column("updated_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "todo_events", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "todo_events",
                Column("todo_item_id", String(40), nullable=True),
            ),
            _ColumnAddition(
                "todo_events",
                Column("proposal_id", String(40), nullable=True),
            ),
            _ColumnAddition(
                "todo_events", Column("seq", Integer, nullable=False)
            ),
            _ColumnAddition(
                "todo_events",
                Column("event_type", String(32), nullable=False),
            ),
            _ColumnAddition(
                "todo_events", Column("who", String(64), nullable=False)
            ),
            _ColumnAddition(
                "todo_events",
                Column("who_kind", String(16), nullable=False),
            ),
            _ColumnAddition(
                "todo_events", Column("did", String(240), nullable=False)
            ),
            _ColumnAddition(
                "todo_events", Column("reason", String(240), nullable=True)
            ),
            _ColumnAddition(
                "todo_events",
                Column(
                    "payload_json",
                    Text,
                    nullable=False,
                    server_default=text("'{}'"),
                ),
            ),
            _ColumnAddition(
                "todo_events",
                Column("at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "reminder_deliveries", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column("todo_item_id", String(40), nullable=False),
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column("owner_user_id", Integer, nullable=False),
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column("scheduled_for", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column(
                    "channel",
                    String(32),
                    nullable=False,
                    server_default=text("'pending'"),
                ),
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column("delivery_key", String(192), nullable=False),
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column(
                    "status",
                    String(16),
                    nullable=False,
                    server_default=text("'pending'"),
                ),
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
            _ColumnAddition(
                "reminder_deliveries",
                Column("delivered_at", DateTime(timezone=True), nullable=True),
            ),
            _ColumnAddition(
                "todo_task_links", Column("id", Integer, primary_key=True)
            ),
            _ColumnAddition(
                "todo_task_links",
                Column("todo_item_id", String(40), nullable=False),
            ),
            _ColumnAddition(
                "todo_task_links",
                Column("task_id", String(32), nullable=False),
            ),
            _ColumnAddition(
                "todo_task_links",
                Column("created_by", String(64), nullable=False),
            ),
            _ColumnAddition(
                "todo_task_links",
                Column("created_at", DateTime(timezone=True), nullable=False),
            ),
        ),
        (
            ("todo_proposals", "ux_todo_proposals_owner_dedup"),
            ("todo_proposals", "ix_todo_proposals_owner_status"),
            ("todo_proposals", "ix_todo_proposals_proposed_by"),
            ("todo_items", "ix_todo_items_owner_status"),
            ("todo_items", "ix_todo_items_owner_due"),
            ("todo_items", "ix_todo_items_remind_at"),
            ("todo_events", "ux_todo_events_item_seq"),
            ("todo_events", "ux_todo_events_proposal_seq"),
            ("todo_events", "ix_todo_events_item"),
            ("todo_events", "ix_todo_events_proposal"),
            ("reminder_deliveries", "ux_reminder_deliveries_key"),
            ("reminder_deliveries", "ux_reminder_deliveries_slot"),
            ("reminder_deliveries", "ix_reminder_deliveries_due"),
            ("todo_task_links", "ux_todo_task_links_todo"),
            ("todo_task_links", "ux_todo_task_links_task"),
        ),
        tables=(
            "todo_proposals",
            "todo_items",
            "todo_events",
            "reminder_deliveries",
            "todo_task_links",
        ),
    ),
)
LATEST_SCHEMA_VERSION = SCHEMA_MIGRATIONS[-1].version


def _baseline_version(conn: Connection) -> int:
    """Return the newest contiguous migration already present in the schema."""
    inspector = inspect(conn)
    version = 0
    for migration in SCHEMA_MIGRATIONS:
        if not _migration_is_present(inspector, migration):
            break
        version = migration.version
    return version


def _migration_is_present(inspector, migration: _Migration) -> bool:
    for table_name in migration.tables:
        if not inspector.has_table(table_name):
            return False

    columns: dict[str, set[str]] = {}
    for addition in migration.columns:
        if addition.table not in columns:
            if not inspector.has_table(addition.table):
                return False
            columns[addition.table] = {
                column["name"] for column in inspector.get_columns(addition.table)
            }
        if addition.column.name not in columns[addition.table]:
            return False

    indexes: dict[str, set[str]] = {}
    for table_name, index_name in migration.indexes:
        if table_name not in indexes:
            if not inspector.has_table(table_name):
                return False
            indexes[table_name] = {
                index["name"] for index in inspector.get_indexes(table_name)
            }
        if index_name not in indexes[table_name]:
            return False
    return True


def _apply_migration(conn: Connection, migration: _Migration) -> None:
    inspector = inspect(conn)
    newly_added: set[tuple[str, str]] = set()
    for addition in migration.columns:
        existing = {
            column["name"] for column in inspector.get_columns(addition.table)
        }
        if addition.column.name in existing:
            continue
        table_name = conn.dialect.identifier_preparer.quote(addition.table)
        definition = CreateColumn(addition.column).compile(dialect=conn.dialect)
        conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {definition}")
        newly_added.add((addition.table, addition.column.name))
        inspector.clear_cache()

    if ("tasks", "progress") in newly_added:
        # Keep the done⇒100 invariant true for rows created before progress.
        conn.exec_driver_sql("UPDATE tasks SET progress = 100 WHERE status = 'done'")

    if ("nodes", "membership_status") in newly_added:
        # Version 9 makes the implicit legacy roster explicit without locking
        # out machines that were already reporting before admission existed.
        conn.exec_driver_sql(
            "UPDATE nodes SET membership_status = 'admitted', "
            "admitted_by = 'migration-v9', admitted_at = CURRENT_TIMESTAMP"
        )

    for table_name, index_name in migration.indexes:
        existing = {index["name"] for index in inspector.get_indexes(table_name)}
        if index_name not in existing:
            _create_migration_index(conn, table_name, index_name)
            inspector.clear_cache()


def _create_migration_index(
    conn: Connection, table_name: str, index_name: str
) -> None:
    if index_name != "ix_task_events_event_key":
        table = Base.metadata.tables[table_name]
        index = next(
            (candidate for candidate in table.indexes if candidate.name == index_name),
            None,
        )
        if index is None:
            raise RuntimeError(f"migration index is not declared: {index_name}")
        conn.execute(CreateIndex(index))
        return

    # SQLAlchemy has no dialect-neutral partial-index predicate. The two
    # supported evolution dialects expose equivalent dialect-specific options;
    # another dialect needs an explicit adapter before this migration can run.
    if conn.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError(
            f"partial unique indexes are not configured for {conn.dialect.name}"
        )

    metadata = MetaData()
    task_events = Table(
        "task_events", metadata, Column("event_key", String(64), nullable=True)
    )
    predicate = task_events.c.event_key.is_not(None)
    dialect_option = {f"{conn.dialect.name}_where": predicate}
    index = Index(
        index_name,
        task_events.c.event_key,
        unique=True,
        **dialect_option,
    )
    conn.execute(CreateIndex(index))
