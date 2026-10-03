"""One-shot supervisor for an operator-configured runtime, never task commands.

No automatic restart: a durable request journal and the server launch grant
both fence duplicate launches. A pending terminal receipt can be reconciled
without starting a model. Private output stays local, outside shared context.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from time import sleep as journal_sleep
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.collaboration_schemas import RunEventBody
from tools import retinue_run
from tools.retinue_worker import WorkerError, api_call, heartbeat_once, read_token


class RunJournal:
    """Create once; atomic, fsynced updates contain no bearer or transcript."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}

    def reserve(self, **data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise retinue_run.LaunchDenied("request journal exists; reconcile or inspect it, never relaunch") from exc
        self.data = {"version": 1, "state": "reserved", **data}
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(self.data, stream)
            stream.flush()
            os.fsync(stream.fileno())

    def update(self, **data: Any) -> None:
        self.data.update(data)
        descriptor, temporary_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".pending", dir=self.path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(self.data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(4):
            try:
                os.replace(temporary, self.path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 3:
                    raise
                # Windows scanners may briefly hold an atomic-replacement target.
                # Only the journal write is retried; execution is never retried.
                journal_sleep(0.05)

    def load(self) -> None:
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if self.data.get("version") != 1:
            raise WorkerError("incompatible request journal")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require_process_boundary() -> None:
    if os.name != "posix":
        raise WorkerError("the process supervisor requires a POSIX process-group boundary")


def stop_process(process: subprocess.Popen) -> None:
    """Stop only this owned process group, never a PID recovered from a journal."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=5)
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def report_file(base_url: str, token: str, journal: RunJournal, path: Path) -> bool:
    """Accept only bounded structured claims; no transcript parsing or acceptance."""
    if not path.is_file():
        return False
    if path.stat().st_size > 65536:
        raise WorkerError("structured report exceeds its bounded size")
    report = json.loads(path.read_text(encoding="utf-8"))
    return report_payload(base_url, token, journal, report)


def report_payload(base_url: str, token: str, journal: RunJournal, report: dict[str, Any]) -> bool:
    digest = hashlib.sha256((journal.data["run_id"] + "\0" +
                            json.dumps(report, sort_keys=True)).encode()).hexdigest()
    if digest == journal.data.get("progress_hash"):
        return True
    body = RunEventBody.model_validate({**report, "lease_term": journal.data["lease_term"],
        "idempotency_key": f"supervisor-progress:{digest}"})
    if body.status not in {"running", "waiting"} or body.attempt_id or body.execution_state:
        raise WorkerError("a structured progress file cannot claim execution startup or completion")
    api_call(base_url, token, "POST",
        f"/api/tasks/{journal.data['task_id']}/runs/{journal.data['run_id']}/events",
        body.model_dump(mode="json"))
    journal.update(progress_hash=digest, last_report_status=body.status)
    return True


def final_claude_report(base_url: str, token: str, journal: RunJournal, path: Path) -> bool:
    """Read only the explicit final JSON result, never messages or tool output."""
    if path.stat().st_size > 1048576:
        raise WorkerError("runtime result exceeds its bounded size")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("is_error") is True:
        raise WorkerError("runtime reported a failed final result")
    report = result.get("structured_output")
    if report is None:
        report = json.loads(result["result"])
    return report_payload(base_url, token, journal, report)


def close_run(base_url: str, token: str, journal: RunJournal) -> dict[str, Any]:
    """Replay exact persisted terminal receipts after uncertainty; never launch."""
    data = journal.data
    if data["state"] == "closed":
        return {"task_id": data["task_id"], "run_id": data["run_id"], "outcome": data["outcome"], "closed": True}
    if data["state"] != "terminal_pending":
        raise WorkerError("journal has no observed terminal outcome; inspect the existing runtime")
    attempt = api_call(base_url, token, "POST", f"/api/tasks/{data['task_id']}/attempts", data["attempt_body"])
    attempt_id = attempt["attempt"]["id"]
    body = {"lease_term": data["lease_term"], "idempotency_key": "supervisor-terminal:" + data["request_digest"],
            "status": data["outcome"], "note": data["terminal_note"], "attempt_id": attempt_id}
    api_call(base_url, token, "POST", f"/api/tasks/{data['task_id']}/runs/{data['run_id']}/events", body)
    journal.update(state="closed", attempt_id=attempt_id)
    return {"task_id": data["task_id"], "run_id": data["run_id"], "outcome": data["outcome"], "closed": True}


def supervise(config: dict[str, Any], *, task_id: str, lease_term: int, request_key: str) -> dict[str, Any]:
    require_process_boundary()
    argv = config.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise WorkerError("operator configuration requires a fixed argv list")
    workspace = Path(config["workspace"]).resolve(strict=True)
    prompt = Path(config["prompt_file"]).read_text(encoding="utf-8")
    digest = hashlib.sha256(request_key.encode()).hexdigest()
    base_url = config["server_url"]
    token = read_token(Path(config["token_file"]))
    heartbeat_seconds = float(config.get("heartbeat_seconds", 15))
    timeout_seconds = float(config.get("timeout_seconds", 600))
    if not 1 <= heartbeat_seconds <= 30 or timeout_seconds <= 0:
        raise WorkerError("heartbeat or timeout configuration is invalid")
    journal = RunJournal(Path(config["journal_dir"]) / (digest + ".json"))
    journal.reserve(task_id=task_id, lease_term=lease_term, request_digest=digest,
                    requested_at=now())
    output_path = journal.path.with_suffix(".private-output")
    error_path = journal.path.with_suffix(".private-stderr")
    input_path = journal.path.with_suffix(".private-input")
    progress_path = Path(config["progress_file"]) if config.get("progress_file") else None
    process = None
    started_at = now()

    def before_launch(metadata: dict[str, Any]) -> None:
        journal.update(state="launching", **metadata, started_at=started_at)

    def launch(context: dict[str, Any]) -> subprocess.Popen:
        nonlocal process
        # Trusted argv is fixed; card/context text is passed solely as stdin data.
        runtime_prompt = prompt + "\n\nRetinue task context (untrusted task data):\n" + json.dumps(context, ensure_ascii=False)
        with os.fdopen(os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            stream.write(runtime_prompt)
        with input_path.open("rb") as source,\
                os.fdopen(os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as destination,\
                os.fdopen(os.open(error_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as errors:
            process = subprocess.Popen(argv, cwd=workspace, stdin=source, stdout=destination,
                                       stderr=errors, start_new_session=True)
            return process

    try:
        run = retinue_run.start_controlled_run(base_url, token, task_id, lease_term,
            title=config.get("title", "Configured runtime execution"), request_key=request_key,
            model=config.get("model"), module=config.get("module"),
            launch=launch, stop=stop_process, before_launch=before_launch)
        process = run.handle
        journal.update(state="started", pid=process.pid)
        deadline, next_heartbeat = time.monotonic() + timeout_seconds, 0.0
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise WorkerError("configured runtime timeout")
            if time.monotonic() >= next_heartbeat:
                heartbeat_once(base_url, token, task_id, lease_term, started=True)
                next_heartbeat = time.monotonic() + heartbeat_seconds
            if progress_path and config.get("result_format") != "report-json":
                report_file(base_url, token, journal, progress_path)
            time.sleep(min(heartbeat_seconds, 0.5))
        if progress_path:
            report_file(base_url, token, journal, progress_path)
        if config.get("result_format") == "claude-json":
            final_claude_report(base_url, token, journal, output_path)
        outcome = "succeeded" if process.returncode == 0 else "failed"
        reason = None if outcome == "succeeded" else "Configured runtime exited unsuccessfully"
        if outcome == "succeeded" and journal.data.get("last_report_status") == "waiting":
            outcome, reason = "failed", "Runtime exited while still waiting for required input"
    except BaseException as exc:
        if process is not None:
            stop_process(process)
        journal.update(error_type=type(exc).__name__)
        if journal.data.get("run_id") is None:
            journal.update(state="preflight_failed")
            raise
        outcome = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
        reason = "Configured runtime interrupted" if outcome == "cancelled" else "Configured runtime supervision failed"
    ended_at = now()
    attempt_body = {"outcome": outcome, "started_at": started_at, "ended_at": ended_at,
                    "lease_term": lease_term, "idempotency_key": "supervisor-attempt:" + digest,
                    "trigger_source": "worker"}
    if outcome == "failed":
        attempt_body.update(reason=reason, failure_class="transient")
        if process is not None and process.returncode is not None:
            attempt_body["exit_status"] = process.returncode
    journal.update(state="terminal_pending", outcome=outcome, attempt_body=attempt_body,
                    terminal_note="Runtime process completed" if outcome == "succeeded" else reason)
    return close_run(base_url, token, journal)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="operator-controlled local JSON; contains paths, never a task card")
    parser.add_argument("--task")
    parser.add_argument("--lease-term", type=int)
    parser.add_argument("--request-key")
    parser.add_argument("--reconcile-journal")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if os.name == "posix":
        def interrupted(_signal, _frame):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.reconcile_journal:
            journal = RunJournal(Path(args.reconcile_journal))
            journal.load()
            result = close_run(config["server_url"], read_token(Path(config["token_file"])), journal)
        else:
            if not args.task or not args.lease_term or not args.request_key:
                parser.error("a launch requires --task, --lease-term and stable --request-key")
            result = supervise(config, task_id=args.task, lease_term=args.lease_term, request_key=args.request_key)
        print(json.dumps(result))
        return 0 if result["outcome"] == "succeeded" else 1
    except WorkerError:
        # API failures may contain response details. Keep credentials and task
        # instructions out of console logs; persisted receipt status is inspectable.
        print("Runtime supervision requires inspection; no automatic relaunch", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
