"""Feishu receipt transport and inbound protocol adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from typing import Iterable, Mapping, Protocol

import yaml

from core.protocol.org import validate_org
from core.protocol.task import ProtocolError, load_task, render_receipt, update_task
from server.http_client import RequestClass, open_url


RECEIPT_RE = re.compile(
    r"^【任务回执】(?P<id>task-[0-9]{8}-[0-9]{3}) (?P<title>[^\n]+)\n"
    r"状态：(?P<old_status>[^ ]+) → (?P<new_status>[^　]+)　"
    r"持棒：(?P<old_holder>[^ ]+) → (?P<new_holder>[^　]+)　备注：(?P<note>.+)$"
)


@dataclass(frozen=True)
class Receipt:
    task_id: str
    title: str
    old_status: str | None
    new_status: str
    old_holder: str | None
    new_holder: str
    note: str


def parse_receipt(text: str) -> Receipt:
    match = RECEIPT_RE.fullmatch(text.strip())
    if not match:
        raise ProtocolError("message is not a protocol v0.1 receipt")
    values = match.groupdict()
    return Receipt(
        task_id=values["id"],
        title=values["title"],
        old_status=None if values["old_status"] == "—" else values["old_status"],
        new_status=values["new_status"],
        old_holder=None if values["old_holder"] == "—" else values["old_holder"],
        new_holder=values["new_holder"],
        note=values["note"],
    )


class Transport(Protocol):
    def send(self, text: str, mention: str | None) -> str: ...
    def reply(self, root_id: str, text: str, mention: str | None) -> str: ...


class IntentDispatcher(Protocol):
    def dispatch(
        self, *, intent: str, idempotency_key: str, token: str
    ) -> Mapping[str, object]: ...


class DispatchError(ProtocolError):
    def __init__(self, status_code: int | None, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ServerIntentDispatcher:
    """Thin authenticated client for Retinue's canonical dispatch endpoint."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def dispatch(
        self, *, intent: str, idempotency_key: str, token: str
    ) -> Mapping[str, object]:
        request = urllib.request.Request(
            self.base_url + "/api/dispatch",
            method="POST",
            data=json.dumps(
                {"intent": intent, "idempotency_key": idempotency_key}
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with open_url(
                request, timeout=15, request_class=RequestClass.INWARD
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
                detail = (
                    error_payload.get("detail", "Retinue Server rejected the request")
                    if isinstance(error_payload, dict)
                    else "Retinue Server rejected the request"
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = "Retinue Server rejected the request"
            raise DispatchError(exc.code, str(detail)) from exc
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DispatchError(None, "cannot reach Retinue Server") from exc
        if not isinstance(payload, dict):
            raise DispatchError(None, "Retinue Server returned an invalid response")
        return payload


class MemoryTransport:
    """Deterministic transport used by tests and local demonstrations."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str | None]] = []

    def _append(self, kind: str, text: str, mention: str | None, root_id: str | None = None) -> str:
        message_id = f"message-{len(self.messages) + 1}"
        self.messages.append({"kind": kind, "text": text, "mention": mention, "root_id": root_id, "message_id": message_id})
        return message_id

    def send(self, text: str, mention: str | None) -> str:
        return self._append("send", text, mention)

    def reply(self, root_id: str, text: str, mention: str | None) -> str:
        return self._append("reply", text, mention, root_id)


class LarkCliTransport:
    """Send real post messages through the deployment's bound lark-cli profile."""

    def __init__(self, chat_id: str, profile: str, executable: str = "lark-cli") -> None:
        self.chat_id = chat_id
        self.profile = profile
        self.executable = executable

    @staticmethod
    def _content(text: str, mention: str | None) -> str:
        elements = []
        if mention:
            elements.extend(({"tag": "at", "user_id": mention}, {"tag": "text", "text": "\n"}))
        elements.append({"tag": "text", "text": text})
        return json.dumps({"zh_cn": {"content": [elements]}}, ensure_ascii=False)

    def _run(self, argv: list[str]) -> str:
        completed = subprocess.run(argv, check=True, capture_output=True, text=True)
        envelope = json.loads(completed.stdout)
        if not envelope.get("ok"):
            raise ProtocolError("lark-cli did not return a success envelope")
        data = envelope.get("data", envelope)
        message_id = data.get("message_id")
        if not isinstance(message_id, str):
            raise ProtocolError("lark-cli response is missing message_id")
        return message_id

    def send(self, text: str, mention: str | None) -> str:
        return self._run([self.executable, "--profile", self.profile, "im", "+messages-send", "--chat-id", self.chat_id, "--as", "bot", "--msg-type", "post", "--content", self._content(text, mention)])

    def reply(self, root_id: str, text: str, mention: str | None) -> str:
        return self._run([self.executable, "--profile", self.profile, "im", "+messages-reply", "--message-id", root_id, "--reply-in-thread", "--as", "bot", "--msg-type", "post", "--content", self._content(text, mention)])


class FeishuAdapter:
    def __init__(
        self,
        root: Path | str,
        *,
        transport: Transport | None = None,
        dispatcher: IntentDispatcher | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.environ = dict(os.environ if environ is None else environ)
        self.org = self._load_org()
        self.config = self.org.get("adapters", {}).get("feishu", {})
        if not self.config.get("enabled"):
            raise ProtocolError("Feishu adapter is not enabled")
        self.transport = transport or self._transport()
        self.dispatcher = dispatcher
        self.state_path = self.root / "nodes" / "feishu-adapter.json"
        self.roots, self.positions = self._load_state()

    def _load_org(self) -> dict:
        try:
            value = yaml.safe_load((self.root / "org.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ProtocolError(f"cannot read organization: {exc}") from exc
        validate_org(value)
        return value

    def _env(self, config_key: str) -> str:
        variable = self.config.get(config_key)
        value = self.environ.get(variable, "") if isinstance(variable, str) else ""
        if not value:
            raise ProtocolError(f"missing environment value for adapters.feishu.{config_key}")
        return value

    def _transport(self) -> LarkCliTransport:
        return LarkCliTransport(self._env("chat_id_env"), self._env("profile_env"), self.config.get("lark_cli", "lark-cli"))

    def _dispatch_sender_tokens(self) -> dict[str, str]:
        variable = self.config.get("dispatch_senders_env")
        if not isinstance(variable, str) or not variable:
            return {}
        raw = self.environ.get(variable, "")
        if not raw:
            raise ProtocolError(
                "missing environment value for adapters.feishu.dispatch_senders_env"
            )
        try:
            mapping = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(
                "adapters.feishu.dispatch_senders_env must contain a JSON object"
            ) from exc
        if not isinstance(mapping, dict) or any(
            not isinstance(sender, str)
            or not sender
            or not isinstance(token_env, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env)
            for sender, token_env in mapping.items()
        ):
            raise ProtocolError(
                "adapters.feishu.dispatch_senders_env must map sender ids to token environment variable names"
            )
        return mapping

    def _intent_dispatcher(self) -> IntentDispatcher:
        if self.dispatcher is None:
            try:
                base_url = self._env("dispatch_url_env")
            except ProtocolError as exc:
                raise DispatchError(
                    None, "Retinue Server dispatch URL is not configured"
                ) from exc
            self.dispatcher = ServerIntentDispatcher(base_url)
        return self.dispatcher

    def _load_state(self) -> tuple[dict[str, str], dict[str, int]]:
        if not self.state_path.exists():
            return {}, {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"cannot read Feishu adapter state: {exc}") from exc
        roots = value.get("roots", {}) if isinstance(value, dict) else {}
        positions = value.get("positions", {}) if isinstance(value, dict) else {}
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in roots.items()):
            raise ProtocolError("Feishu roots must map task ids to message ids")
        if any(not isinstance(k, str) or not isinstance(v, int) for k, v in positions.items()):
            raise ProtocolError("Feishu positions must map task ids to chain lengths")
        return roots, positions

    def _save_roots(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"roots": self.roots, "positions": self.positions}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def _mention_for(self, agent_id: str) -> str | None:
        agent = next((item for item in self.org["agents"] if item["id"] == agent_id), None)
        variable = ((agent or {}).get("im") or {}).get("feishu", {}).get("mention_id_env")
        return self.environ.get(variable) if variable else None

    def emit(self, task_path: Path | str) -> str:
        task = load_task(task_path)
        length = len(task["chain"])
        if self.positions.get(task["id"], 0) >= length:
            return "duplicate"
        event = task["chain"][-1]
        status = event.get("to_status") or task["status"]
        is_creation = event.get("from_status") is None
        main_flow = is_creation or status in {"blocked", "done"}
        target = task["created_by"] if status in {"blocked", "done", "cancelled"} else task["holder"]
        mention = self._mention_for(target)
        text = render_receipt(task)
        root_id = self.roots.get(task["id"])
        if main_flow or root_id is None:
            message_id = self.transport.send(text, mention)
            if root_id is None:
                self.roots[task["id"]] = message_id
        else:
            message_id = self.transport.reply(root_id, text, mention)
        self.positions[task["id"]] = length
        self._save_roots()
        return message_id

    def _reply_to_event(self, event: Mapping[str, object], text: str) -> None:
        message_id = event.get("message_id")
        if isinstance(message_id, str) and message_id:
            self.transport.reply(message_id, text, None)

    @staticmethod
    def _dispatch_result(payload: Mapping[str, object]) -> tuple[str, str, str, bool]:
        task_id = payload.get("id")
        title = payload.get("title")
        actor_id = payload.get("created_by")
        created = payload.get("created")
        if (
            not isinstance(task_id, str)
            or not isinstance(title, str)
            or not isinstance(actor_id, str)
            or not isinstance(created, bool)
        ):
            raise DispatchError(None, "Retinue Server returned an invalid response")
        return task_id, title, actor_id, created

    def _receive_intent(self, event: Mapping[str, object]) -> str:
        sender = event.get("sender_id")
        message_id = event.get("message_id")
        intent = str(event.get("text", "")).strip()
        if not isinstance(sender, str) or not isinstance(message_id, str) or not message_id:
            self._reply_to_event(
                event,
                "Task not published: the source event has no stable sender or message ID. Ask an operator to check the IM event subscription.",
            )
            return "rejected"
        if not intent:
            self._reply_to_event(
                event,
                "Task not published: send a non-empty request that includes a configured pipeline name or match term.",
            )
            return "unmatched"
        try:
            token_variable = self._dispatch_sender_tokens().get(sender)
        except ProtocolError:
            self._reply_to_event(
                event,
                "Task not published: IM publication is not configured. Ask an operator to configure the sender-to-actor token mapping.",
            )
            return "unavailable"
        if token_variable is None:
            self._reply_to_event(
                event,
                "Task not published: this sender is not authorized. Ask an operator to map this Feishu/Lark sender to a Retinue actor token.",
            )
            return "unauthorized"
        token = self.environ.get(token_variable, "")
        if not token:
            self._reply_to_event(
                event,
                "Task not published: the authorized sender token is unavailable. Ask an operator to check the adapter's secret environment.",
            )
            return "unavailable"
        try:
            payload = self._intent_dispatcher().dispatch(
                intent=intent,
                idempotency_key=message_id,
                token=token,
            )
            task_id, title, actor_id, created = self._dispatch_result(payload)
        except DispatchError as exc:
            detail = exc.detail.casefold()
            if exc.status_code == 422 and "ambiguous pipeline" in detail:
                reply = (
                    "Task not published: more than one configured pipeline matched this message. "
                    "Rephrase it to include one configured pipeline name, or ask an operator to refine the match terms."
                )
                outcome = "unmatched"
            elif exc.status_code == 422 and "pipeline template" in detail:
                reply = (
                    "Task not published: no configured pipeline matched this message. "
                    "Rephrase it to include a configured pipeline name or match term, or ask an operator to add one."
                )
                outcome = "unmatched"
            elif exc.status_code in {401, 403}:
                reply = (
                    "Task not published: this sender is not authorized by Retinue Server. "
                    "Ask an operator to check the actor-bound token mapping."
                )
                outcome = "unauthorized"
            elif exc.status_code == 409:
                reply = (
                    "Task not published: this message ID was already used with different text. "
                    "The existing task was left unchanged; ask an operator to inspect it."
                )
                outcome = "rejected"
            else:
                reply = (
                    "Task not published: Retinue Server could not accept the request. "
                    "Ask an operator to check the adapter and pipeline configuration."
                )
                outcome = "unavailable"
            self._reply_to_event(event, reply)
            return outcome
        verb = "Task published" if created else "Message already published"
        self._reply_to_event(
            event,
            f"{verb} as {actor_id}: {task_id} {title}\nOpen the Retinue board to follow this card.",
        )
        return "published" if created else "duplicate"

    def receive(self, event: Mapping[str, object]) -> str:
        sender = event.get("sender_id")
        mentions = event.get("mentions", [])
        self_id = self._env("self_mention_id_env")
        if not isinstance(mentions, list) or self_id not in mentions:
            return "ignored"
        text = str(event.get("text", "")).strip()
        if not text.startswith("【任务回执】"):
            return self._receive_intent(event)
        allowed = {item.strip() for item in self._env("allow_from_env").split(",") if item.strip()}
        if sender not in allowed:
            return "ignored"
        try:
            receipt = parse_receipt(text)
            path = self.root / "tasks" / f"{receipt.task_id}.yaml"
            task = load_task(path)
            if task["title"] != receipt.title:
                raise ProtocolError("receipt title does not match the task card")
            if task["status"] == receipt.new_status and task["holder"] == receipt.new_holder:
                return "duplicate"
            if task["status"] != receipt.old_status or task["holder"] != receipt.old_holder:
                raise ProtocolError("receipt origin does not match the task card")
            update_task(path, status=receipt.new_status, holder=receipt.new_holder, note=receipt.note, who=receipt.new_holder)
            return "applied"
        except ProtocolError as exc:
            root_id = self.roots.get(getattr(locals().get("receipt", None), "task_id", "")) or str(event.get("message_id", ""))
            if root_id:
                self.transport.reply(root_id, f"Receipt rejected: {exc}", None)
            return "rejected"


def normalize_event(payload: Mapping[str, object]) -> dict[str, object]:
    event = payload.get("event", payload)
    if isinstance(event, dict) and isinstance(event.get("event"), dict):
        event = event["event"]
    if not isinstance(event, dict):
        raise ProtocolError("event envelope is invalid")
    message = event.get("message", event)
    sender_block = event.get("sender", {})
    sender_id = sender_block.get("sender_id", {}) if isinstance(sender_block, dict) else {}
    sender = sender_id.get("open_id") if isinstance(sender_id, dict) else event.get("sender_id")
    content = message.get("content", "") if isinstance(message, dict) else ""
    try:
        decoded = (
            json.loads(content)
            if isinstance(content, str) and content.startswith("{")
            else content
        )
        text = decoded.get("text", "") if isinstance(decoded, dict) else decoded
    except json.JSONDecodeError:
        text = content
    if not isinstance(text, str):
        text = ""
    raw_mentions = message.get("mentions", []) if isinstance(message, dict) else event.get("mentions", [])
    mentions = []
    for item in raw_mentions if isinstance(raw_mentions, list) else []:
        if isinstance(item, str):
            mentions.append(item)
        elif isinstance(item, dict):
            identity = item.get("id", item)
            if isinstance(identity, dict):
                mentions.append(identity.get("open_id"))
    return {"sender_id": sender, "mentions": [item for item in mentions if item], "text": text, "message_id": message.get("message_id", "") if isinstance(message, dict) else ""}


def consume(adapter: FeishuAdapter, lines: Iterable[str]) -> list[str]:
    outcomes = []
    for line in lines:
        if line.strip():
            outcomes.append(adapter.receive(normalize_event(json.loads(line))))
    return outcomes


def listen(adapter: FeishuAdapter, command: str | list[str] | None = None) -> None:
    configured = command or adapter.config.get("event_command") or [adapter.config.get("lark_cli", "lark-cli"), "--profile", adapter._env("profile_env"), "event", "consume", "im.message.receive_v1"]
    argv = shlex.split(configured) if isinstance(configured, str) else configured
    with subprocess.Popen(argv, stdout=subprocess.PIPE, text=True) as process:
        if process.stdout is None:
            raise ProtocolError("event command has no stdout stream")
        consume(adapter, process.stdout)
        if process.wait() != 0:
            raise ProtocolError("event command exited unsuccessfully")


def emit_if_configured(task_path: Path | str) -> bool:
    path = Path(task_path)
    root = path.parent.parent
    org_path = root / "org.yaml"
    if not org_path.exists():
        return False
    try:
        org = yaml.safe_load(org_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    if not ((org or {}).get("adapters", {}).get("feishu", {}).get("enabled")):
        return False
    FeishuAdapter(root).emit(path)
    return True
