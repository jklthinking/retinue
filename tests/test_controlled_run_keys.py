"""Boundary keys must remain valid after an adapter has already launched."""

from server.collaboration_schemas import RunEventBody
from tools import retinue_run


def test_maximum_request_key_produces_valid_bounded_started_receipt(monkeypatch):
    calls, launched, stopped = [], [], []
    def api(base_url, token, method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/context"):
            return {"version": 1, "task_id": "task-20260101-001"}
        if path.endswith("/runs"):
            return {"run": {"id": "run-" + "a" * 32}}
        if path.endswith("/authorize-execution"):
            return {"allow_execute": True}
        RunEventBody.model_validate(body)
        return {"created": True}
    monkeypatch.setattr(retinue_run, "api_call", api)
    result = retinue_run.start_controlled_run(
        "http://127.0.0.1:9219", "synthetic-bearer", "task-20260101-001", 1,
        title="Boundary check", request_key="q" * 128,
        launch=lambda context: launched.append(context) or "test-handle", stop=stopped.append)
    assert result.handle == "test-handle" and len(launched) == 1 and stopped == []
    key = calls[-1][2]["idempotency_key"]
    assert len(key) <= 128 and key.startswith("started:") and "q" * 128 not in key
