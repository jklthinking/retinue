"""Daily report boundaries, missing coverage, and absolute-report semantics."""
from __future__ import annotations

import datetime as dt

import pytest

from server.db import Actor, TokenUsage
from server import push_usage
from server.routers import metrics
from test_server_api import agent_headers, client, login


NOW = dt.datetime(2026, 5, 2, 23, 30, tzinfo=dt.timezone.utc)


def report(client, *, date="2026-05-02", runtime="claude-code", input=10, output=3):
    return client.post("/api/metrics/ingest", json={
        "actor_id": "scribe", "date": date, "runtime": runtime,
        "input_tokens": input, "output_tokens": output,
    })


def summary(client, monkeypatch, **params):
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    params.setdefault("timezone", "UTC")
    return client.get("/api/metrics/summary", params=params)


def test_missing_report_is_unknown_not_measured_zero(client, monkeypatch):
    login(client)
    body = summary(client, monkeypatch, days=1).json()
    assert body["usage_available"] is False
    assert body["totals"] == {"input": None, "output": None}
    assert body["coverage"]["missing_actors"] == ["scribe"]
    row = body["actors"][0]
    assert row["input"] is row["output"] is row["stale"] is None
    assert row["records"] == 0
    assert report(client, input=0, output=0).status_code == 200
    measured = summary(client, monkeypatch, days=1).json()
    assert measured["totals"] == {"input": 0, "output": 0}
    assert measured["actors"][0]["usage_available"] is True


def test_upsert_is_absolute_and_same_bucket_collectors_replace_not_add(client, monkeypatch):
    login(client)
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    assert report(client, input=100).status_code == 200
    assert report(client, input=100).status_code == 200
    assert report(client, input=25).status_code == 200
    assert report(client, runtime="codex", input=7, output=2).status_code == 200
    body = summary(client, monkeypatch, days=1).json()
    row = body["actors"][0]
    assert (row["input"], row["output"], row["records"]) == (32, 5, 2)
    assert {r["runtime"]: r["input"] for r in row["runtimes"]} == {"claude-code": 25, "codex": 7}
    assert "different collectors do not add" in body["accounting"]
    assert all(r["cache_breakdown"] is None for r in row["runtimes"])


def test_report_receipt_refresh_does_not_claim_source_observation(client, monkeypatch):
    login(client)
    assert report(client).status_code == 200
    with client.app.state.session_factory() as db:
        row = db.query(TokenUsage).one()
        row.updated_at = NOW - dt.timedelta(days=3)
        db.commit()
    before = summary(client, monkeypatch, days=1).json()["actors"][0]
    assert before["stale"] is True
    assert report(client).status_code == 200
    after = summary(client, monkeypatch, days=1).json()["actors"][0]
    assert after["last_reported_at"] == NOW.isoformat()
    assert after["stale"] is False
    assert "observed_at" not in after


def test_calendar_window_excludes_future_and_does_not_rebucket_legacy(client, monkeypatch):
    login(client)
    for date, count in (("2026-05-01", 1), ("2026-05-02", 2), ("2026-05-03", 3), ("2026-06-01", 1000)):
        assert report(client, date=date, input=count).status_code == 200
    utc = summary(client, monkeypatch, days=2).json()
    assert (utc["start"], utc["end"], utc["totals"]["input"]) == ("2026-05-01", "2026-05-02", 3)
    assert utc["diagnostics"]["excluded_future_rows"] == 2
    local = summary(client, monkeypatch, days=1, timezone="Asia/Shanghai").json()
    assert (local["start"], local["end"], local["totals"]["input"]) == ("2026-05-03", "2026-05-03", 3)
    assert local["bucket_timezone"] == "reporter-local-unrecorded"
    assert summary(client, monkeypatch, timezone="invalid-zone").status_code == 422


@pytest.mark.parametrize("date", ["2026-02-30", "2026-00-10", "2026-13-01"])
def test_invalid_calendar_report_is_rejected_without_a_row(client, date):
    login(client)
    assert report(client, date=date).status_code == 422
    with client.app.state.session_factory() as db:
        assert db.query(TokenUsage).count() == 0


def test_actor_bearer_cannot_overwrite_a_different_actor(client):
    headers = agent_headers(client)
    client.cookies.clear()
    body = {"actor_id": "owner", "date": "2026-05-02", "runtime": "codex", "input_tokens": 1, "output_tokens": 1}
    assert client.post("/api/metrics/ingest", headers=headers, json=body).status_code == 403


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
def test_empty_runtime_source_does_not_publish_false_zero_days(tmp_path, runtime):
    source = tmp_path / "empty-runtime"
    source.mkdir()
    snapshot = push_usage.collect(runtime, str(source), "worker-a")
    assert snapshot["source"]["files"] == 0
    assert push_usage.daily_rows(snapshot, "worker-a", runtime) == []


def test_processed_input_includes_claude_cache_but_not_codex_cache_twice():
    claude = {"source": {"files": 1}, "last_7_days": {"daily": [{
        "date": "2026-05-02", "input_tokens": 100, "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 7, "output_tokens": 5,
    }]}}
    codex = {"source": {"files": 1}, "last_7_days": {"daily": [{
        "date": "2026-05-02", "input_tokens": 100, "cached_input_tokens": 60,
        "output_tokens": 20, "reasoning_output_tokens": 8, "total_tokens": 120,
    }]}}
    assert push_usage.daily_rows(claude, "worker-a", "claude-code")[0]["input_tokens"] == 137
    row = push_usage.daily_rows(codex, "worker-a", "codex")[0]
    assert row["input_tokens"] + row["output_tokens"] == 120


def test_push_preserves_explicit_exporter_calendar(monkeypatch):
    calls = []
    monkeypatch.setattr(push_usage, "collect", lambda *args, **kwargs: calls.append(kwargs) or {"source": {"files": 0}})
    monkeypatch.setattr(push_usage, "push", lambda url, token, rows: len(rows))
    assert push_usage.push_usage(runtime="codex", source="local-records", actor_id="worker-a",
                                 url="http://127.0.0.1", token="test-placeholder", timezone_name="Asia/Shanghai") == 0
    assert calls == [{"timezone_name": "Asia/Shanghai"}]


def test_transport_service_is_not_worker_coverage_or_token_total(client, monkeypatch):
    login(client)
    assert report(client, input=100).status_code == 200
    with client.app.state.session_factory() as db:
        db.get(Actor, "scribe").model = "session-index-v1"
        db.commit()
    body = summary(client, monkeypatch, days=1).json()
    assert body["coverage"]["expected_actors"] == 0
    assert body["actors"] == []
    assert body["totals"]["input"] is None
    assert body["diagnostics"]["excluded_transport_rows"] == 1
    with client.app.state.session_factory() as db:
        assert db.query(TokenUsage).one().input_tokens == 100


def test_throughput_uses_event_offsets_and_excludes_future_or_unknown_timezones(client, monkeypatch):
    import server.engine as engine

    login(client)
    monkeypatch.setattr(metrics, "utc_now", lambda: NOW)
    ids = []
    for timestamp in ("2026-05-02T23:00:00Z", "2026-05-03T06:00:00+08:00",
                      "2026-05-02T15:59:00Z", "2026-05-03T09:00:00+08:00", "2026-05-02T22:00:00"):
        monkeypatch.setattr(engine, "_now_iso", lambda timestamp=timestamp: timestamp)
        created = client.post("/api/tasks", json={"title": "Calendar receipt fixture", "holder": "scribe"})
        assert created.status_code == 200
        ids.append(created.json()["id"])
    monkeypatch.setattr(engine, "_now_iso", lambda: "2026-05-02T23:10:00Z")
    for status in ("doing", "done"):
        assert client.post(f"/api/tasks/{ids[0]}/update", json={"status": status, "note": "Calendar fixture receipt"}).status_code == 200
    local = client.get("/api/metrics/throughput", params={"days": 1, "timezone": "Asia/Shanghai"}).json()
    assert (local["start"], local["end"]) == ("2026-05-03", "2026-05-03")
    assert local["days"] == [{"date": "2026-05-03", "done": 1, "receipts": 4}]
    assert local["done_by_actor"] == [{"actor_id": "owner", "done": 1}]
    assert local["diagnostics"]["timezone_unknown_timestamps"] == 1
    assert local["diagnostics"]["future_events_in_candidate_window"] == 1
    utc = client.get("/api/metrics/throughput", params={"days": 1, "timezone": "UTC"}).json()
    assert utc["days"] == [{"date": "2026-05-02", "done": 1, "receipts": 5}]
