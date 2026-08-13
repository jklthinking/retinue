"""Task-card validation and mutation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml


STATES = ("queued", "doing", "handoff", "blocked", "done", "cancelled")
PRIORITIES = ("urgent", "high", "medium", "low", "none")
DEPENDENCY_KINDS = ("blocks",)
TRANSITIONS = {
    "queued": {"doing", "cancelled"},
    "doing": {"handoff", "blocked", "done", "cancelled"},
    "handoff": {"doing", "cancelled"},
    "blocked": {"doing", "cancelled"},
    "done": set(),
    "cancelled": set(),
}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TASK_ID_RE = re.compile(r"^task-[0-9]{8}-[0-9]{3}$")
SYNCTHING_CONFLICT_RE = re.compile(r"\.sync-conflict-[0-9]{8}-[0-9]{6}-", re.I)
GIT_CONFLICT_RE = re.compile(
    r"(?:\.orig$|[._~-](?:BACKUP|BASE|LOCAL|REMOTE|MERGE_HEAD)(?:[._~-]|$))"
)
STATE_FIELDS = (
    "priority",
    "acceptance",
    "dept",
    "refs",
    "progress",
    "blocked_reason",
    "holder",
    "status",
)
STATE_PAYLOAD_VERSION = 1
ACTED_ON_BEHALF_OF_KEY = "acted_on_behalf_of"
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9._~+/-])/(?!/|\s)(?:[^/\s]+/)*[^/\s]*|"
    r"\b[A-Za-z]:[\\/]|(?:^|\s)\\\\[^\\\s]+\\|\bfile://",
    re.IGNORECASE,
)
_CREDENTIAL_SHAPE = re.compile(
    r"(?:\b(?:bearer|token|api[-_ ]?key|access[-_ ]?key|client[-_ ]?secret|"
    r"password|passwd|credential)\s*[:=]\s*\S+|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"\b(?:sk-|ghp_|xox[baprs]-|AKIA)[A-Za-z0-9_-]{12,}\b|"
    r"\b(?:rtn|rts|rtd)_[A-Za-z0-9_-]{12,}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_COMMAND_LINE_SHAPE = re.compile(r"(?:^|\s)(?:--?[A-Za-z][A-Za-z0-9-]*|\$\(|`)")


class ProtocolError(ValueError):
    """Raised when protocol data or an operation is invalid."""


@dataclass(frozen=True)
class FoldResult:
    """State justified by a task chain, plus any gaps in that evidence."""

    state: dict[str, Any]
    reconstructible: bool
    history_complete: bool
    unknown_fields: tuple[str, ...]
    errors: tuple[str, ...]
    attributions: tuple[dict[str, Any], ...] = ()

    @property
    def completeness(self) -> str:
        if self.errors:
            return "invalid"
        return "complete" if self.reconstructible else "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reconstructible": self.reconstructible,
            "history_complete": self.history_complete,
            "completeness": self.completeness,
            "unknown_fields": list(self.unknown_fields),
            "errors": list(self.errors),
            "attributions": list(self.attributions),
        }


def _require_id(value: Any, field: str) -> None:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ProtocolError(f"{field} must use lowercase letters, digits, and hyphens")


def _require_nonempty(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{field} must be a non-empty string")


def validate_ledger_text(value: str, field: str, *, max_length: int = 240) -> str:
    """Apply the attempts-ledger refusal boundary to content entering a log."""
    if len(value) > max_length:
        raise ProtocolError(f"{field} must not exceed {max_length} characters")
    if "\n" in value or "\r" in value or any(ord(char) < 32 for char in value):
        raise ProtocolError(f"{field} must be one line of plain text")
    if _ABSOLUTE_PATH.search(value):
        raise ProtocolError(f"{field} must not contain an absolute path")
    if _CREDENTIAL_SHAPE.search(value):
        raise ProtocolError(f"{field} must not contain credential-shaped text")
    if _COMMAND_LINE_SHAPE.search(value):
        raise ProtocolError(f"{field} must not contain a command line")
    return value


def _validate_logged_value(value: Any, field: str) -> None:
    if isinstance(value, str):
        validate_ledger_text(value, field)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise ProtocolError(f"{field}[{index}] must be a string")
            validate_ledger_text(item, f"{field}[{index}]")
    elif value is not None and not isinstance(value, (int, bool)):
        raise ProtocolError(f"{field} has an unsupported logged value")


def state_payload(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any],
    *,
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a canonical, validated before/after payload for one event."""
    selected = tuple(fields) if fields is not None else STATE_FIELDS
    changes: dict[str, dict[str, Any]] = {}
    for field in selected:
        if field not in STATE_FIELDS:
            raise ProtocolError(f"unknown state field: {field}")
        old_value = before.get(field) if before is not None else None
        new_value = after[field]
        if before is not None and old_value == new_value:
            continue
        _validate_logged_value(old_value, f"{field}.before")
        _validate_logged_value(new_value, f"{field}.after")
        changes[field] = {"before": old_value, "after": new_value}
    return {"state_version": STATE_PAYLOAD_VERSION, "changes": changes}


def add_acted_on_behalf_of(
    payload: Mapping[str, Any],
    *,
    authorising_identity: str,
    performing_agent: str,
) -> dict[str, Any]:
    """Add execution attribution without replacing the chain author.

    ``who`` remains the identity that authorised the event. This payload says
    which automation actually carried it out, keeping both facts in the same
    append-only evidence the fold already consumes.
    """
    _require_id(authorising_identity, "authorising_identity")
    _require_id(performing_agent, "performing_agent")
    result = dict(payload)
    result[ACTED_ON_BEHALF_OF_KEY] = {
        "authorising_identity": authorising_identity,
        "performing_agent": performing_agent,
    }
    return result


def task_card_state(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project the state fields represented by the portable YAML card."""
    return {
        "priority": task.get("priority"),
        "acceptance": list(task["acceptance"]) if "acceptance" in task else None,
        "dept": task.get("dept"),
        "refs": list(task.get("refs", [])),
        "progress": task.get("progress"),
        "blocked_reason": task.get("blocked_reason"),
        "holder": task["holder"],
        "status": task["status"],
    }


def fold_task_events(events: Iterable[Mapping[str, Any]]) -> FoldResult:
    """Rebuild the provable current state using only an ordered event chain."""
    state: dict[str, Any] = {}
    known: set[str] = set()
    errors: list[str] = []
    attributions: list[dict[str, Any]] = []
    history_complete = False

    for index, event in enumerate(events):
        payload = event.get("payload")
        changes: Mapping[str, Any] = {}
        if payload is not None:
            if not isinstance(payload, Mapping):
                errors.append(f"event {index + 1}: payload is not a mapping")
            else:
                raw_changes = payload.get("changes", {})
                if "changes" in payload and payload.get("state_version") != STATE_PAYLOAD_VERSION:
                    errors.append(f"event {index + 1}: unsupported state payload version")
                elif not isinstance(raw_changes, Mapping):
                    errors.append(f"event {index + 1}: changes is not a mapping")
                else:
                    changes = raw_changes
                    if index == 0:
                        history_complete = set(changes) == set(STATE_FIELDS) and all(
                            isinstance(change, Mapping)
                            and change.get("before") is None
                            for change in changes.values()
                        )

                raw_attribution = payload.get(ACTED_ON_BEHALF_OF_KEY)
                if raw_attribution is not None:
                    if not isinstance(raw_attribution, Mapping):
                        errors.append(f"event {index + 1}: acted-on-behalf-of is not a mapping")
                    else:
                        authority = raw_attribution.get("authorising_identity")
                        performer = raw_attribution.get("performing_agent")
                        if not (
                            isinstance(authority, str)
                            and ID_RE.fullmatch(authority)
                            and isinstance(performer, str)
                            and ID_RE.fullmatch(performer)
                        ):
                            errors.append(
                                f"event {index + 1}: malformed acted-on-behalf-of attribution"
                            )
                        elif event.get("who") is not None and event.get("who") != authority:
                            errors.append(
                                f"event {index + 1}: authorising identity disagrees with who"
                            )
                        else:
                            attributions.append(
                                {
                                    "event": index + 1,
                                    "authorising_identity": authority,
                                    "performing_agent": performer,
                                }
                            )

        for field, change in changes.items():
            if field not in STATE_FIELDS:
                errors.append(f"event {index + 1}: unknown state field {field}")
                continue
            if not isinstance(change, Mapping) or not {"before", "after"} <= change.keys():
                errors.append(f"event {index + 1}: malformed change for {field}")
                continue
            before = change["before"]
            after = change["after"]
            if field in known and state[field] != before:
                errors.append(
                    f"event {index + 1}: {field} before value does not match prior event"
                )
            state[field] = after
            known.add(field)

        for field, from_key, to_key in (
            ("status", "from_status", "to_status"),
            ("holder", "from_holder", "to_holder"),
        ):
            old_value = event.get(from_key)
            new_value = event.get(to_key)
            if field in changes:
                change = changes[field]
                if isinstance(change, Mapping):
                    if old_value is not None and change.get("before") != old_value:
                        errors.append(
                            f"event {index + 1}: {field} payload disagrees with {from_key}"
                        )
                    if new_value is not None and change.get("after") != new_value:
                        errors.append(
                            f"event {index + 1}: {field} payload disagrees with {to_key}"
                        )
                continue
            if old_value is not None:
                if field in known and state[field] != old_value:
                    errors.append(
                        f"event {index + 1}: {from_key} does not match prior event"
                    )
                elif field not in known:
                    state[field] = old_value
                    known.add(field)
            if new_value is not None:
                state[field] = new_value
                known.add(field)

    unknown = tuple(field for field in STATE_FIELDS if field not in known)
    return FoldResult(
        state={field: state[field] for field in STATE_FIELDS if field in known},
        reconstructible=history_complete and not unknown and not errors,
        history_complete=history_complete,
        unknown_fields=unknown,
        errors=tuple(errors),
        attributions=tuple(attributions),
    )


def drift_report(folded: FoldResult, stored: Mapping[str, Any]) -> dict[str, Any]:
    """Compare chain-derived evidence with a stored row/card without mutation."""
    differences = {
        field: {"folded": value, "stored": stored.get(field)}
        for field, value in folded.state.items()
        if stored.get(field) != value
    }
    if differences:
        status = "drift"
    elif folded.errors:
        status = "invalid"
    elif folded.unknown_fields:
        status = "partial"
    else:
        status = "in_sync"
    return {
        "status": status,
        "in_sync": status == "in_sync",
        "differences": differences,
        "fold": folded.to_dict(),
    }


def audit_task_card(task: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only fold and drift report for one portable YAML task card."""
    folded = fold_task_events(task.get("chain", []))
    return drift_report(folded, task_card_state(task))


def _parse_timestamp(value: Any, field: str) -> datetime:
    _require_nonempty(value, field)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError(f"{field} must be an ISO 8601 timestamp") from exc


def _validate_timestamp(value: Any, field: str) -> None:
    _parse_timestamp(value, field)


def validate_transition(old: str, new: str) -> None:
    """Validate a state-machine edge."""
    if old not in STATES or new not in STATES:
        raise ProtocolError(f"unknown status: {old!r} or {new!r}")
    if new not in TRANSITIONS[old]:
        raise ProtocolError(f"illegal status transition: {old} -> {new}")


def validate_task(task: Any) -> None:
    """Validate one task card against protocol v0.2."""
    if not isinstance(task, dict):
        raise ProtocolError("task card must be a mapping")

    required = {"id", "title", "created_by", "status", "holder", "chain", "refs"}
    missing = sorted(required - task.keys())
    if missing:
        raise ProtocolError(f"missing required fields: {', '.join(missing)}")

    if not isinstance(task["id"], str) or not TASK_ID_RE.fullmatch(task["id"]):
        raise ProtocolError("id must match task-YYYYMMDD-NNN")
    _require_nonempty(task["title"], "title")
    _require_id(task["created_by"], "created_by")
    _require_id(task["holder"], "holder")
    if task.get("dept") is not None:
        _require_id(task["dept"], "dept")
    if task.get("next") is not None:
        _require_id(task["next"], "next")
    if "priority" in task and task["priority"] not in PRIORITIES:
        raise ProtocolError(f"priority must be one of: {', '.join(PRIORITIES)}")
    if "acceptance" in task:
        acceptance = task["acceptance"]
        if not isinstance(acceptance, list):
            raise ProtocolError("acceptance must be a list of non-empty strings")
        for index, criterion in enumerate(acceptance):
            _require_nonempty(criterion, f"acceptance[{index}]")
    if "progress" in task:
        progress = task["progress"]
        if not isinstance(progress, int) or isinstance(progress, bool) or not 0 <= progress <= 100:
            raise ProtocolError("progress must be an integer between 0 and 100")
    if "depends_on" in task:
        dependencies = task["depends_on"]
        if not isinstance(dependencies, list):
            raise ProtocolError("depends_on must be a list of task ids")
        if len(dependencies) != len(set(dependencies)):
            raise ProtocolError("depends_on must not contain duplicate task ids")
        for index, task_id in enumerate(dependencies):
            if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
                raise ProtocolError(
                    f"depends_on[{index}] must match task-YYYYMMDD-NNN"
                )
            if task_id == task["id"]:
                raise ProtocolError(f"dependency cycle: {task['id']} -> {task['id']}")

    status = task["status"]
    if status not in STATES:
        raise ProtocolError(f"status must be one of: {', '.join(STATES)}")
    reason = task.get("blocked_reason")
    if status == "blocked":
        _require_nonempty(reason, "blocked_reason")
    elif reason is not None:
        raise ProtocolError("blocked_reason must be null unless status is blocked")

    chain = task["chain"]
    if not isinstance(chain, list):
        raise ProtocolError("chain must be a list")
    for index, event in enumerate(chain):
        if not isinstance(event, dict):
            raise ProtocolError(f"chain[{index}] must be a mapping")
        for field in ("who", "did", "at"):
            if field not in event:
                raise ProtocolError(f"chain[{index}] is missing {field}")
        _require_id(event["who"], f"chain[{index}].who")
        _require_nonempty(event["did"], f"chain[{index}].did")
        _validate_timestamp(event["at"], f"chain[{index}].at")
        for field in ("from_status", "to_status"):
            if event.get(field) is not None and event[field] not in STATES:
                raise ProtocolError(f"chain[{index}].{field} is invalid")
        for field in ("from_holder", "to_holder"):
            if event.get(field) is not None:
                _require_id(event[field], f"chain[{index}].{field}")
        if "payload" in event:
            payload = event["payload"]
            if not isinstance(payload, dict):
                raise ProtocolError(f"chain[{index}].payload must be a mapping")
            if payload.get("state_version") != STATE_PAYLOAD_VERSION:
                raise ProtocolError(f"chain[{index}].payload state_version is invalid")
            changes = payload.get("changes")
            if not isinstance(changes, dict):
                raise ProtocolError(f"chain[{index}].payload.changes must be a mapping")
            for field, change in changes.items():
                if field not in STATE_FIELDS:
                    raise ProtocolError(f"chain[{index}].payload has unknown field {field}")
                if not isinstance(change, dict) or set(change) != {"before", "after"}:
                    raise ProtocolError(f"chain[{index}].payload.{field} is invalid")
                _validate_logged_value(
                    change["before"], f"chain[{index}].payload.{field}.before"
                )
                _validate_logged_value(
                    change["after"], f"chain[{index}].payload.{field}.after"
                )

    refs = task["refs"]
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ProtocolError("refs must be a list of strings")


def load_task(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProtocolError(f"cannot read {path}: {exc}") from exc
    validate_task(data)
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    temporary.replace(path)


def utc_timestamp(at: str | None = None) -> str:
    """Return a fixed-width UTC timestamp for a newly written chain event."""
    value = datetime.now(timezone.utc) if at is None else _parse_timestamp(at, "at")
    if value.tzinfo is None:
        # Preserve the CLI's historical interpretation of an explicit naive
        # value as local wall time, but never persist that local representation.
        value = value.astimezone()
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def create_task(
    directory: Path | str,
    *,
    task_id: str,
    title: str,
    created_by: str,
    holder: str,
    dept: str | None = None,
    priority: str = "none",
    acceptance: Iterable[str] = (),
    depends_on: Iterable[str] = (),
    note: str = "task created",
    at: str | None = None,
) -> Path:
    """Create a queued task card in a task directory."""
    directory = Path(directory)
    path = directory / f"{task_id}.yaml"
    if path.exists():
        raise ProtocolError(f"task already exists: {path}")
    dependencies = list(depends_on)
    task = {
        "id": task_id,
        "title": title,
        "created_by": created_by,
        "dept": dept,
        "priority": priority,
        "acceptance": list(acceptance),
        "depends_on": dependencies,
        "status": "queued",
        "holder": holder,
        "blocked_reason": None,
        "progress": 0,
        "chain": [
            {
                "who": created_by,
                "did": note,
                "at": utc_timestamp(at),
                "from_status": None,
                "to_status": "queued",
                "from_holder": None,
                "to_holder": holder,
            }
        ],
        "next": None,
        "refs": [],
    }
    task["chain"][0]["payload"] = state_payload(None, task_card_state(task))
    validate_task(task)
    if dependencies:
        cards = _tasks_by_id(directory)
        cards[task_id] = task
        validate_dependency_graph(cards)
    _write_yaml(path, task)
    return path


def _tasks_by_id(directory: Path) -> dict[str, dict[str, Any]]:
    if not directory.is_dir():
        return {}
    cards: dict[str, dict[str, Any]] = {}
    for candidate in sorted(directory.glob("*.y*ml")):
        card = load_task(candidate)
        cards[card["id"]] = card
    return cards


def _cycle_after_edge(
    cards: dict[str, dict[str, Any]], dependent_id: str, prerequisite_id: str
) -> list[str] | None:
    """Return the concrete cycle made by dependent -> prerequisite, if any."""
    pending = [(prerequisite_id, [prerequisite_id])]
    visited: set[str] = set()
    while pending:
        current, path = pending.pop(0)
        if current == dependent_id:
            return [dependent_id, *path]
        if current in visited:
            continue
        visited.add(current)
        card = cards.get(current)
        if card is None:
            continue
        for next_id in card.get("depends_on", []):
            pending.append((next_id, [*path, next_id]))
    return None


def validate_dependency_graph(cards: dict[str, dict[str, Any]]) -> None:
    """Validate cross-card references, acyclicity, and cancellation safety."""
    for dependent_id, card in cards.items():
        for prerequisite_id in card.get("depends_on", []):
            prerequisite = cards.get(prerequisite_id)
            if prerequisite is None:
                raise ProtocolError(
                    f"{dependent_id} depends on unknown card {prerequisite_id}"
                )
            cycle = _cycle_after_edge(cards, dependent_id, prerequisite_id)
            if cycle:
                raise ProtocolError(f"dependency cycle: {' -> '.join(cycle)}")
            if (
                prerequisite["status"] == "cancelled"
                and card["status"] not in ("done", "cancelled")
            ):
                raise ProtocolError(
                    f"{dependent_id} has cancelled prerequisite {prerequisite_id}"
                )


def ready_tasks(directory: Path | str) -> list[dict[str, Any]]:
    """Return queued file-mode cards whose prerequisites are all done."""
    cards = _tasks_by_id(Path(directory))
    validate_dependency_graph(cards)
    return [
        card
        for card in cards.values()
        if card["status"] == "queued"
        and all(cards[task_id]["status"] == "done" for task_id in card.get("depends_on", []))
    ]


def add_dependency(
    path: Path | str,
    prerequisite_id: str,
    *,
    note: str = "dependency added",
    who: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Add one finish-to-start prerequisite to a queued file-mode card."""
    path = Path(path)
    task = load_task(path)
    if task["status"] != "queued":
        raise ProtocolError("dependencies may only be changed while a card is queued")
    cards = _tasks_by_id(path.parent)
    prerequisite = cards.get(prerequisite_id)
    if prerequisite is None:
        raise ProtocolError(f"{task['id']} depends on unknown card {prerequisite_id}")
    if prerequisite["status"] == "cancelled":
        raise ProtocolError(f"cancelled card cannot be a prerequisite: {prerequisite_id}")
    dependencies = task.setdefault("depends_on", [])
    if prerequisite_id in dependencies:
        return task
    dependencies.append(prerequisite_id)
    cards[task["id"]] = task
    validate_dependency_graph(cards)
    _append_relation_event(task, note=note, who=who, at=at)
    validate_task(task)
    _write_yaml(path, task)
    return task


def remove_dependency(
    path: Path | str,
    prerequisite_id: str,
    *,
    note: str = "dependency removed",
    who: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Remove one prerequisite while retaining an append-only audit event."""
    path = Path(path)
    task = load_task(path)
    if task["status"] != "queued":
        raise ProtocolError("dependencies may only be changed while a card is queued")
    dependencies = task.setdefault("depends_on", [])
    if prerequisite_id not in dependencies:
        raise ProtocolError(f"dependency not found: {task['id']} -> {prerequisite_id}")
    dependencies.remove(prerequisite_id)
    _append_relation_event(task, note=note, who=who, at=at)
    validate_task(task)
    _write_yaml(path, task)
    return task


def _append_relation_event(
    task: dict[str, Any], *, note: str, who: str | None, at: str | None
) -> None:
    _require_nonempty(note, "note")
    event_who = who or task["holder"]
    _require_id(event_who, "who")
    task["chain"].append(
        {
            "who": event_who,
            "did": note.strip(),
            "at": utc_timestamp(at),
            "from_status": task["status"],
            "to_status": task["status"],
            "from_holder": task["holder"],
            "to_holder": task["holder"],
            "payload": state_payload(task_card_state(task), task_card_state(task)),
        }
    )


def update_task(
    path: Path | str,
    *,
    status: str | None = None,
    holder: str | None = None,
    dept: str | None = None,
    blocked_reason: str | None = None,
    next_holder: str | None = None,
    priority: str | None = None,
    acceptance: Iterable[str] | None = None,
    refs: Iterable[str] = (),
    note: str | None = None,
    who: str | None = None,
    at: str | None = None,
    progress: int | None = None,
) -> dict[str, Any]:
    """Update a task and append one immutable transition or progress event."""
    path = Path(path)
    task = load_task(path)
    old_status = task["status"]
    old_holder = task["holder"]
    new_status = status or old_status
    new_holder = holder or old_holder
    state_changed = new_status != old_status
    holder_changed = new_holder != old_holder

    if state_changed:
        validate_transition(old_status, new_status)
        cards = _tasks_by_id(path.parent)
        if old_status == "queued" and new_status == "doing":
            unfinished = [
                task_id
                for task_id in task.get("depends_on", [])
                if cards.get(task_id, {}).get("status") != "done"
            ]
            if unfinished:
                raise ProtocolError(
                    f"{task['id']} cannot start; unfinished prerequisites: "
                    f"{', '.join(unfinished)}"
                )
        if new_status == "cancelled":
            dependents = [
                card["id"]
                for card in cards.values()
                if task["id"] in card.get("depends_on", [])
                and card["status"] not in ("done", "cancelled")
            ]
            if dependents:
                raise ProtocolError(
                    f"cannot cancel {task['id']}; unfinished dependents: "
                    f"{', '.join(dependents)}"
                )
    if dept is not None:
        _require_id(dept, "dept")
    if priority is not None and priority not in PRIORITIES:
        raise ProtocolError(f"priority must be one of: {', '.join(PRIORITIES)}")
    if progress is not None:
        if not isinstance(progress, int) or isinstance(progress, bool) or not 0 <= progress <= 100:
            raise ProtocolError("progress must be an integer between 0 and 100")
        if new_status not in ("doing", "done"):
            raise ProtocolError("progress is only reportable while a card is doing")

    acceptance_value = (
        list(acceptance)
        if acceptance is not None
        else list(task["acceptance"])
        if "acceptance" in task
        else None
    )
    refs_value = list(task["refs"])
    for ref in refs:
        if ref not in refs_value:
            refs_value.append(ref)
    if new_status == "blocked":
        reason_value = blocked_reason if blocked_reason is not None else task.get("blocked_reason")
        if not reason_value:
            raise ProtocolError("blocked_reason is required when entering blocked")
    else:
        if blocked_reason is not None:
            raise ProtocolError("blocked_reason is only valid with blocked status")
        reason_value = None
    progress_value = progress if progress is not None else task.get("progress")
    if new_status == "done":
        progress_value = 100

    old_state = task_card_state(task)
    new_state = {
        "priority": priority if priority is not None else task.get("priority"),
        "acceptance": acceptance_value,
        "dept": dept if dept is not None else task.get("dept"),
        "refs": refs_value,
        "progress": progress_value,
        "blocked_reason": reason_value,
        "holder": new_holder,
        "status": new_status,
    }
    payload = state_payload(old_state, new_state)
    changed = bool(payload["changes"])
    if changed or note is not None:
        if not note or not note.strip():
            raise ProtocolError("--note must be non-empty for a task change or progress event")
        event_who = who or new_holder
        _require_id(event_who, "who")
        task["chain"].append(
            {
                "who": event_who,
                "did": note,
                "at": utc_timestamp(at),
                "from_status": old_status,
                "to_status": new_status,
                "from_holder": old_holder,
                "to_holder": new_holder,
                "payload": payload,
            }
        )

    task["status"] = new_state["status"]
    task["holder"] = new_state["holder"]
    task["refs"] = new_state["refs"]
    if "blocked_reason" in task or state_changed or blocked_reason is not None:
        task["blocked_reason"] = new_state["blocked_reason"]
    if priority is not None:
        task["priority"] = new_state["priority"]
    if acceptance is not None:
        task["acceptance"] = new_state["acceptance"]
    if dept is not None:
        task["dept"] = new_state["dept"]
    if "progress" in task or progress is not None or new_status == "done":
        task["progress"] = new_state["progress"]
    if next_holder is not None:
        task["next"] = next_holder or None
    validate_task(task)
    _write_yaml(path, task)
    return task


def render_receipt(task: dict[str, Any]) -> str:
    """Render the most recent task event as a two-line IM receipt."""
    validate_task(task)
    if not task["chain"]:
        raise ProtocolError("cannot render a receipt without a chain event")
    event = task["chain"][-1]
    old_status = event.get("from_status") or "—"
    new_status = event.get("to_status") or task["status"]
    old_holder = event.get("from_holder") or "—"
    new_holder = event.get("to_holder") or task["holder"]
    payload = event.get("payload")
    attribution = (
        payload.get(ACTED_ON_BEHALF_OF_KEY)
        if isinstance(payload, Mapping)
        else None
    )
    execution = ""
    if isinstance(attribution, Mapping):
        authority = attribution.get("authorising_identity")
        performer = attribution.get("performing_agent")
        if isinstance(authority, str) and isinstance(performer, str):
            execution = f"　执行：{performer} 代表 {authority}"
    return (
        f"【任务回执】{task['id']} {task['title']}\n"
        f"状态：{old_status} → {new_status}　"
        f"持棒：{old_holder} → {new_holder}　备注：{event['did']}{execution}"
    )


def lint_path(path: Path | str) -> list[tuple[Path, str | None]]:
    """Lint one YAML task or all YAML tasks directly under a directory."""
    path = Path(path)
    if path.is_dir():
        yaml_files = {*path.glob("*.yaml"), *path.glob("*.yml")}
        conflict_files = {
            candidate
            for candidate in path.iterdir()
            if candidate.is_file() and _conflict_copy_kind(candidate.name) is not None
        }
        files = sorted(yaml_files | conflict_files)
        if not files:
            raise ProtocolError(f"no YAML task cards found in {path}")
    elif path.is_file():
        files = [path]
    else:
        raise ProtocolError(f"path does not exist: {path}")

    results: list[tuple[Path, str | None]] = []
    cards: dict[str, dict[str, Any]] = {}
    for candidate in files:
        conflict_kind = _conflict_copy_kind(candidate.name)
        if conflict_kind is not None:
            results.append((candidate, f"{conflict_kind} conflict copy detected"))
            continue
        try:
            task = load_task(candidate)
            if candidate.stem != task["id"]:
                raise ProtocolError(
                    "filename must equal <id>.yaml; possible duplicate or conflict copy"
                )
            cards[task["id"]] = task
            results.append((candidate, None))
        except ProtocolError as exc:
            results.append((candidate, str(exc)))
    if path.is_dir() and cards:
        try:
            validate_dependency_graph(cards)
        except ProtocolError as exc:
            message = str(exc)
            for index, (candidate, error) in enumerate(results):
                if error is None and candidate.stem in message:
                    results[index] = (candidate, message)
                    break
    return results


def _conflict_copy_kind(filename: str) -> str | None:
    """Recognize task-card copies created by common sync and merge tools."""
    lowered = filename.lower()
    looks_like_task_data = "task-" in lowered and (".yaml" in lowered or ".yml" in lowered)
    if not looks_like_task_data:
        return None
    if SYNCTHING_CONFLICT_RE.search(filename):
        return "Syncthing"
    if "conflicted copy" in lowered:
        return "sync-tool"
    if GIT_CONFLICT_RE.search(filename):
        return "Git merge"
    return None
