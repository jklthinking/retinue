"""Deterministic one-node sample data for the two-minute local demo."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import random

import yaml

from core.protocol.task import ProtocolError, create_task, update_task


SAMPLE_DATE = date(2026, 7, 20)
SAMPLE_TIMEZONE = timezone(timedelta(hours=8))


def _at(day: date, hour: int, minute: int = 0) -> str:
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=SAMPLE_TIMEZONE
    ).isoformat(timespec="minutes")


def _org() -> dict:
    return {
        "org": "sample-studio",
        "departments": [{"id": "studio", "name": "Studio", "lead": "claude-1"}],
        "agents": [
            {
                "id": "claude-1",
                "dept": "studio",
                "runtime": "claude-code",
                "model": "claude-sonnet",
                "node": "laptop",
                "on_claim": ["claude", "-p", "Read RETINUE_TASK_FILE and complete its acceptance checks. Update the Retinue card and print its receipt."],
            },
            {
                "id": "codex-1",
                "dept": "studio",
                "runtime": "codex",
                "model": "gpt-5",
                "node": "laptop",
                "on_claim": [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--sandbox",
                    "workspace-write",
                    "--skip-git-repo-check",
                    "Read RETINUE_TASK_FILE, move the card to doing, complete its acceptance checks, move it to done, then print retinue receipt for the card.",
                ],
            },
            {
                "id": "observer-1",
                "dept": "studio",
                "runtime": "shell",
                "model": "local",
                "node": "laptop",
            },
        ],
        "nodes": [{"id": "laptop"}],
    }


def _seed_tasks(root: Path) -> None:
    tasks = root / "tasks"
    first = create_task(tasks, task_id="task-20260720-101", title="Draft onboarding", created_by="claude-1", holder="claude-1", dept="studio", at=_at(SAMPLE_DATE, 8))
    update_task(first, status="doing", note="Started onboarding draft", at=_at(SAMPLE_DATE, 8, 5))
    update_task(first, status="done", note="Onboarding steps verified", at=_at(SAMPLE_DATE, 8, 20))

    second = create_task(tasks, task_id="task-20260720-102", title="Export runtime metrics", created_by="claude-1", holder="claude-1", dept="studio", at=_at(SAMPLE_DATE, 8, 10))
    update_task(second, status="doing", note="Read local transcripts", at=_at(SAMPLE_DATE, 8, 15))
    update_task(second, status="done", note="Metrics snapshot emitted", at=_at(SAMPLE_DATE, 8, 30))

    third = create_task(tasks, task_id="task-20260720-103", title="Review task API", created_by="codex-1", holder="codex-1", dept="studio", at=_at(SAMPLE_DATE, 8, 25))
    update_task(third, status="doing", note="Running API checks", at=_at(SAMPLE_DATE, 8, 35))

    create_task(
        tasks,
        task_id="task-20260720-104",
        title="Polish quickstart",
        created_by="observer-1",
        holder="observer-1",
        dept="studio",
        depends_on=["task-20260720-103"],
        at=_at(SAMPLE_DATE, 8, 40),
    )

    fifth = create_task(tasks, task_id="task-20260720-105", title="Verify overview", created_by="claude-1", holder="claude-1", dept="studio", at=_at(SAMPLE_DATE, 8, 45))
    update_task(fifth, status="doing", note="Rendered both data channels", at=_at(SAMPLE_DATE, 8, 50))
    update_task(fifth, status="handoff", holder="codex-1", note="Ready for review", at=_at(SAMPLE_DATE, 9))

    sixth = create_task(tasks, task_id="task-20260720-106", title="Resolve runtime timeout", created_by="codex-1", holder="codex-1", dept="studio", at=_at(SAMPLE_DATE, 8, 55))
    update_task(sixth, status="doing", note="Reproduced timeout", at=_at(SAMPLE_DATE, 9, 5))
    update_task(sixth, status="blocked", blocked_reason="Runtime unavailable", note="Waiting for runtime", at=_at(SAMPLE_DATE, 9, 10))


def _seed_metrics(root: Path, seed: int) -> None:
    generator = random.Random(seed)
    metrics = root / "metrics"
    metrics.mkdir()
    runtimes = {
        "claude-1": "claude-code",
        "codex-1": "codex",
        "observer-1": "shell",
    }
    for index, (agent_id, runtime) in enumerate(runtimes.items()):
        daily = []
        for offset in range(6, -1, -1):
            day = SAMPLE_DATE - timedelta(days=offset)
            input_tokens = generator.randint(80, 420) * (index + 1)
            cache_creation = generator.randint(900, 3_400) * (index + 1)
            cache_read = generator.randint(4_000, 22_000) * (index + 1)
            output_tokens = generator.randint(500, 2_800) * (index + 1)
            daily.append({
                "date": day.isoformat(),
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + cache_creation + cache_read + output_tokens,
                "sessions": generator.randint(1, 4),
            })
        snapshot = {
            "schema_version": 1,
            "agent_id": agent_id,
            "runtime": runtime,
            "generated_at": _at(SAMPLE_DATE, 9, 15),
            "timezone": "Asia/Shanghai",
            "source": {"kind": f"sample-seed-{seed}", "read_only": True},
            "today": daily[-1],
            "last_7_days": {
                "total_tokens": sum(item["total_tokens"] for item in daily),
                "sessions": sum(item["sessions"] for item in daily),
                "daily": daily,
            },
            "sessions": sum(item["sessions"] for item in daily),
            "last_active_at": _at(SAMPLE_DATE, 9, 12 - index),
        }
        (metrics / f"{agent_id}.json").write_text(
            json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
        )


def seed_sample(path: Path | str, *, seed: int = 42) -> Path:
    """Create exactly one node, three agents, six cards, and seven token days."""
    root = Path(path).resolve()
    if root.exists() and any(root.iterdir()):
        raise ProtocolError(f"refusing to overwrite non-empty demo workspace: {root}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "nodes").mkdir()
    (root / "org.yaml").write_text(
        yaml.safe_dump(_org(), sort_keys=False), encoding="utf-8"
    )
    _seed_tasks(root)
    _seed_metrics(root, seed)
    checkpoint = {}
    for path in sorted((root / "tasks").glob("*.yaml")):
        task = yaml.safe_load(path.read_text(encoding="utf-8"))
        checkpoint[task["id"]] = len(task["chain"])
    (root / "nodes" / "laptop.daemon.json").write_text(
        json.dumps(checkpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return root
