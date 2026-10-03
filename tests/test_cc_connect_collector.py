from datetime import datetime, timezone
import json

from adapters.collectors.cc_connect import collect_observation
from adapters.collectors.central import build_rows, report_once, reserve_session, load_config, owner_lock, write_state, ssh_argv, CollectorError
import pytest

CURRENT = "11111111-1111-4111-8111-111111111111"
PAST = "22222222-2222-4222-8222-222222222222"
UNRELATED = "33333333-3333-4333-8333-333333333333"
MODEL = "claude-example-current"
OLD_MODEL = "claude-example-previous"
NOW = datetime(2026, 1, 8, 0, tzinfo=timezone.utc)


def fixture_sources(tmp_path):
    native = tmp_path / "native"
    transcripts = tmp_path / "transcripts"
    native.mkdir()
    transcripts.mkdir()
    state = {"sessions": {"private-im-key": {"id": "private-native-name", "name": "DO NOT EXPORT TITLE",
        "agent_session_id": CURRENT, "past_agent_session_ids": [PAST],
        "agent_type": "claudecode", "history": [{"body": "DO NOT EXPORT BODY"}],
        "updated_at": "2026-01-08T00:00:00Z", "last_user_activity": "2026-01-07T23:55:00Z"}},
        "user_meta": {"private-user": {"name": "DO NOT EXPORT USER"}}}
    (native / "sessions.json").write_text(json.dumps(state), encoding="utf-8")
    return native, transcripts


def record(session=CURRENT, model=MODEL, message_id="message-a", tokens=5, stamp="2026-01-07T23:50:00Z"):
    return {"sessionId": session, "type": "assistant", "timestamp": stamp, "secret-shaped-extra": "PRIVATE EXTRA",
            "message": {"id": message_id, "model": model, "content": "DO NOT EXPORT MESSAGE",
                        "usage": {"input_tokens": tokens, "cache_creation_input_tokens": 2,
                                  "cache_read_input_tokens": 3, "output_tokens": 4, "private-extra": "PRIVATE USAGE"}}}


def write_records(path, records):
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")


def config():
    return {"models": {MODEL: {"actor_id": "device-claude", "token_file": "unused"}},
            "bindings": [], "url": "http://127.0.0.1:8000", "node_id": "example-device"}


def snapshot(tmp_path):
    native, transcripts = fixture_sources(tmp_path)
    write_records(transcripts / "first.jsonl", [record(), record(PAST, OLD_MODEL), record(UNRELATED, tokens=999)])
    synthetic = record(model="<synthetic>", tokens=0)
    synthetic["message"]["usage"] = {field: 0 for field in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    write_records(transcripts / "copy.jsonl", [record(tokens=7), synthetic])
    return collect_observation(sessions_source=native, transcripts_source=transcripts, now=NOW)


def test_exact_native_join_actual_model_and_cross_file_dedupe(tmp_path):
    result = snapshot(tmp_path)
    assert result["coverage"]["complete"]
    assert result["native_health"]["runtime_ids"] == 2
    assert result["native_health"]["current_runtime_ids"] == 1
    assert result["usage_cohorts"][MODEL]["last_7_days"]["total_tokens"] == 16
    assert result["usage_cohorts"][MODEL]["source"]["usage_records"] == 1
    assert result["usage_cohorts"][OLD_MODEL]["last_7_days"]["total_tokens"] == 14
    assert result["usage_cohorts"][MODEL]["today"]["total_tokens"] == 16  # UTC previous day, local current day
    assert len(result["observations"]) == 2
    current = next(item for item in result["observations"] if item["model"] == MODEL)
    assert current["message_count"] == 1
    assert current["current"]
    text = json.dumps(result)
    for private in ["DO NOT EXPORT", "PRIVATE", "private-im-key", "private-user", UNRELATED, "<synthetic>"]:
        assert private not in text


def test_explicit_binding_and_unmapped_model_never_mix_worker(tmp_path):
    result = snapshot(tmp_path)
    settings = config()
    sessions, metrics = build_rows(result, settings)
    assert len(sessions) == 1 and len(metrics) == 7
    assert sessions[0]["task_id"] is None
    assert sessions[0]["summary"] == "" and sessions[0]["messages"] == []
    assert sessions[0]["privacy"] == "metadata"
    assert sum(row["input_tokens"] + row["output_tokens"] for row in metrics) == 16
    settings["bindings"] = [{"native_id": PAST, "model": MODEL, "task_id": "task-20260108-001"}]
    assert build_rows(result, settings)[0][0]["task_id"] is None
    settings["bindings"][0]["native_id"] = CURRENT
    assert build_rows(result, settings)[0][0]["task_id"] == "task-20260108-001"


@pytest.mark.parametrize("problem", ["missing-history", "broken-state", "partial-jsonl", "no-usage"])
def test_unknown_coverage_does_not_overwrite_daily_totals_with_zero(tmp_path, problem):
    native, transcripts = fixture_sources(tmp_path)
    records = [record(), record(PAST, OLD_MODEL)]
    if problem == "missing-history":
        records = [record()]
    if problem == "broken-state":
        (native / "broken.json").write_text("{", encoding="utf-8")
    if problem == "no-usage":
        for item in records:
            item["message"].pop("usage")
    write_records(transcripts / "first.jsonl", records)
    if problem == "partial-jsonl":
        with (transcripts / "first.jsonl").open("a", encoding="utf-8") as stream:
            stream.write('{"type":')
    result = collect_observation(sessions_source=native, transcripts_source=transcripts, now=NOW)
    assert build_rows(result, config())[1] == []


def test_unknown_response_reuses_durable_cursor_and_changed_body_advances(tmp_path):
    result = snapshot(tmp_path)
    state = {}
    persisted = []
    sent = []

    def api(url, token, path, body=None):
        if path == "/api/auth/me":
            return {"kind": "agent", "actor_id": "device-claude"}
        if path == "/api/actors":
            return [{"id": "device-claude", "node": "example-device", "model": MODEL, "runtime": "claude-code"}]
        if path == "/api/sessions/sync":
            assert persisted  # persist before potentially accepted POST
            sent.append(body)
            if len(sent) == 1:
                raise CollectorError("unknown-response")
        return {}

    with pytest.raises(CollectorError):
        report_once(result, config(), state, lambda: persisted.append(json.loads(json.dumps(state))),
                    api=api, token_reader=lambda path: "test-only")
    assert report_once(result, config(), state, lambda: persisted.append(json.loads(json.dumps(state))),
                       api=api, token_reader=lambda path: "test-only") == {"sessions": 1, "metrics": 7}
    assert sent[0] == sent[1]
    sessions, _ = build_rows(result, config())
    assert reserve_session(state, {**sessions[0], "message_count": 2})["cursor"] == 2


def test_identity_mismatch_fails_before_any_write(tmp_path):
    calls = []

    def api(url, token, path, payload=None):
        calls.append(path)
        return {"kind": "agent", "actor_id": "another-device"}

    with pytest.raises(CollectorError, match="worker-mismatch"):
        report_once(snapshot(tmp_path), config(), {}, lambda: None, api=api, token_reader=lambda path: "test-only")
    assert calls == ["/api/auth/me"]


@pytest.mark.parametrize("problem", ["negative", "boolean", "missing-timestamp", "model-conflict", "missing-id"])
def test_inconsistent_usage_never_publishes_partial_absolute_totals(tmp_path, problem):
    native, transcripts = fixture_sources(tmp_path)
    bad = record(message_id="message-b")
    if problem == "negative":
        bad["message"]["usage"]["input_tokens"] = -1
    if problem == "boolean":
        bad["message"]["usage"]["output_tokens"] = True
    if problem == "missing-timestamp":
        bad.pop("timestamp")
    if problem == "model-conflict":
        bad["message"]["id"] = "message-a"
        bad["message"]["model"] = OLD_MODEL
    if problem == "missing-id":
        bad["message"].pop("id")
    write_records(transcripts / "first.jsonl", [record(), record(PAST, OLD_MODEL), bad])
    result = collect_observation(sessions_source=native, transcripts_source=transcripts, now=NOW)
    settings = config()
    settings["models"][OLD_MODEL] = {"actor_id": "device-old-model", "token_file": "unused"}
    assert result["coverage"]["complete"] is False
    assert build_rows(result, settings)[1] == []


def test_streaming_without_usage_followed_by_valid_delivery_is_complete(tmp_path):
    native, transcripts = fixture_sources(tmp_path)
    partial = record()
    partial["message"].pop("usage")
    write_records(transcripts / "first.jsonl", [partial, record(), record(PAST, OLD_MODEL)])
    result = collect_observation(sessions_source=native, transcripts_source=transcripts, now=NOW)
    assert result["coverage"]["complete"] is True
    assert result["usage_cohorts"][MODEL]["last_7_days"]["total_tokens"] == 14


def test_observer_handles_only_unmapped_history_without_duplicate_session_rows(tmp_path):
    settings = config()
    settings["observer"] = {"actor_id": "device-session-sync", "token_file": "unused"}
    sessions, metrics = build_rows(snapshot(tmp_path), settings)
    assert len(sessions) == 2
    assert len({row["external_id"] for row in sessions}) == 2
    current = next(row for row in sessions if MODEL in row["external_id"])
    old = next(row for row in sessions if OLD_MODEL in row["external_id"])
    assert current["actor_id"] == "device-claude"
    assert old["actor_id"] == "device-session-sync"
    assert "historical observation" in old["title"] and OLD_MODEL in old["title"]
    assert all(row["actor_id"] == "device-claude" for row in metrics)


@pytest.mark.parametrize("changed", ["node", "model", "runtime", "disabled"])
def test_registered_identity_is_checked_before_session_and_metrics_write(tmp_path, changed):
    registered = {"id": "device-claude", "node": "example-device", "model": MODEL, "runtime": "claude-code"}
    registered[changed] = True if changed == "disabled" else "other"
    calls = []

    def api(url, token, path, payload=None):
        calls.append(path)
        return {"kind": "agent", "actor_id": "device-claude"} if path == "/api/auth/me" else [registered]

    with pytest.raises(CollectorError, match="device-or-model"):
        report_once(snapshot(tmp_path), config(), {}, lambda: None, api=api, token_reader=lambda path: "test-only")
    assert calls == ["/api/auth/me", "/api/actors"]


def test_private_state_atomic_replace_and_single_owner(tmp_path):
    path = tmp_path / "private-state.json"
    write_state(path, {"schema_version": 1, "counter": 1})
    with owner_lock(path):
        with pytest.raises(CollectorError, match="another-collector"):
            with owner_lock(path):
                pass
        write_state(path, {"schema_version": 1, "counter": 2})
    assert json.loads(path.read_text(encoding="utf-8"))["counter"] == 2
    assert not path.with_suffix(".json.tmp").exists()


def test_config_changes_interval_without_inventing_credentials_and_rejects_partial_owner(tmp_path):
    settings = {**config(), "schema_version": 1, "interval_seconds": 5, "remote": {
        "host": "example-device", "workspace": "/opt/example-observer", "sessions_source": "/var/lib/example-native",
        "transcripts_source": "/var/lib/example-transcripts"}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    assert load_config(path)["interval_seconds"] == 5
    assert load_config(path)["timezone"] == "Asia/Shanghai"
    command = ssh_argv(load_config(path))
    assert "StrictHostKeyChecking=yes" in command
    assert not any("test-only" in part for part in command)
    settings["models"][OLD_MODEL] = {"actor_id": "device-claude", "token_file": "unused"}
    path.write_text(json.dumps(settings), encoding="utf-8")
    with pytest.raises(CollectorError, match="one-complete-model-owner"):
        load_config(path)


@pytest.mark.parametrize("problem", ["missing-model-positive", "invalid-model-positive", "missing-model-zero",
                                    "invalid-model-zero", "invalid-model-malformed", "synthetic-positive",
                                    "synthetic-missing-counter", "synthetic-missing-usage"])
def test_unattributed_usage_cannot_publish_partial_absolute_metrics(tmp_path, problem):
    native, transcripts = fixture_sources(tmp_path)
    unknown = record(message_id="message-unknown")
    unknown["message"]["model"] = "<synthetic>" if problem.startswith("synthetic") else "invalid model"
    if problem.startswith("missing-model"):
        unknown["message"].pop("model")
    if problem.endswith("zero") or problem == "synthetic-missing-counter":
        unknown["message"]["usage"] = {key: 0 for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    if problem == "invalid-model-malformed":
        unknown["message"]["usage"]["input_tokens"] = True
    if problem == "synthetic-missing-counter":
        unknown["message"]["usage"].pop("cache_read_input_tokens")
    if problem == "synthetic-missing-usage":
        unknown["message"].pop("usage")
    write_records(transcripts / "first.jsonl", [record(), record(PAST, OLD_MODEL), unknown])
    result = collect_observation(sessions_source=native, transcripts_source=transcripts, now=NOW)
    assert result["coverage"]["unattributed_records"] == 1
    assert result["coverage"]["unattributed_usage_records"] == 1
    assert result["coverage"]["zero_token_synthetic_records"] == 0
    assert result["coverage"]["complete"] is False
    assert build_rows(result, config())[1] == []


def test_explicit_zero_synthetic_status_is_not_a_worker_delivery_or_missing_usage(tmp_path):
    result = snapshot(tmp_path)
    assert result["coverage"]["unattributed_records"] == 1
    assert result["coverage"]["zero_token_synthetic_records"] == 1
    assert result["coverage"]["unattributed_usage_records"] == 0
    assert result["coverage"]["complete"] is True
    assert sum(row["input_tokens"] + row["output_tokens"] for row in build_rows(result, config())[1]) == 16
    assert all(row["model"] != "<synthetic>" for row in result["observations"])
