"""Run the collaboration API lifecycle in an isolated synthetic local database."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, User, make_session_factory
from server.engine import create_task
from server.security import hash_password, hash_token


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="retinue-collaboration-demo-") as directory:
        factory = make_session_factory(Path(directory) / "demo.db")
        with factory() as db:
            for actor_id, kind in [("agent-a", "agent"), ("agent-b", "agent"), ("reviewer", "human")]:
                db.add(Actor(id=actor_id, kind=kind, display_name=actor_id))
            db.flush()
            db.add(User(username="demo-operator", password_hash=hash_password("synthetic-demo-pass"), role="admin", actor_id="reviewer"))
            for actor_id in ("agent-a", "agent-b"):
                db.add(ApiToken(token_hash=hash_token(f"synthetic-demo-{actor_id}"), actor_id=actor_id, label="local synthetic demo"))
            root = create_task(db, title="Synthetic source review", created_by="agent-a", holder="agent-a")
            root_id = root.id
            db.commit()
        client = TestClient(create_app(factory))
        def post(path, body, actor=None):
            request_headers = {"Authorization": f"Bearer synthetic-demo-{actor}"} if actor else {}
            result = client.post(path, json=body, headers=request_headers)
            result.raise_for_status()
            return result.json()
        post("/api/auth/login", {"username": "demo-operator", "password": "synthetic-demo-pass"})
        post(f"/api/tasks/{root_id}/collaboration/policy", {"allowed_actor_ids": ["agent-a", "agent-b"], "idempotency_key": "demo-policy-001"})
        delegation = post(f"/api/tasks/{root_id}/delegations", {
            "delegated_to": "agent-b", "title": "Check sources", "instruction": "Review the source selection",
            "acceptance": ["All three sources checked"], "idempotency_key": "demo-delegate-001",
        }, "agent-a")["delegation"]
        child_id = delegation["child_task_id"]
        post(f"/api/tasks/{child_id}/update", {"status": "doing", "note": "Synthetic execution begins"}, "agent-b")
        run = post(f"/api/tasks/{child_id}/runs", {"title": "Synthetic source checks", "idempotency_key": "demo-run-001"}, "agent-b")["run"]
        endpoint = f"/api/tasks/{child_id}/runs/{run['id']}/events"
        post(endpoint, {"status": "waiting", "note": "Source selection needs review", "waiting": {"kind": "review", "owner": "reviewer", "reason": "Confirm the source list"}, "idempotency_key": "demo-wait-001", "lease_term": run["lease_term"]}, "agent-b")
        post(endpoint, {"status": "running", "note": "Source list confirmed", "progress": {"completed": 3, "total": 3, "unit": "checks"}, "idempotency_key": "demo-progress-001", "lease_term": run["lease_term"]}, "agent-b")
        attempt = post(f"/api/tasks/{child_id}/attempts", {"outcome": "succeeded", "started_at": run["started_at"], "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(), "lease_term": run["lease_term"], "idempotency_key": "demo-attempt-001"}, "agent-b")["attempt"]
        post(endpoint, {"status": "succeeded", "note": "Synthetic checks complete", "attempt_id": attempt["id"], "refs": ["artifact:source-list"], "lease_term": run["lease_term"], "idempotency_key": "demo-complete-001"}, "agent-b")
        result = client.get(f"/api/tasks/{root_id}/collaboration")
        result.raise_for_status()
        projection = result.json()
        print(json.dumps({"synthetic": True, "root_task_id": root_id, "delegations": projection["delegations"], "runs": projection["runs"], "coverage": projection["coverage"]}, ensure_ascii=False, indent=2))
        client.close()
        factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
