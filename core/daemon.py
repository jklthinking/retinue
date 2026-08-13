"""Per-node task-card watcher with durable claim checkpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Callable

import yaml

from core.protocol.org import validate_org
from core.protocol.task import ProtocolError, load_task


Runner = Callable[[list[str], dict[str, str]], object]


def _default_runner(argv: list[str], env: dict[str, str]) -> None:
    subprocess.run(argv, env=env, check=True)


class TaskDaemon:
    """Scan task cards and run local `on_claim` hooks exactly once per event."""

    def __init__(
        self,
        root: Path | str,
        node_id: str,
        *,
        runner: Runner = _default_runner,
        state_path: Path | str | None = None,
    ) -> None:
        self.root = Path(root)
        self.node_id = node_id
        self.runner = runner
        self.state_path = Path(state_path or self.root / "nodes" / f"{node_id}.daemon.json")
        self.agents = self._load_agents()
        self.positions = self._load_positions()

    def _load_agents(self) -> dict[str, dict]:
        try:
            org = yaml.safe_load((self.root / "org.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ProtocolError(f"cannot read organization: {exc}") from exc
        validate_org(org)
        nodes = {item["id"] for item in org["nodes"]}
        if self.node_id not in nodes:
            raise ProtocolError(f"unknown node: {self.node_id}")
        return {item["id"]: item for item in org["agents"] if item["node"] == self.node_id}

    def _load_positions(self) -> dict[str, int]:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"cannot read daemon checkpoint: {exc}") from exc
        if not isinstance(data, dict) or any(not isinstance(v, int) or v < 0 for v in data.values()):
            raise ProtocolError("daemon checkpoint must map task ids to chain lengths")
        return data

    def _save_positions(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.positions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    @staticmethod
    def _argv(hook: object) -> list[str]:
        if isinstance(hook, str):
            argv = shlex.split(hook)
        elif isinstance(hook, list) and all(isinstance(item, str) for item in hook):
            argv = hook
        else:
            raise ProtocolError("on_claim must be a command string or argv list")
        if not argv:
            raise ProtocolError("on_claim cannot be empty")
        return argv

    def scan_once(self) -> int:
        triggered = 0
        dirty = False
        for path in sorted((self.root / "tasks").glob("*.y*ml")):
            task = load_task(path)
            length = len(task["chain"])
            position = self.positions.get(task["id"], 0)
            if length <= position:
                continue
            event = task["chain"][-1] if task["chain"] else {}
            agent = self.agents.get(task["holder"])
            is_claim = event.get("to_holder") == task["holder"] and event.get("from_holder") != event.get("to_holder")
            if agent is not None and is_claim and agent.get("on_claim"):
                env = dict(os.environ)
                env.update({
                    "RETINUE_ROOT": str(self.root),
                    "RETINUE_TASK_FILE": str(path),
                    "RETINUE_TASK_ID": task["id"],
                    "RETINUE_AGENT_ID": task["holder"],
                })
                self.runner(self._argv(agent["on_claim"]), env)
                triggered += 1
            self.positions[task["id"]] = length
            dirty = True
        if dirty:
            self._save_positions()
        return triggered

    def serve(self, poll_interval: float = 1.0) -> None:
        if poll_interval <= 0:
            raise ProtocolError("poll interval must be positive")
        while True:
            self.scan_once()
            time.sleep(poll_interval)
