import json

from sqlalchemy import select
import yaml

from adapters.im.feishu import (
    DispatchError,
    FeishuAdapter,
    MemoryTransport,
    normalize_event,
    parse_receipt,
)
from core.cli import main as cli
from core.protocol.task import ProtocolError, create_task, load_task, update_task
from server.db import Actor, ApiToken, PipelineTemplate, Task, make_session_factory
from server.dispatch import dispatch_intent
from server.engine import Conflict
from server.security import hash_token


def make_org(root):
    (root / "org.yaml").write_text(yaml.safe_dump({
        "org": "acme-inc",
        "departments": [{"id": "eng", "name": "Engineering"}],
        "agents": [
            {"id": "boss", "dept": "eng", "runtime": "human", "node": "node-1", "im": {"feishu": {"mention_id_env": "BOSS_MENTION"}}},
            {"id": "coder-1", "dept": "eng", "runtime": "local", "node": "node-1", "im": {"feishu": {"mention_id_env": "CODER_MENTION"}}},
        ],
        "nodes": [{"id": "node-1"}],
        "adapters": {"feishu": {"enabled": True, "chat_id_env": "CHAT_ID", "self_mention_id_env": "SELF_ID", "allow_from_env": "ALLOW_FROM"}},
    }, sort_keys=False), encoding="utf-8")


def configured(tmp_path):
    root = tmp_path / "fleet"
    (root / "tasks").mkdir(parents=True)
    make_org(root)
    transport = MemoryTransport()
    env = {"CHAT_ID": "chat-example", "SELF_ID": "self-example", "ALLOW_FROM": "sender-example", "BOSS_MENTION": "boss-example", "CODER_MENTION": "coder-example"}
    return root, transport, FeishuAdapter(root, transport=transport, environ=env)


class DatabaseDispatcher:
    def __init__(self, factory):
        self.factory = factory
        self.calls = []

    def dispatch(self, *, intent, idempotency_key, token):
        self.calls.append(
            {
                "intent": intent,
                "idempotency_key": idempotency_key,
                "token": token,
            }
        )
        with self.factory() as db:
            principal = db.execute(
                select(ApiToken).where(ApiToken.token_hash == hash_token(token))
            ).scalar()
            if principal is None or principal.disabled or principal.actor.disabled:
                raise DispatchError(401, "authentication required")
            try:
                outcome = dispatch_intent(
                    db,
                    actor_id=principal.actor_id,
                    intent=intent,
                    idempotency_key=idempotency_key,
                )
            except Conflict as exc:
                raise DispatchError(409, str(exc)) from exc
            except ProtocolError as exc:
                raise DispatchError(422, str(exc)) from exc
            result = {
                "id": outcome.task.id,
                "title": outcome.task.title,
                "created_by": outcome.task.created_by,
                "created": outcome.created,
            }
            db.commit()
            return result


def configured_publication(tmp_path):
    factory = make_session_factory(tmp_path / "dispatch.db")
    token = "adapter-test-token"
    with factory() as db:
        db.add_all(
            [
                Actor(id="publisher", kind="human", display_name="Publisher"),
                Actor(id="writer", kind="agent", display_name="Writer"),
                Actor(id="checker", kind="agent", display_name="Checker"),
                ApiToken(
                    token_hash=hash_token(token),
                    actor_id="publisher",
                    label="im-publication-test",
                ),
                PipelineTemplate(
                    name="Content workflow",
                    stages_json=json.dumps(
                        [
                            {"name": "Draft", "holder": "writer", "gate": "auto"},
                            {
                                "name": "Review",
                                "holder": "checker",
                                "gate": "review",
                            },
                        ]
                    ),
                    match_terms_json=json.dumps(["lesson", "handout"]),
                    acceptance_json=json.dumps(["review is recorded"]),
                ),
            ]
        )
        db.commit()
    root = tmp_path / "im"
    (root / "tasks").mkdir(parents=True)
    make_org(root)
    org = yaml.safe_load((root / "org.yaml").read_text(encoding="utf-8"))
    org["adapters"]["feishu"].update(
        {
            "dispatch_url_env": "SERVER_URL",
            "dispatch_senders_env": "DISPATCH_SENDERS",
        }
    )
    (root / "org.yaml").write_text(
        yaml.safe_dump(org, sort_keys=False), encoding="utf-8"
    )
    transport = MemoryTransport()
    dispatcher = DatabaseDispatcher(factory)
    env = {
        "CHAT_ID": "chat-example",
        "SELF_ID": "self-example",
        "ALLOW_FROM": "receipt-sender-example",
        "SERVER_URL": "http://127.0.0.1:9219",
        "DISPATCH_SENDERS": json.dumps(
            {"publisher-sender-example": "PUBLISHER_TOKEN"}
        ),
        "PUBLISHER_TOKEN": token,
    }
    adapter = FeishuAdapter(
        root, transport=transport, dispatcher=dispatcher, environ=env
    )
    return factory, transport, dispatcher, adapter


def published_cards(factory):
    with factory() as db:
        return list(db.execute(select(Task).order_by(Task.id)).scalars())


def publication_event(text, *, message_id="message-publication", sender="publisher-sender-example"):
    return {
        "sender_id": sender,
        "mentions": ["self-example"],
        "text": text,
        "message_id": message_id,
    }


def test_receipts_are_automatic_and_one_task_one_thread(tmp_path):
    root, transport, adapter = configured(tmp_path)
    task = create_task(root / "tasks", task_id="task-20260719-104", title="Build", created_by="boss", holder="coder-1")
    adapter.emit(task)
    update_task(task, status="doing", note="claimed")
    adapter.emit(task)
    assert transport.messages[0]["kind"] == "send"
    assert transport.messages[0]["mention"] == "coder-example"
    assert transport.messages[1]["kind"] == "reply"
    assert transport.messages[1]["root_id"] == transport.messages[0]["message_id"]
    assert adapter.emit(task) == "duplicate"
    assert len(transport.messages) == 2


def test_cli_mutation_calls_automatic_receipt_hook(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "emit_if_configured", lambda path: called.append(path))
    root = tmp_path / "tasks"
    assert cli.main(["task", "new", str(root), "--id", "task-20260719-109", "--title", "Auto emit", "--created-by", "boss", "--holder", "coder-1"]) == 0
    assert called == [root / "task-20260719-109.yaml"]


def test_done_mentions_creator_in_main_flow(tmp_path):
    root, transport, adapter = configured(tmp_path)
    task = create_task(root / "tasks", task_id="task-20260719-105", title="Build", created_by="boss", holder="coder-1")
    adapter.emit(task)
    update_task(task, status="doing", note="claimed")
    update_task(task, status="done", note="finished")
    adapter.emit(task)
    assert transport.messages[-1]["kind"] == "send"
    assert transport.messages[-1]["mention"] == "boss-example"


def test_inbound_receipt_validates_and_applies_once(tmp_path):
    root, _, adapter = configured(tmp_path)
    task = create_task(root / "tasks", task_id="task-20260719-106", title="Build", created_by="boss", holder="coder-1")
    text = "【任务回执】task-20260719-106 Build\n状态：queued → doing　持棒：coder-1 → coder-1　备注：claimed remotely"
    event = {"sender_id": "sender-example", "mentions": ["self-example"], "text": text, "message_id": "message-example"}
    assert adapter.receive(event) == "applied"
    assert load_task(task)["status"] == "doing"
    assert adapter.receive(event) == "duplicate"


def test_invalid_receipt_is_rejected_and_replied(tmp_path):
    root, transport, adapter = configured(tmp_path)
    create_task(root / "tasks", task_id="task-20260719-107", title="Build", created_by="boss", holder="coder-1")
    text = "【任务回执】task-20260719-107 Build\n状态：queued → done　持棒：coder-1 → coder-1　备注：skip"
    event = {"sender_id": "sender-example", "mentions": ["self-example"], "text": text, "message_id": "message-example"}
    assert adapter.receive(event) == "rejected"
    assert transport.messages[-1]["kind"] == "reply"
    assert "rejected" in transport.messages[-1]["text"]
    assert load_task(root / "tasks" / "task-20260719-107.yaml")["status"] == "queued"


def test_receipt_parser_rejects_non_protocol_text():
    try:
        parse_receipt("hello")
    except ProtocolError as exc:
        assert "receipt" in str(exc)
    else:
        raise AssertionError("invalid message accepted")


def test_raw_feishu_event_is_normalized():
    raw = {"event": {"sender": {"sender_id": {"open_id": "sender-example"}}, "message": {"message_id": "message-example", "content": '{"text":"hello"}', "mentions": [{"id": {"open_id": "self-example"}}]}}}
    assert normalize_event(raw) == {"sender_id": "sender-example", "mentions": ["self-example"], "text": "hello", "message_id": "message-example"}


def test_inbound_message_dispatch_is_idempotent_and_reports_existing_card(tmp_path):
    factory, transport, dispatcher, adapter = configured_publication(tmp_path)
    event = publication_event("Prepare a lesson handout")

    assert adapter.receive(event) == "published"
    assert adapter.receive(event) == "duplicate"

    cards = published_cards(factory)
    assert len(cards) == 1
    assert cards[0].created_by == "publisher"
    assert cards[0].holder == "writer"
    assert [call["idempotency_key"] for call in dispatcher.calls] == [
        "message-publication",
        "message-publication",
    ]
    assert transport.messages[-1]["root_id"] == "message-publication"
    assert "Message already published as publisher" in transport.messages[-1]["text"]
    assert cards[0].id in transport.messages[-1]["text"]


def test_unmatched_inbound_message_replies_with_next_action_and_creates_nothing(tmp_path):
    factory, transport, _, adapter = configured_publication(tmp_path)

    assert adapter.receive(publication_event("Perform an unrelated operation")) == "unmatched"

    assert published_cards(factory) == []
    reply = transport.messages[-1]["text"]
    assert "no configured pipeline matched" in reply
    assert "pipeline name or match term" in reply
    assert "ask an operator" in reply


def test_unknown_inbound_sender_is_fail_closed(tmp_path):
    factory, transport, dispatcher, adapter = configured_publication(tmp_path)

    assert adapter.receive(
        publication_event("Prepare a lesson", sender="unknown-sender-example")
    ) == "unauthorized"

    assert dispatcher.calls == []
    assert published_cards(factory) == []
    assert "not authorized" in transport.messages[-1]["text"]


def test_sender_with_invalid_actor_token_is_fail_closed(tmp_path):
    factory, transport, dispatcher, adapter = configured_publication(tmp_path)
    adapter.environ["PUBLISHER_TOKEN"] = "invalid-test-token"

    assert adapter.receive(publication_event("Prepare a lesson")) == "unauthorized"

    assert len(dispatcher.calls) == 1
    assert published_cards(factory) == []
    assert "not authorized by Retinue Server" in transport.messages[-1]["text"]


def test_inbound_message_is_only_intent_not_executable_configuration(
    tmp_path, monkeypatch
):
    factory, _, dispatcher, adapter = configured_publication(tmp_path)
    monkeypatch.setattr(
        "adapters.im.feishu.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("message triggered an executable")
        ),
    )
    monkeypatch.setattr(
        "adapters.im.feishu.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("message selected an executable")
        ),
    )
    hostile = (
        'Prepare a lesson; on_claim=["launch-tool"] '
        'command="launch-tool" path="./operator-only" holder="checker"'
    )

    assert adapter.receive(publication_event(hostile)) == "published"

    assert dispatcher.calls == [
        {
            "intent": hostile,
            "idempotency_key": "message-publication",
            "token": "adapter-test-token",
        }
    ]
    cards = published_cards(factory)
    assert len(cards) == 1
    assert cards[0].title == hostile
    assert cards[0].holder == "writer"
