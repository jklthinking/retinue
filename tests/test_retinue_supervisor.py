"""Supervisor launch fencing, exact process ownership and terminal reconciliation."""
import json
from pathlib import Path

import pytest

from tools import retinue_supervisor as supervisor, retinue_run, retinue_worker
from tools.retinue_worker import WorkerError
from test_collaboration import environment, headers
from test_controlled_run import adapter


def setup(environment, monkeypatch, tmp_path, *, fail_terminal_once=False):
    client, factory, task_id = adapter(environment, monkeypatch)
    calls, launched, stopped = [], [], []
    failed = False

    def api(base, token, method, path, body=None):
        nonlocal failed
        calls.append((method, path, body))
        response = client.request(method, path, headers=headers(), json=body)
        if response.status_code >= 400:
            raise WorkerError(f"HTTP {response.status_code}")
        if fail_terminal_once and not failed and body and body.get("status") == "succeeded":
            failed = True
            raise WorkerError("terminal response lost after commit")
        return response.json()

    monkeypatch.setattr(retinue_run, "api_call", api)
    monkeypatch.setattr(supervisor, "api_call", api)
    monkeypatch.setattr(retinue_worker, "api_call", api)
    monkeypatch.setattr(supervisor, "require_process_boundary", lambda: None)
    monkeypatch.setattr(supervisor.time, "sleep", lambda delay: None)
    token = tmp_path / "actor.token"
    token.write_text("synthetic-agent-a-bearer")
    prompt = tmp_path / "operator-prompt.txt"
    prompt.write_text("Read the task and return bounded structured findings.")
    config = {"server_url": "unused", "token_file": str(token), "workspace": str(tmp_path),
              "prompt_file": str(prompt), "journal_dir": str(tmp_path / "journals"),
              "argv": ["configured-runtime", "--print"], "result_format": "claude-json",
              "module": "Reader", "title": "Configured inspection"}

    class Process:
        pid = 4321
        returncode = None
        reads = 0

        def poll(self):
            self.reads += 1
            if self.reads > 3:
                self.returncode = 0
            return self.returncode

    def popen(argv, **options):
        launched.append((argv, options["stdin"].read()))
        assert options["start_new_session"] is True
        options["stdout"].write(json.dumps({"is_error": False, "result": json.dumps({
            "status": "running", "note": "Inspection findings recorded",
            "progress_report": {"completed": [{"summary": "Reader inspected", "refs": ["commit:example-v1"]}],
                                "remaining": ["Independent review"], "next_owner": "agent-b"}})}).encode())
        return Process()

    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    monkeypatch.setattr(supervisor, "stop_process", lambda process: stopped.append(process.pid))
    return client, config, task_id, calls, launched, stopped


def test_supervisor_heartbeats_reports_module_and_never_relaunches(environment, monkeypatch, tmp_path):
    client, config, task_id, calls, launched, stopped = setup(environment, monkeypatch, tmp_path)
    result = supervisor.supervise(config, task_id=task_id, lease_term=1, request_key="supervisor-check-001")
    assert result["closed"] is True and result["outcome"] == "succeeded"
    assert len(launched) == 1 and stopped == []
    assert b"Retinue task context" in launched[0][1]
    assert any(path.endswith("/heartbeat") for _, path, _ in calls)
    view = client.get(f"/api/tasks/{task_id}/collaboration", headers=headers()).json()
    assert view["runs"][0]["status"] == "succeeded"
    assert view["runs"][0]["module"] == "Reader"
    assert view["runs"][0]["progress_report"]["completed"][0]["summary"] == "Reader inspected"
    assert view["tasks"][0]["status"] == "doing"  # process success is not acceptance
    with pytest.raises(retinue_run.LaunchDenied):
        supervisor.supervise(config, task_id=task_id, lease_term=1, request_key="supervisor-check-001")
    assert len(launched) == 1
    journal_data = json.loads(next(Path(config["journal_dir"]).glob("*.json")).read_text())
    assert "token" not in journal_data and "synthetic-agent-a-bearer" not in json.dumps(journal_data)
    retry = client.post(f"/api/tasks/{task_id}/retry", headers=headers(), json={"note": "New independent inspection"})
    assert retry.status_code == 200, retry.text
    term = retry.json()["lease"]["term"]
    second = supervisor.supervise(config, task_id=task_id, lease_term=term, request_key="supervisor-check-004")
    assert second["closed"] and second["run_id"] != result["run_id"]
    assert len(launched) == 2  # identical progress on a new run has a different key


def test_lost_terminal_receipt_reconciles_exact_attempt_without_restart(environment, monkeypatch, tmp_path):
    client, config, task_id, _, launched, _ = setup(environment, monkeypatch, tmp_path, fail_terminal_once=True)
    with pytest.raises(WorkerError):
        supervisor.supervise(config, task_id=task_id, lease_term=1, request_key="supervisor-check-002")
    journal = supervisor.RunJournal(next(Path(config["journal_dir"]).glob("*.json")))
    journal.load()
    assert journal.data["state"] == "terminal_pending"
    result = supervisor.close_run("unused", "synthetic", journal)
    assert result["closed"] is True and len(launched) == 1
    view = client.get(f"/api/tasks/{task_id}/collaboration", headers=headers()).json()
    assert len(view["attempts"]) == 1 and view["runs"][0]["status"] == "succeeded"


def test_heartbeat_rejection_stops_owned_process_and_records_failure(environment, monkeypatch, tmp_path):
    client, config, task_id, _, launched, stopped = setup(environment, monkeypatch, tmp_path)
    def rejected(*args, **kwargs):
        raise WorkerError("fenced heartbeat")
    monkeypatch.setattr(supervisor, "heartbeat_once", rejected)
    result = supervisor.supervise(config, task_id=task_id, lease_term=1, request_key="supervisor-check-003")
    assert result["outcome"] == "failed" and stopped == [4321] and len(launched) == 1
    view = client.get(f"/api/tasks/{task_id}/collaboration", headers=headers()).json()
    assert view["runs"][0]["status"] == "failed"


def test_progress_file_cannot_fabricate_terminal_outcome_or_startup(environment, monkeypatch, tmp_path):
    _, config, task_id, _, _, _ = setup(environment, monkeypatch, tmp_path)
    journal = supervisor.RunJournal(tmp_path / "report-journal.json")
    journal.reserve(task_id=task_id, run_id="run-" + "a" * 32, lease_term=1)
    with pytest.raises(WorkerError):
        supervisor.report_payload("unused", "synthetic", journal, {"status": "running", "execution_state": "started", "note": "Invented startup"})


def test_process_exit_race_still_reaps_owned_child(monkeypatch):
    reaped = []
    class Process:
        pid = 4321
        def poll(self):
            return None
        def wait(self, timeout):
            reaped.append(timeout)
            return 0
    def exited(*args):
        raise ProcessLookupError()
    monkeypatch.setattr(supervisor.os, "killpg", exited, raising=False)
    supervisor.stop_process(Process())
    assert reaped == [5]


def test_final_report_json_is_read_only_after_process_exit(environment, monkeypatch, tmp_path):
    client, config, task_id, _, _, _ = setup(environment, monkeypatch, tmp_path)
    result_file = tmp_path / "final-report.json"
    config.update(result_format="report-json", progress_file=str(result_file))
    final_report = {"status": "running", "note": "Final inspection recorded",
        "progress_report": {"completed": [{"summary": "Timeout reviewed", "module": "Timeout contract"}],
                            "remaining": ["Review acceptance"]}}
    class Process:
        pid, returncode, reads = 4321, None, 0
        def poll(self):
            self.reads += 1
            if self.reads > 3:
                result_file.write_text(json.dumps(final_report))
                self.returncode = 0
            return self.returncode
    def popen(argv, **options):
        # Simulate a runtime halfway through its final-message write. Parsing
        # this while the process is alive would abort otherwise healthy work.
        result_file.write_text('{"status":')
        return Process()
    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    result = supervisor.supervise(config, task_id=task_id, lease_term=1, request_key="supervisor-check-005")
    assert result["outcome"] == "succeeded"
    view = client.get(f"/api/tasks/{task_id}/collaboration", headers=headers()).json()
    assert view["runs"][0]["progress_report"]["completed"][0]["module"] == "Timeout contract"
