"""The well-known agent card: off by default, safe to publish, schema-shaped.

The card is the one unauthenticated discovery surface in the project, so these
tests assert against the serialised response body, not against intent: no
token, no absolute path, no non-loopback address, no node identifier, and no
actor roster material may appear in what a stranger receives.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, Skill, make_session_factory

CARD_PATH = "/.well-known/agent-card.json"

# Distinctive strings that must never appear in the served card.
NODE_ID = "topology-node-9"
ACTOR_SLUG = "agent-one"
DISABLED_SKILL = "drafting-disabled"


@pytest.fixture()
def client(tmp_path):
    factory = make_session_factory(tmp_path / "test.db")
    with factory() as db:
        db.add(
            Actor(
                id=ACTOR_SLUG,
                kind="agent",
                display_name="Agent One",
                runtime="test-runtime",
                model="test-model",
                node=NODE_ID,
            )
        )
        db.add(
            Skill(
                name="triage",
                description="Triage incoming cards.",
                category="ops",
                enabled=True,
                owners_json=f'["{ACTOR_SLUG}"]',
            )
        )
        db.add(
            Skill(
                name=DISABLED_SKILL,
                description="Not ready for publication.",
                category="writing",
                enabled=False,
                owners_json=f'["{ACTOR_SLUG}"]',
            )
        )
        db.commit()
    app = create_app(factory, data_dir=tmp_path)
    return TestClient(app)


def _enabled(client, monkeypatch):
    monkeypatch.setenv("RETINUE_AGENT_CARD", "1")
    return client.get(CARD_PATH)


def test_card_absent_by_default(client, monkeypatch):
    monkeypatch.delenv("RETINUE_AGENT_CARD", raising=False)
    response = client.get(CARD_PATH)
    assert response.status_code == 404


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_card_present_once_enabled(client, monkeypatch, value):
    monkeypatch.setenv("RETINUE_AGENT_CARD", value)
    response = client.get(CARD_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_card_contains_no_sensitive_material(client, monkeypatch):
    body = _enabled(client, monkeypatch).text

    # No node identifier and no actor roster material (slugs appear only as
    # skill owners in the registry; owners are not published).
    assert NODE_ID not in body
    assert ACTOR_SLUG not in body
    # Disabled skills are not published.
    assert DISABLED_SKILL not in body
    # No absolute machine path.
    assert not re.search(r"/(?:root|home|Users)/", body)
    # No non-loopback address: every IPv4 literal in the document is loopback.
    for address in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", body):
        assert address.startswith("127."), address
    # No token or credential shapes (mirrors scripts/check.sh patterns).
    assert not re.search(r"\b(?:sk|ghp|AKIA)[A-Za-z0-9_-]{8,}", body)
    assert not re.search(r"\b(?:rtn|rts|rtd)_[A-Za-z0-9_-]{8,}", body)
    # No URL at all: the card advertises no endpoint.
    assert "http" not in body.lower()


def test_card_matches_agent_card_schema_shape(client, monkeypatch):
    """Assert the A2A v1.0 AgentCard field names and types explicitly.

    The official JSON schema is not vendored because this work order forbids
    new dependencies, so the required fields of the shape we populate are
    asserted by hand: ``name``, ``description``, ``version``,
    ``capabilities``, ``defaultInputModes``, ``defaultOutputModes``, and
    ``skills`` with ``id``/``name``/``description``/``tags`` per entry. The
    pre-1.0 top-level ``url``/``protocolVersion`` and the 1.0
    ``supportedInterfaces`` are deliberately absent: this deployment serves
    no A2A task endpoint, and a card must not promise one.
    """
    card = _enabled(client, monkeypatch).json()

    assert set(card) == {
        "name",
        "description",
        "version",
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "skills",
    }
    assert isinstance(card["name"], str) and card["name"]
    assert isinstance(card["description"], str) and card["description"]
    assert isinstance(card["version"], str) and card["version"]
    assert isinstance(card["defaultInputModes"], list)
    assert all(isinstance(mode, str) for mode in card["defaultInputModes"])
    assert isinstance(card["defaultOutputModes"], list)
    assert all(isinstance(mode, str) for mode in card["defaultOutputModes"])

    capabilities = card["capabilities"]
    assert capabilities == {
        "streaming": False,
        "pushNotifications": False,
        "extendedAgentCard": False,
    }

    assert isinstance(card["skills"], list)
    assert [skill["name"] for skill in card["skills"]] == ["triage"]
    for skill in card["skills"]:
        assert set(skill) == {"id", "name", "description", "tags"}
        assert isinstance(skill["id"], str) and skill["id"]
        assert isinstance(skill["name"], str) and skill["name"]
        assert isinstance(skill["description"], str)
        assert isinstance(skill["tags"], list)
        assert all(isinstance(tag, str) for tag in skill["tags"])

    # No endpoint is promised anywhere in the document.
    assert "url" not in card
    assert "supportedInterfaces" not in card
    assert "protocolVersion" not in card


def test_unauthenticated_fetch_and_no_route_widening(client, monkeypatch):
    # Enabled: a stranger can fetch the card...
    response = _enabled(client, monkeypatch)
    assert response.status_code == 200

    # ...but enabling it widens nothing else.
    assert client.get("/api/skills").status_code == 401
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/actors").status_code == 401
    assert client.post("/api/tasks", json={}).status_code == 401

    # The pre-existing public route is unchanged.
    assert client.get("/api/health").status_code == 200

    # Disabled again: the card disappears and the rest of the surface is as
    # it was.
    monkeypatch.delenv("RETINUE_AGENT_CARD", raising=False)
    assert client.get(CARD_PATH).status_code == 404
    assert client.get("/api/skills").status_code == 401
    assert client.get("/api/health").status_code == 200
