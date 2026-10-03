"""Observe cc-connect Claude sessions without exporting prompts or chat bodies.

Only native current/history runtime IDs select transcripts. Actual message.model
selects usage cohorts; a shared HOME is never treated as one worker's history.
This is observation, not permission to start a model or mutate task progress.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
import uuid

from adapters.exporters.claude_code import TOKEN_FIELDS, _timestamp, _tokens, collect_metrics

MODEL_RE = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{0,95}$")


def runtime_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None
    return normalized if value.lower() == normalized else None


def _time(value: Any) -> str | None:
    parsed = _timestamp(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else None


def _latest(*values: str | None) -> str | None:
    valid = [value for value in values if value]
    return max(valid) if valid else None


def native_index(source: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read only fixed state keys; dynamic IM identifiers/names/history stay local."""
    if not source.is_dir():
        raise ValueError("native sessions source unavailable")
    ids: dict[str, dict[str, Any]] = {}
    health: dict[str, Any] = {"files": 0, "invalid_files": 0, "native_sessions": 0,
                              "invalid_runtime_ids": 0}
    for path in sorted(source.glob("*.json")):
        if path.is_symlink():
            health["invalid_files"] += 1
            continue
        health["files"] += 1
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            health["invalid_files"] += 1
            continue
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(sessions, (dict, list)):
            health["invalid_files"] += 1
            continue
        entries = sessions.values() if isinstance(sessions, dict) else sessions
        for item in entries:
            if not isinstance(item, dict) or item.get("agent_type", "claudecode") != "claudecode":
                continue
            health["native_sessions"] += 1
            current = runtime_id(item.get("agent_session_id"))
            past = item.get("past_agent_session_ids", [])
            if not isinstance(past, list):
                past = []
                health["invalid_runtime_ids"] += 1
            for raw_id, is_current in [(item.get("agent_session_id"), True),
                                       *((value, False) for value in past)]:
                native_id = runtime_id(raw_id)
                if native_id is None:
                    if raw_id:
                        health["invalid_runtime_ids"] += 1
                    continue
                fact = ids.setdefault(native_id, {"current": False, "native_updated_at": None,
                                                  "native_last_activity_at": None})
                fact["current"] |= is_current and current == native_id
                fact["native_updated_at"] = _latest(fact["native_updated_at"], _time(item.get("updated_at")))
                # Activity on the replacement session is not attributed to past IDs.
                if is_current:
                    fact["native_last_activity_at"] = _latest(
                        fact["native_last_activity_at"], _time(item.get("last_user_activity")))
    health["runtime_ids"] = len(ids)
    health["current_runtime_ids"] = sum(item["current"] for item in ids.values())
    return ids, health


def collect_observation(*, sessions_source: Path | str, transcripts_source: Path | str,
                        timezone_name: str = "Asia/Shanghai",
                        now: datetime | None = None) -> dict[str, Any]:
    sessions_source = Path(sessions_source).expanduser().resolve()
    transcripts_source = Path(transcripts_source).expanduser().resolve()
    if not transcripts_source.is_dir():
        raise ValueError("transcript source unavailable")
    ids, native_health = native_index(sessions_source)
    cohorts: dict[str, list[tuple[Path, int, dict[str, Any]]]] = defaultdict(list)
    deliveries: dict[tuple[str, str], dict[str, Any]] = {}
    coverage = {"scanned_files": 0, "matched_files": 0, "invalid_records": 0,
                "unreadable_files": 0, "matched_usage_records": 0, "unattributed_records": 0,
                "unattributed_usage_records": 0, "zero_token_synthetic_records": 0,
                "invalid_usage_records": 0, "ambiguous_model_deliveries": 0,
                "incomplete_deliveries": 0}
    matched_files: set[Path] = set()
    observed: set[str] = set()
    activity: dict[str, str] = {}
    delivery_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(transcripts_source.rglob("*.jsonl")):
        # Do not traverse an operator accidentally pointing at a symlink tree.
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents
                                    if parent != transcripts_source and transcripts_source in parent.parents):
            coverage["unreadable_files"] += 1
            continue
        coverage["scanned_files"] += 1
        try:
            with path.open(encoding="utf-8", errors="strict") as stream:
                for number, line in enumerate(stream, 1):
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        coverage["invalid_records"] += 1
                        continue
                    if not isinstance(record, dict):
                        continue
                    native_id = runtime_id(record.get("sessionId"))
                    if native_id not in ids:
                        continue
                    observed.add(native_id)
                    matched_files.add(path)
                    stamp = _time(record.get("timestamp"))
                    if stamp:
                        activity[native_id] = _latest(activity.get(native_id), stamp)
                    message = record.get("message")
                    if record.get("type") != "assistant" or not isinstance(message, dict):
                        continue
                    model = message.get("model")
                    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
                        coverage["unattributed_records"] += 1
                        raw_usage = message.get("usage")
                        parsed_usage = _tokens(raw_usage)
                        # A known synthetic status with four explicit zero
                        # counters has no usage to allocate. Every other missing
                        # or invalid model is unknown attribution, not permission
                        # to replace complete totals with the remaining subset.
                        if (model == "<synthetic>" and isinstance(raw_usage, dict)
                                and all(field in raw_usage for field in TOKEN_FIELDS)
                                and parsed_usage is not None and not any(parsed_usage.values())):
                            coverage["zero_token_synthetic_records"] += 1
                        else:
                            coverage["unattributed_usage_records"] += 1
                        continue
                    # No title, message content, tools, prompts, history, IM keys,
                    # or unknown usage properties survive this whitelist.
                    usage = message.get("usage")
                    message_id = message.get("id")
                    stable_id = isinstance(message_id, str) and bool(message_id)
                    identity = message_id if stable_id else f"{path.relative_to(transcripts_source)}:{number}"
                    evidence = delivery_evidence.setdefault((native_id, identity), {"models": set(), "valid_usage": False})
                    evidence["models"].add(model)
                    if usage is not None:
                        if _tokens(usage) is None or stamp is None or not stable_id:
                            coverage["invalid_usage_records"] += 1
                        else:
                            evidence["valid_usage"] = True
                    compact = {"sessionId": native_id, "timestamp": stamp,
                               "message": {"id": message.get("id"), "model": model}}
                    if isinstance(usage, dict):
                        compact["message"]["usage"] = {key: usage.get(key, 0) for key in TOKEN_FIELDS}
                        coverage["matched_usage_records"] += 1
                    cohorts[model].append((path, number, compact))
                    fact = deliveries.setdefault((native_id, model), {"ids": set(), "started_at": None,
                                                                     "last_delivery_at": None})
                    fact["ids"].add(identity)
                    if stamp:
                        fact["started_at"] = min(fact["started_at"], stamp) if fact["started_at"] else stamp
                        fact["last_delivery_at"] = _latest(fact["last_delivery_at"], stamp)
        except (OSError, UnicodeDecodeError):
            coverage["unreadable_files"] += 1
    coverage["matched_files"] = len(matched_files)
    coverage["ambiguous_model_deliveries"] = sum(len(fact["models"]) > 1 for fact in delivery_evidence.values())
    coverage["incomplete_deliveries"] = sum(not fact["valid_usage"] for fact in delivery_evidence.values())
    coverage["missing_runtime_ids"] = len(set(ids) - observed)
    coverage["complete"] = bool(native_health["files"] and not native_health["invalid_files"]
                                and not native_health["invalid_runtime_ids"]
                                and not coverage["invalid_records"]
                                and not coverage["invalid_usage_records"]
                                and not coverage["unattributed_usage_records"]
                                and not coverage["ambiguous_model_deliveries"]
                                and not coverage["incomplete_deliveries"]
                                and not coverage["unreadable_files"] and not coverage["missing_runtime_ids"])
    usage_cohorts = {}
    for model, records in sorted(cohorts.items()):
        snapshot = collect_metrics(transcripts_source, agent_id="native-observer",
                                   timezone_name=timezone_name, now=now, records=records)
        # No machine paths or identifiers are needed for token statistics.
        snapshot["source"].pop("path", None)
        usage_cohorts[model] = snapshot
    observations = []
    latest_model: dict[str, tuple[str, str]] = {}
    for (native_id, model), fact in deliveries.items():
        stamp = fact["last_delivery_at"]
        if stamp and (native_id not in latest_model or stamp > latest_model[native_id][0]):
            latest_model[native_id] = (stamp, model)
    for (native_id, model), fact in sorted(deliveries.items()):
        observations.append({
            "native_id": native_id, "model": model,
            "current": ids[native_id]["current"] and latest_model.get(native_id, (None, None))[1] == model,
            "native_current": ids[native_id]["current"],
            "message_count": len(fact["ids"]), "message_count_definition": "unique assistant deliveries for actual model",
            "started_at": fact["started_at"], "last_delivery_at": fact["last_delivery_at"],
            "last_active_at": fact["last_delivery_at"], "native_last_active_at": activity.get(native_id),
            "native_updated_at": ids[native_id]["native_updated_at"],
            "native_last_activity_at": ids[native_id]["native_last_activity_at"],
        })
    current = now or datetime.now(timezone.utc)
    return {"schema_version": 1, "reported_source": "cc-connect", "privacy": "metadata",
            "timezone": timezone_name, "observed_at": current.astimezone(timezone.utc).isoformat(),
            "native_health": native_health, "coverage": coverage,
            "observations": observations, "usage_cohorts": usage_cohorts,
            "last_active_at": max(activity.values()) if activity else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-source", required=True)
    parser.add_argument("--transcripts-source", required=True)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    args = parser.parse_args()
    try:
        result = collect_observation(sessions_source=args.sessions_source,
                                     transcripts_source=args.transcripts_source, timezone_name=args.timezone)
    except Exception:
        # The central process needs a fixed failure category, never raw source text.
        print(json.dumps({"schema_version": 1, "error": "source-read-failed"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
