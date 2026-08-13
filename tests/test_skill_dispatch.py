"""Local cache and retry for the generic-worker skill briefing hook."""

from __future__ import annotations

import urllib.error

from tools.skill_dispatch import (
    DispatchError,
    bound_skill_names,
    briefing_prompt_block,
    fetch_skill_briefing,
    materialize_briefing,
    read_cached_briefing,
)


def _briefing() -> dict:
    return {
        "actor_id": "scribe",
        "display_name": "Scribe",
        "count": 1,
        "note": "Bindings take effect on subsequent runs.",
        "skills": [
            {
                "name": "review",
                "category": "coding",
                "description": "Review a change",
                "source": "local",
                "source_kind": "local",
                "risk_notice": None,
            }
        ],
    }


def test_fetch_writes_cache_and_retries_then_serves_stale(tmp_path):
    cache_dir = tmp_path / "cache"
    calls = {"n": 0}

    def succeed() -> dict:
        calls["n"] += 1
        return _briefing()

    first = fetch_skill_briefing(
        succeed, cache_dir=cache_dir, actor_id="scribe", retries=2
    )
    assert first["count"] == 1
    assert calls["n"] == 1
    cached = read_cached_briefing(cache_dir, "scribe")
    assert cached is not None
    assert bound_skill_names(cached) == {"review"}

    def flaky() -> dict:
        calls["n"] += 1
        raise TimeoutError("brief pause")

    sleeps: list[float] = []
    stale = fetch_skill_briefing(
        flaky,
        cache_dir=cache_dir,
        actor_id="scribe",
        retries=3,
        sleep=sleeps.append,
    )
    assert stale["from_cache"] is True
    assert bound_skill_names(stale) == {"review"}
    assert len(sleeps) == 2


def test_fetch_retries_transient_then_succeeds(tmp_path):
    cache_dir = tmp_path / "cache"
    calls = {"n": 0}

    def once() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("brief pause")
        return _briefing()

    sleeps: list[float] = []
    payload = fetch_skill_briefing(
        once,
        cache_dir=cache_dir,
        actor_id="scribe",
        retries=3,
        sleep=sleeps.append,
    )
    assert calls["n"] == 2
    assert sleeps == [0.05]
    assert payload.get("from_cache") is not True
    assert bound_skill_names(payload) == {"review"}


def test_fetch_does_not_retry_forbidden_and_raises_without_cache(tmp_path):
    cache_dir = tmp_path / "cache"

    def forbidden() -> dict:
        raise urllib.error.HTTPError(
            "http://example.invalid/briefing", 403, "no", hdrs=None, fp=None
        )

    sleeps: list[float] = []
    try:
        fetch_skill_briefing(
            forbidden,
            cache_dir=cache_dir,
            actor_id="scribe",
            retries=4,
            sleep=sleeps.append,
        )
    except DispatchError as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("expected DispatchError")
    assert sleeps == []


def test_materialize_and_prompt_include_risk(tmp_path):
    dest = tmp_path / "skills"
    briefing = _briefing()
    briefing["skills"][0]["risk_notice"] = (
        "This skill comes from an unreviewed source and is not sandboxed."
    )
    written = materialize_briefing(briefing, dest)
    assert written == ["review"]
    text = (dest / "review" / "SKILL.md").read_text(encoding="utf-8")
    assert "Review a change" in text
    assert "not sandboxed" in text
    block = briefing_prompt_block(briefing)
    assert "review (coding)" in block
    assert "risk:" in block
