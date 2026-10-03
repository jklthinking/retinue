"""Bounded reports for the collaboration event protocol; no executable inputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.protocol.task import validate_ledger_text


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportIdentity(ReportModel):
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    lease_term: int | None = Field(default=None, ge=1)

    @field_validator("idempotency_key")
    @classmethod
    def safe_key(cls, value: str) -> str:
        return validate_ledger_text(value, "idempotency key", max_length=128)


class ExecutionAuthorizationBody(ReportModel):
    lease_term: int = Field(ge=1, strict=True)
    request_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")

    @field_validator("request_key")
    @classmethod
    def safe_key(cls, value: str) -> str:
        return validate_ledger_text(value, "execution request key", max_length=128)


class DelegationStage(ReportModel):
    name: str = Field(min_length=1, max_length=80)
    holder: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    gate: Literal["auto", "review", "queen"] = "auto"

    @field_validator("name")
    @classmethod
    def bounded_name(cls, value: str) -> str:
        value = validate_ledger_text(value.strip(), "stage name", max_length=80)
        if not value:
            raise ValueError("stage name must be non-empty")
        return value


class DelegationBody(ReportIdentity):
    delegated_to: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    title: str = Field(min_length=1, max_length=240)
    instruction: str = Field(min_length=1, max_length=240)
    acceptance: list[str] = Field(min_length=1, max_length=32)
    parent_run_id: str | None = Field(default=None, pattern=r"^run-[a-f0-9]{32}$")
    module: str | None = Field(default=None, min_length=1, max_length=80)
    pipeline: list[DelegationStage] | None = Field(default=None, min_length=2, max_length=8)

    @field_validator("module")
    @classmethod
    def explicit_module(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = validate_ledger_text(value.strip(), "module", max_length=80)
        if not value:
            raise ValueError("module must be non-empty")
        return value

    @field_validator("title", "instruction")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must be non-empty")
        return validate_ledger_text(value, "delegation text")

    @field_validator("acceptance")
    @classmethod
    def bounded_acceptance(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("acceptance items must be non-empty")
        return [validate_ledger_text(value.strip(), "acceptance") for value in values]


class RunCreateBody(ReportIdentity):
    title: str = Field(min_length=1, max_length=240)
    model: str | None = Field(default=None, max_length=80)
    session_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    runtime_session_id: int | None = Field(default=None, ge=1, strict=True)
    execution_state: Literal["prepared", "started"] = "started"
    module: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("module")
    @classmethod
    def explicit_module(cls, value: str | None) -> str | None:
        return DelegationBody.explicit_module(value)

    @field_validator("session_ref")
    @classmethod
    def safe_session_ref(cls, value: str | None) -> str | None:
        return validate_ledger_text(value, "session reference", max_length=128) if value else None

    @field_validator("title", "model")
    @classmethod
    def bounded_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("text must be non-empty")
        return validate_ledger_text(value, "run text")


class RunProgress(ReportModel):
    completed: int = Field(ge=0, strict=True)
    total: int = Field(ge=1, le=1000000, strict=True)
    unit: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def valid_count(self):
        if self.completed > self.total:
            raise ValueError("completed cannot exceed total")
        self.unit = validate_ledger_text(self.unit.strip(), "progress unit", max_length=40)
        if not self.unit:
            raise ValueError("progress unit must be non-empty")
        return self


class RunWaiting(ReportModel):
    kind: Literal["input", "review", "human", "external", "dependency"]
    owner: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    reason: str = Field(min_length=1, max_length=240)

    @field_validator("reason")
    @classmethod
    def bounded_reason(cls, value: str) -> str:
        value = validate_ledger_text(value.strip(), "waiting reason")
        if not value:
            raise ValueError("waiting reason must be non-empty")
        return value


class CompletedWork(ReportModel):
    summary: str = Field(min_length=1, max_length=240)
    refs: list[str] = Field(default_factory=list, max_length=20)
    revision: str | None = Field(default=None, min_length=1, max_length=128)
    module: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("module")
    @classmethod
    def explicit_module(cls, value: str | None) -> str | None:
        return DelegationBody.explicit_module(value)

    @field_validator("summary", "revision")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = validate_ledger_text(value.strip(), "completed work")
        if not value:
            raise ValueError("completed work must be non-empty")
        return value

    @field_validator("refs")
    @classmethod
    def safe_refs(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence references must be non-empty")
        return [validate_ledger_text(value.strip(), "evidence reference") for value in values]


class ProgressReport(ReportModel):
    """Executor claims and handoff intent, never acceptance or authority."""

    completed: list[CompletedWork] = Field(default_factory=list, max_length=32)
    remaining: list[str] = Field(default_factory=list, max_length=32)
    next_action: str | None = Field(default=None, min_length=1, max_length=240)
    next_owner: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)

    @field_validator("remaining")
    @classmethod
    def safe_remaining(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("remaining work must be non-empty")
        return [validate_ledger_text(value.strip(), "remaining work") for value in values]

    @field_validator("next_action")
    @classmethod
    def safe_action(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = validate_ledger_text(value.strip(), "next action")
        if not value:
            raise ValueError("next action must be non-empty")
        return value


class RunEventBody(ReportIdentity):
    status: Literal["running", "waiting", "succeeded", "failed", "cancelled"]
    note: str = Field(min_length=1, max_length=240)
    progress: RunProgress | None = None
    progress_report: ProgressReport | None = None
    execution_state: Literal["started"] | None = None
    waiting: RunWaiting | None = None
    refs: list[str] = Field(default_factory=list, max_length=20)
    attempt_id: str | None = Field(default=None, pattern=r"^attempt-[a-f0-9]{32}$")

    @model_validator(mode="after")
    def valid_report(self):
        self.note = validate_ledger_text(self.note.strip(), "run note")
        if not self.note:
            raise ValueError("run note must be non-empty")
        if (self.status == "waiting") != (self.waiting is not None):
            raise ValueError("waiting details are required only for waiting status")
        if self.execution_state is not None and self.status != "running":
            raise ValueError("execution start must be reported with running status")
        self.refs = [validate_ledger_text(value, "artifact reference") for value in self.refs]
        if any(not value.strip() for value in self.refs):
            raise ValueError("artifact references must be non-empty")
        return self


class CollaborationRetryBody(ReportModel):
    note: str = Field(min_length=1, max_length=240)

    @field_validator("note")
    @classmethod
    def bounded_note(cls, value: str) -> str:
        value = validate_ledger_text(value.strip(), "retry note")
        if not value:
            raise ValueError("retry note must be non-empty")
        return value


class DelegationPolicyBody(ReportModel):
    allowed_actor_ids: list[str] = Field(default_factory=list, max_length=16)
    max_depth: int = Field(default=3, ge=1, le=3, strict=True)
    max_children: int = Field(default=12, ge=1, le=12, strict=True)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")

    @field_validator("idempotency_key")
    @classmethod
    def safe_key(cls, value: str) -> str:
        return validate_ledger_text(value, "idempotency key", max_length=128)

    @field_validator("allowed_actor_ids")
    @classmethod
    def actor_ids(cls, values: list[str]) -> list[str]:
        from core.protocol.task import ID_RE
        if any(len(value) > 64 or not ID_RE.fullmatch(value) for value in values):
            raise ValueError("allowed actors must be actor IDs")
        return list(dict.fromkeys(values))
