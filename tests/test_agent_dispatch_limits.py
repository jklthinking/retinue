"""Creating cards with an agent credential cannot reach past the agent itself.

An agent bearer token is one actor's identity. Before this, any agent could
create a card and assign it to any executor on the roster, which made every
stolen or leaked token a dispatcher. Free-form creation is now self-or-hall
for agents; naming other executors stays with member and admin accounts, and
with admin-curated dispatch templates.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, User, make_session_factory
from server.security import hash_password, hash_token

AGENT_BEARER = "self-only-agent-bearer"


@pytest.fixture()
def board(tmp_path):
    factory = make_session_factory(tmp_path / "board.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="self-agent", kind="agent", display_name="Self Agent"),
                Actor(id="other-agent", kind="agent", display_name="Other Agent"),
                Actor(id="dispatcher", kind="human", display_name="Dispatcher"),
            ]
        )
        db.flush()
        db.add(
            User(
                username="dispatcher",
                password_hash=hash_password("dispatcher-pass-1"),
                role="member",
                actor_id="dispatcher",
            )
        )
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="self-agent",
                label="dispatch probe",
            )
        )
        db.commit()
    return TestClient(create_app(factory))


def agent_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_BEARER}"}


def test_agent_keeps_or_hangs_work_but_cannot_assign_it_away(board):
    kept = board.post(
        "/api/tasks",
        json={"title": "own card", "holder": "self-agent"},
        headers=agent_headers(),
    )
    assert kept.status_code == 200
    assert kept.json()["holder"] == "self-agent"

    hung = board.post(
        "/api/tasks",
        json={"title": "hall card", "open_dispatch": True},
        headers=agent_headers(),
    )
    assert hung.status_code == 200

    refused = board.post(
        "/api/tasks",
        json={"title": "cross card", "holder": "other-agent"},
        headers=agent_headers(),
    )
    assert refused.status_code == 403
    assert "other-agent" in refused.json()["detail"]


def test_agent_pipeline_stages_cannot_name_other_executors(board):
    refused = board.post(
        "/api/tasks",
        json={
            "title": "pipeline card",
            "pipeline": [
                {"name": "draft", "holder": "self-agent"},
                {"name": "review", "holder": "other-agent"},
            ],
        },
        headers=agent_headers(),
    )
    assert refused.status_code == 403

    allowed = board.post(
        "/api/tasks",
        json={
            "title": "own pipeline",
            "pipeline": [
                {"name": "draft", "holder": "self-agent"},
                {"name": "polish", "holder": "self-agent"},
            ],
        },
        headers=agent_headers(),
    )
    assert allowed.status_code == 200


def test_member_account_still_dispatches_to_anyone(board):
    login = board.post(
        "/api/auth/login",
        json={"username": "dispatcher", "password": "dispatcher-pass-1"},
    )
    assert login.status_code == 200

    assigned = board.post(
        "/api/tasks", json={"title": "member dispatch", "holder": "other-agent"}
    )
    assert assigned.status_code == 200
    assert assigned.json()["holder"] == "other-agent"
