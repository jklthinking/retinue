"""Observer discoveries become explicit roster proposal cards."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from core.panel import render_thread
from server.db import Actor, KnowledgeSource, Node, Skill, Task, TaskEvent, make_session_factory
from server.engine import create_task, fold_stored_task, task_to_dict
from server.helpers import receipt_text
from server.kingdom_import import (
    PROPOSAL_EVENT_TYPE,
    apply_kingdom_proposal,
    import_kingdom,
)


def _snapshot(directory) -> None:
    directory.mkdir()
    (directory / "observer-overview.json").write_text(
        json.dumps(
            {
                "node": {"id": "observer-node", "label": "Observer node"},
                "agents": [
                    {
                        "profile": "worker-one",
                        "display_name": "Worker One",
                        "provider": "runtime-a",
                        "model": "model-a",
                    }
                ],
                "tasks": {
                    "items": [
                        {
                            "id": "source-item-one",
                            "title": "Prepare synthetic report",
                            "assignee": "worker-one",
                            "status": "ready",
                            "priority": 2,
                        }
                    ]
                },
                "skills": [
                    {
                        "name": "Synthetic analysis",
                        "description": "Analyze neutral fixtures",
                        "category": "analysis",
                        "owned_by": ["worker-one"],
                    }
                ],
                "vault": {
                    "available": True,
                    "path_label": "Reference vault",
                    "active_notes": 3,
                    "active_size_bytes": 120,
                },
            }
        ),
        encoding="utf-8",
    )


def _factory(tmp_path):
    factory = make_session_factory(tmp_path / "proposal.db")
    with factory() as db:
        db.add(Actor(id="authority", kind="human", display_name="Authority"))
        db.commit()
    return factory


def test_import_only_publishes_a_human_readable_proposal(tmp_path):
    snapshots = tmp_path / "observer-input"
    _snapshot(snapshots)
    factory = _factory(tmp_path)

    with factory() as db:
        result = import_kingdom(
            db,
            snapshots,
            created_by="authority",
            performed_by="nightly-observer",
        )
        db.commit()

        assert result == {"proposals": 1, "proposed": 5, "skipped": 0}
        assert db.scalar(select(func.count()).select_from(Actor)) == 1
        assert db.scalar(select(func.count()).select_from(Node)) == 0
        assert db.scalar(select(func.count()).select_from(Skill)) == 0
        assert db.scalar(select(func.count()).select_from(KnowledgeSource)) == 0
        tasks = list(db.execute(select(Task)).scalars())
        assert len(tasks) == 1
        proposal = tasks[0]
        assert proposal.title == "Roster proposal: 5 additions"
        assert len(proposal.acceptance) == 5
        assert {item["kind"] for item in task_to_dict(proposal)["proposal"]["items"]} == {
            "actor",
            "task",
            "node",
            "skill",
            "knowledge_source",
        }
        assert proposal.events[0].event_type == PROPOSAL_EVENT_TYPE
        assert db.execute(
            select(TaskEvent).where(TaskEvent.event_type == PROPOSAL_EVENT_TYPE)
        ).scalar_one().event_key.startswith("proposal-")


def test_same_snapshot_is_proposed_once_and_applied_rows_are_not_reproposed(tmp_path):
    snapshots = tmp_path / "observer-input"
    _snapshot(snapshots)
    factory = _factory(tmp_path)

    with factory() as db:
        first = import_kingdom(db, snapshots, created_by="authority")
        second = import_kingdom(db, snapshots, created_by="authority")
        assert first["proposals"] == 1
        assert second == {"proposals": 0, "proposed": 0, "skipped": 5}
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        db.commit()

    with factory() as db:
        proposal = db.execute(
            select(Task).join(TaskEvent).where(TaskEvent.event_type == PROPOSAL_EVENT_TYPE)
        ).scalar_one()
        apply_kingdom_proposal(
            db,
            proposal,
            authorised_by="authority",
            performed_by="proposal-runner",
        )
        db.commit()

    with factory() as db:
        replay = import_kingdom(db, snapshots, created_by="authority")
        assert replay == {"proposals": 0, "proposed": 0, "skipped": 5}
        assert db.scalar(
            select(func.count()).select_from(TaskEvent).where(
                TaskEvent.event_type == PROPOSAL_EVENT_TYPE
            )
        ) == 1


def test_existing_imported_rows_are_grandfathered_without_rewrite_or_proposal(tmp_path):
    snapshots = tmp_path / "observer-input"
    _snapshot(snapshots)
    factory = _factory(tmp_path)

    with factory() as db:
        worker = Actor(
            id="worker-one",
            kind="agent",
            display_name="Preserved worker",
            runtime="preserved-runtime",
        )
        db.add(worker)
        db.add(
            Node(
                id="observer-node",
                label="Preserved node",
                admitted_by="authority",
            )
        )
        db.add(
            Skill(
                name="Synthetic analysis",
                description="Preserved skill",
                source="kingdom",
            )
        )
        db.add(
            KnowledgeSource(
                name="observer-node · Reference vault",
                kind="obsidian",
                notes="Preserved source",
            )
        )
        db.flush()
        create_task(
            db,
            title="Preserved imported task",
            created_by="authority",
            holder="worker-one",
            refs=["kingdom:source-item-one"],
        )
        db.commit()

    with factory() as db:
        result = import_kingdom(db, snapshots, created_by="authority")
        assert result == {"proposals": 0, "proposed": 0, "skipped": 5}
        assert db.get(Actor, "worker-one").display_name == "Preserved worker"
        assert db.get(Actor, "worker-one").runtime == "preserved-runtime"
        assert db.get(Node, "observer-node").label == "Preserved node"
        assert db.execute(
            select(Skill).where(Skill.name == "Synthetic analysis")
        ).scalar_one().description == "Preserved skill"
        assert db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.name == "observer-node · Reference vault"
            )
        ).scalar_one().notes == "Preserved source"
        assert db.scalar(
            select(func.count()).select_from(TaskEvent).where(
                TaskEvent.event_type == PROPOSAL_EVENT_TYPE
            )
        ) == 0


def test_approval_applies_entities_and_node_uses_explicit_admission(tmp_path, monkeypatch):
    snapshots = tmp_path / "observer-input"
    _snapshot(snapshots)
    factory = _factory(tmp_path)
    admission_calls: list[dict[str, str]] = []

    from server import kingdom_import

    real_admit = kingdom_import.admit_node

    def observed_admit(db, **kwargs):
        admission_calls.append(kwargs)
        return real_admit(db, **kwargs)

    monkeypatch.setattr(kingdom_import, "admit_node", observed_admit)

    with factory() as db:
        import_kingdom(db, snapshots, created_by="authority")
        proposal = db.execute(select(Task)).scalar_one()
        applied = apply_kingdom_proposal(
            db,
            proposal,
            authorised_by="authority",
            performed_by="proposal-runner",
        )
        assert applied == {"created": 5, "unchanged": 0}
        assert proposal.status == "done"
        assert db.get(Actor, "worker-one") is not None
        node = db.get(Node, "observer-node")
        assert node is not None
        assert node.membership_status == "admitted"
        assert node.admitted_by == "authority"
        assert node.admitted_at is not None
        assert admission_calls == [
            {
                "node_id": "observer-node",
                "label": "Observer node",
                "admitted_by": "authority",
            }
        ]
        assert db.execute(select(Skill).where(Skill.name == "Synthetic analysis")).scalar_one()
        assert db.execute(
            select(KnowledgeSource).where(KnowledgeSource.name.contains("Reference vault"))
        ).scalar_one()
        imported = db.execute(
            select(Task).where(Task.refs_json.contains("kingdom:source-item-one"))
        ).scalar_one()
        assert imported.holder == "worker-one"


def test_chain_fold_receipt_and_response_show_automation_acting_for_authority(tmp_path):
    snapshots = tmp_path / "observer-input"
    _snapshot(snapshots)
    snapshot_file = snapshots / "observer-overview.json"
    unsafe = json.loads(snapshot_file.read_text(encoding="utf-8"))
    unsafe["node"]["label"] = str(snapshots)
    unsafe["agents"][0]["display_name"] = str(snapshots)
    unsafe["tasks"]["items"][0]["title"] = str(snapshots)
    unsafe["vault"]["path_label"] = str(snapshots)
    snapshot_file.write_text(json.dumps(unsafe), encoding="utf-8")
    factory = _factory(tmp_path)

    with factory() as db:
        response = import_kingdom(
            db,
            snapshots,
            created_by="authority",
            performed_by="nightly-observer",
        )
        proposal = db.execute(select(Task)).scalar_one()
        folded = fold_stored_task(proposal)
        assert folded.attributions == (
            {
                "event": 1,
                "authorising_identity": "authority",
                "performing_agent": "nightly-observer",
            },
        )
        serialized = json.dumps(
            {"response": response, "card": task_to_dict(proposal)},
            ensure_ascii=False,
        )
        assert str(snapshots) not in serialized
        receipt = receipt_text(task_to_dict(proposal))
        assert "nightly-observer 代表 authority" in receipt
        assert str(snapshots) not in receipt
        panel = render_thread(task_to_dict(proposal)).decode()
        assert "nightly-observer 代表 authority" in panel
        assert str(snapshots) not in panel

        apply_kingdom_proposal(
            db,
            proposal,
            authorised_by="authority",
            performed_by="proposal-runner",
        )
        applied_fold = fold_stored_task(proposal)
        assert applied_fold.attributions[-1] == {
            "event": 3,
            "authorising_identity": "authority",
            "performing_agent": "proposal-runner",
        }
