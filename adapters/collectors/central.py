"""Pull native metadata over SSH and report through existing actor APIs.

Operator configuration is the only source of paths, host, worker identities,
and optional session/task bindings. No chat or task text is executed. Tokens
remain on the reporting host; the remote exporter never receives a credential.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from adapters.collectors.cc_connect import MODEL_RE, runtime_id
from core.protocol.task import ID_RE

TASK_RE = re.compile(r"^task-\d{8}-\d{3,}$")


class CollectorError(RuntimeError):
    """Fixed categories only; remote stderr, HTTP bodies and secrets stay private."""


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise CollectorError("invalid-config-schema")
    interval = config.get("interval_seconds", 30)
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not 5 <= interval <= 30:
        raise CollectorError("invalid-poll-interval")
    config["interval_seconds"] = interval
    if config.get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
        raise CollectorError("reporting-timezone-must-be-explicit")
    config["timezone"] = "Asia/Shanghai"
    if not isinstance(config.get("node_id"), str) or not ID_RE.fullmatch(config["node_id"]):
        raise CollectorError("missing-explicit-source-device")
    models = config.get("models")
    if not isinstance(models, dict) or not models:
        raise CollectorError("missing-model-worker-mapping")
    actors: set[str] = set()
    for model, worker in models.items():
        if not MODEL_RE.fullmatch(model) or not isinstance(worker, dict):
            raise CollectorError("invalid-model-worker-mapping")
        actor = worker.get("actor_id", "")
        if not isinstance(actor, str) or not ID_RE.fullmatch(actor) or actor in actors:
            raise CollectorError("worker-needs-one-complete-model-owner")
        actors.add(actor)
        if not isinstance(worker.get("token_file"), str) or not worker["token_file"]:
            raise CollectorError("missing-existing-actor-token-path")
    observer = config.get("observer")
    if observer is not None:
        if (not isinstance(observer, dict) or not isinstance(observer.get("actor_id"), str)
                or not ID_RE.fullmatch(observer["actor_id"]) or observer["actor_id"] in actors
                or not isinstance(observer.get("token_file"), str) or not observer["token_file"]):
            raise CollectorError("invalid-existing-observer-identity")
    bindings: set[tuple[str, str]] = set()
    for binding in config.get("bindings", []):
        if not isinstance(binding, dict) or runtime_id(binding.get("native_id")) is None:
            raise CollectorError("invalid-explicit-native-binding")
        model, task = binding.get("model"), binding.get("task_id")
        if model not in models or not isinstance(task, str) or not TASK_RE.fullmatch(task):
            raise CollectorError("invalid-explicit-task-binding")
        key = (binding["native_id"], model)
        if key in bindings:
            raise CollectorError("duplicate-native-binding")
        bindings.add(key)
    # Validate without reading a credential or contacting either host.
    ssh_argv(config)
    return config


def _remote_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/") or any(c in value for c in "\r\n\0"):
        raise CollectorError("invalid-operator-source-path")
    return value


def ssh_argv(config: dict[str, Any]) -> list[str]:
    remote = config.get("remote", {})
    host = remote.get("host", "")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", host):
        raise CollectorError("invalid-operator-ssh-host")
    root = _remote_path(remote.get("workspace"))
    command = ["/usr/bin/env", "PYTHONPATH=" + root,
               _remote_path(remote.get("python", "/usr/bin/python3")),
               "-m", "adapters.collectors.cc_connect", "--sessions-source",
               _remote_path(remote.get("sessions_source")), "--transcripts-source",
               _remote_path(remote.get("transcripts_source")), "--timezone", "Asia/Shanghai"]
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10"]
    if remote.get("ssh_config"):
        argv += ["-F", remote["ssh_config"]]
    return argv + [host, shlex.join(command)]


def pull(config: dict[str, Any]) -> dict[str, Any]:
    try:
        result = subprocess.run(ssh_argv(config), capture_output=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectorError("native-pull-transport-failed") from exc
    if result.returncode != 0:
        raise CollectorError("native-pull-source-failed")
    if len(result.stdout) > 8 * 1024 * 1024:
        raise CollectorError("native-pull-response-too-large")
    try:
        snapshot = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CollectorError("native-pull-invalid-json") from exc
    if (not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1
            or snapshot.get("privacy") != "metadata" or snapshot.get("reported_source") != "cc-connect"
            or snapshot.get("timezone") != "Asia/Shanghai"):
        raise CollectorError("native-pull-invalid-schema")
    return snapshot


def build_rows(snapshot: dict[str, Any], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bindings = {(item["native_id"], item["model"]): item["task_id"] for item in config.get("bindings", [])}
    sessions = []
    for observation in snapshot["observations"]:
        native_id, model = observation["native_id"], observation["model"]
        if runtime_id(native_id) is None or not MODEL_RE.fullmatch(model):
            continue
        mapped = model in config["models"]
        worker = config["models"].get(model) or config.get("observer")
        if worker is None:
            continue
        # Body fields are reconstructed, never copied wholesale from an exporter.
        sessions.append({
            "actor_id": worker["actor_id"], "runtime": "claude-code",
            "external_id": f"cc-connect:{native_id}:{model}",
            "title": f"cc-connect · {'Claude Code' if mapped else 'historical observation'} · {model}",
            "summary": "", "privacy": "metadata", "messages": [],
            "message_count": observation["message_count"], "started_at": observation["started_at"],
            "updated_at": observation["last_delivery_at"],
            "task_id": bindings.get((native_id, model)), "resume_capable": False,
        })
    metrics = []
    # Full source coverage is required for absolute daily replacement. A missing
    # mount, unmatched history or half-written state must not erase prior totals.
    if snapshot["coverage"]["complete"]:
        for model, usage in snapshot["usage_cohorts"].items():
            if model not in config["models"] or not usage["source"].get("usage_records"):
                continue
            for day in usage["last_7_days"]["daily"]:
                metrics.append({
                    "actor_id": config["models"][model]["actor_id"], "runtime": "claude-code",
                    "date": day["date"],
                    "input_tokens": sum(day.get(field, 0) for field in
                                        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
                    "output_tokens": day["output_tokens"],
                })
    return sessions, metrics


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reserve_session(state: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    key = row["actor_id"] + ":" + row["external_id"]
    history = state.setdefault("sessions", {})
    previous = history.get(key)
    checksum = digest(row)
    cursor = previous["cursor"] if previous and previous["hash"] == checksum else (previous["cursor"] + 1 if previous else 1)
    history[key] = {"hash": checksum, "cursor": cursor}
    return {**row, "cursor": cursor}


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
        json.dump(state, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectorError("report-api-redirect-refused")


def request(url: str, token: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise CollectorError("invalid-report-api-base")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    req = urllib.request.Request(url.rstrip("/") + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                 method="POST" if payload is not None else "GET")
    try:
        with opener.open(req, timeout=15) as response:
            body = response.read(1024 * 1024)
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        # Same-cursor/different-body conflict must never reset history silently.
        raise CollectorError(f"report-api-http-{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise CollectorError("report-api-transport-or-json-failed") from exc


def report_once(snapshot: dict[str, Any], config: dict[str, Any], state: dict[str, Any],
                save: Callable[[], None], *, api=request,
                token_reader: Callable[[str], str] | None = None) -> dict[str, int]:
    sessions, metrics = build_rows(snapshot, config)
    tokens: dict[str, str] = {}
    reader = token_reader or (lambda path: Path(path).read_text(encoding="utf-8").strip())
    workers = list(config["models"].values()) + ([config["observer"]] if config.get("observer") else [])
    for worker in workers:
        actor = worker["actor_id"]
        token = reader(worker["token_file"])
        principal = api(config["url"], token, "/api/auth/me")
        if principal.get("actor_id") != actor or principal.get("kind") != "agent":
            raise CollectorError("report-token-worker-mismatch")
        registry = api(config["url"], token, "/api/actors")
        registered = next((item for item in registry if isinstance(item, dict) and item.get("id") == actor), None)
        expected_model = next((model for model, value in config["models"].items() if value["actor_id"] == actor), None)
        if (registered is None or registered.get("disabled") or registered.get("node") != config["node_id"]
                or expected_model is not None and (registered.get("model") != expected_model
                                                   or registered.get("runtime") != "claude-code")):
            raise CollectorError("registered-device-or-model-mismatch")
        tokens[actor] = token
    result = {"sessions": 0, "metrics": 0}
    for row in sessions:
        payload = reserve_session(state, row)
        # Durable intent before POST makes an unknown response safe to repeat at
        # the same cursor, rather than blindly incrementing or resetting it.
        save()
        api(config["url"], tokens[row["actor_id"]], "/api/sessions/sync", payload)
        result["sessions"] += 1
    for row in metrics:
        api(config["url"], tokens[row["actor_id"]], "/api/metrics/ingest", row)
        result["metrics"] += 1
    return result


def health(snapshot: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    sessions, metrics = build_rows(snapshot, config)
    return {
        "reported_source": "cc-connect", "timezone": snapshot["timezone"],
        "last_observed_at": snapshot["observed_at"], "last_active_at": snapshot["last_active_at"],
        "native_health": snapshot["native_health"], "coverage": snapshot["coverage"],
        "session_rows": len(sessions), "metric_rows": len(metrics),
        "explicit_task_bindings": sum(row["task_id"] is not None for row in sessions),
        "session_destination": "model-workers-and-unmapped-observer" if config.get("observer") else "model-workers",
        "unmapped_models": sorted(set(snapshot["usage_cohorts"]) - set(config["models"])),
        "models": {model: {"sessions": usage["sessions"],
                            "usage_records": usage["source"]["usage_records"],
                            "last_active_at": usage["last_active_at"],
                            "tokens_7d": usage["last_7_days"]["total_tokens"]}
                   for model, usage in snapshot["usage_cohorts"].items()},
    }


@contextmanager
def owner_lock(path: Path):
    """Only one complete owner may replace actor/runtime/day absolute totals."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    stream = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if stream.read(1) == b"":
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise CollectorError("another-collector-owner-is-running") from exc
    try:
        yield
    finally:
        # Closing releases OS locks, including after an unclean process exit.
        stream.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="no token reads, API requests, or local state writes")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--watch", action="store_true")
    parser.add_argument("--push", action="store_true", help="explicitly authorize use of configured existing actor credentials")
    args = parser.parse_args()
    if args.dry_run and args.push or not args.dry_run and not args.push:
        parser.error("use --dry-run, or --once/--watch with --push")
    try:
        config = load_config(args.config)
        if args.dry_run:
            print(json.dumps(health(pull(config), config), sort_keys=True))
            return 0
        state_path = Path(config["state_file"]).expanduser().resolve()
        with owner_lock(state_path):
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"schema_version": 1}
            owner = digest({"remote": config["remote"], "node_id": config["node_id"],
                            "models": {m: w["actor_id"] for m, w in config["models"].items()},
                            "observer": config.get("observer", {}).get("actor_id"),
                            "timezone": config["timezone"]})
            if state.get("owner", owner) != owner:
                raise CollectorError("collector-owner-changed-new-state-required")
            state["owner"] = owner
            while True:
                try:
                    # Interval/bindings can be changed without restarting daemon.
                    config = load_config(args.config)
                    if digest({"remote": config["remote"], "node_id": config["node_id"],
                               "models": {m: w["actor_id"] for m, w in config["models"].items()},
                               "observer": config.get("observer", {}).get("actor_id"),
                               "timezone": config["timezone"]}) != owner:
                        raise CollectorError("collector-owner-change-refused")
                    snapshot = pull(config)
                    summary = health(snapshot, config)
                    state["observations"] = snapshot["observations"]
                    last_reported_at = state.get("health", {}).get("last_reported_at")
                    state["health"] = {**summary, "status": "observed-pending-report", "last_reported_at": last_reported_at}
                    write_state(state_path, state)
                    result = report_once(snapshot, config, state, lambda: write_state(state_path, state))
                    state["health"] = {**summary, "report": result, "status": "observed",
                                       "last_reported_at": datetime.now(timezone.utc).isoformat()}
                    write_state(state_path, state)
                    print(json.dumps(state["health"], sort_keys=True), flush=True)
                except CollectorError as exc:
                    state.setdefault("health", {})["status"] = str(exc)
                    write_state(state_path, state)
                    print(json.dumps({"status": str(exc)}), flush=True)
                    if args.once:
                        return 1
                if args.once:
                    return 0
                time.sleep(config["interval_seconds"])
    except (CollectorError, OSError, ValueError, KeyError):
        print(json.dumps({"status": "collector-failed-check-private-config-and-state"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
