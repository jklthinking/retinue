"""Node health watermark policy (M0).

Deployment config lives in ``<data-dir>/watermarks.yaml`` (off by default).
Heartbeats already carry disk and load JSON; this module classifies them into
ok / warn / high / critical / unknown, and when enabled opens at most one
open ops card per node and severity tier for disk high/critical crossings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Actor, Task
from .engine import create_task

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "watermarks.yaml"
DEFAULT_ACTOR = "retinue-watch"

LEVELS = ("ok", "warn", "high", "critical", "unknown")
CARD_TIERS = ("high", "critical")
OPEN_STATUSES = ("queued", "doing", "handoff", "blocked")

WatermarkLevel = str  # ok | warn | high | critical | unknown


@dataclass
class DiskThresholds:
    warn: float = 80.0
    high: float = 90.0
    critical: float = 95.0


@dataclass
class LoadThresholds:
    """Optional load[0] thresholds. Absent config => load not classified."""

    warn: float | None = None
    high: float | None = None
    critical: float | None = None

    @property
    def configured(self) -> bool:
        return self.warn is not None or self.high is not None or self.critical is not None


@dataclass
class WatermarksConfig:
    enabled: bool = False
    actor: str = DEFAULT_ACTOR
    disk: DiskThresholds | None = None
    load: LoadThresholds | None = None

    def __post_init__(self) -> None:
        if self.disk is None:
            self.disk = DiskThresholds()
        if self.load is None:
            self.load = LoadThresholds()


def load_watermarks_config(data_dir: Path | str | None) -> WatermarksConfig:
    """Load ``<data_dir>/watermarks.yaml``. Missing file => inert defaults."""
    cfg = WatermarksConfig()
    if not data_dir:
        return cfg
    path = Path(data_dir) / CONFIG_FILENAME
    if not path.is_file():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("watermarks config unreadable at %s: %s", path, exc)
        return cfg
    if not isinstance(raw, dict):
        return cfg
    cfg.enabled = bool(raw.get("enabled", False))
    actor = str(raw.get("actor") or DEFAULT_ACTOR).strip()
    cfg.actor = actor or DEFAULT_ACTOR
    disk_raw = raw.get("disk")
    if isinstance(disk_raw, dict):
        cfg.disk = DiskThresholds(
            warn=float(disk_raw.get("warn", 80)),
            high=float(disk_raw.get("high", 90)),
            critical=float(disk_raw.get("critical", 95)),
        )
    load_raw = raw.get("load")
    if isinstance(load_raw, dict):
        def _opt(key: str) -> float | None:
            if key not in load_raw or load_raw[key] is None:
                return None
            return float(load_raw[key])

        cfg.load = LoadThresholds(
            warn=_opt("warn"),
            high=_opt("high"),
            critical=_opt("critical"),
        )
    return cfg


def _classify_percent(value: float, warn: float, high: float, critical: float) -> WatermarkLevel:
    # Highest tier wins. Boundaries: ok < warn, warn >= warn, high >= high, critical >= critical.
    if value >= critical:
        return "critical"
    if value >= high:
        return "high"
    if value >= warn:
        return "warn"
    return "ok"


def classify_disk(disk: dict[str, Any] | None, thresholds: DiskThresholds) -> WatermarkLevel:
    if not isinstance(disk, dict):
        return "unknown"
    percent = disk.get("percent")
    if percent is None:
        return "unknown"
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return "unknown"
    return _classify_percent(value, thresholds.warn, thresholds.high, thresholds.critical)


def classify_load(load: list[Any] | None, thresholds: LoadThresholds) -> WatermarkLevel:
    if not thresholds.configured:
        return "unknown"
    if not isinstance(load, list) or not load:
        return "unknown"
    try:
        value = float(load[0])
    except (TypeError, ValueError):
        return "unknown"
    warn = thresholds.warn if thresholds.warn is not None else float("inf")
    high = thresholds.high if thresholds.high is not None else float("inf")
    critical = thresholds.critical if thresholds.critical is not None else float("inf")
    if warn == float("inf") and high == float("inf") and critical == float("inf"):
        return "unknown"
    return _classify_percent(value, warn, high, critical)


def compute_watermark(
    disk: dict[str, Any] | None,
    load: list[Any] | None,
    config: WatermarksConfig | None = None,
) -> dict[str, WatermarkLevel]:
    cfg = config if config is not None else WatermarksConfig()
    assert cfg.disk is not None
    assert cfg.load is not None
    return {
        "disk": classify_disk(disk, cfg.disk),
        "load": classify_load(load, cfg.load),
    }


def dedupe_ref(node_id: str, tier: str) -> str:
    return f"watermark-disk-{node_id}-{tier}"


def card_title(node_id: str) -> str:
    return f"节点磁盘告警 {node_id}"


def _task_refs(task: Task) -> list[str]:
    refs = task.refs
    return list(refs) if isinstance(refs, list) else []


def find_open_watermark_card(db: Session, node_id: str, tier: str) -> Task | None:
    """Return an open card for this node and tier, if any.

    Dedup keys prefer the stable ref ``watermark-disk-{node_id}-{tier}``;
    the human title ``节点磁盘告警 {node_id}`` is kept for operators.
    """
    ref = dedupe_ref(node_id, tier)
    title = card_title(node_id)
    rows = db.execute(
        select(Task).where(Task.status.in_(OPEN_STATUSES)).order_by(Task.id)
    ).scalars()
    for task in rows:
        refs = _task_refs(task)
        if ref in refs:
            return task
        if task.title == title and any(r == ref for r in refs):
            return task
    return None


def _priority_for_tier(tier: str) -> str:
    return "urgent" if tier == "critical" else "high"


def _maybe_open_disk_card(
    db: Session,
    *,
    node_id: str,
    tier: str,
    disk: dict[str, Any],
    actor_id: str,
) -> Task | None:
    if tier not in CARD_TIERS:
        return None
    if find_open_watermark_card(db, node_id, tier) is not None:
        logger.info(
            "watermark card already open for %s tier=%s; skipping", node_id, tier
        )
        return None
    actor = db.get(Actor, actor_id)
    if actor is None:
        logger.warning(
            "watermark actor %s missing; recording level only for %s", actor_id, node_id
        )
        return None
    percent = disk.get("percent")
    try:
        percent_text = f"{float(percent):.1f}"
    except (TypeError, ValueError):
        percent_text = "unknown"
    note = (
        f"节点 {node_id} 磁盘占用 {percent_text}% 达到 {tier} 水位，"
        "自动开运维卡（幂等）"
    )
    acceptance = [
        "确认磁盘占用已回落到告警阈值以下",
        "记录清理或扩容措施并回写卡链",
    ]
    task = create_task(
        db,
        title=card_title(node_id),
        created_by=actor_id,
        holder=actor_id,
        dept="ops",
        priority=_priority_for_tier(tier),
        acceptance=acceptance,
        refs=[dedupe_ref(node_id, tier)],
        note=note,
        open_dispatch=True,
        event_type="watermark",
        event_payload={
            "watermark_node": node_id,
            "watermark_tier": tier,
            "watermark_metric": "disk",
        },
    )
    logger.info(
        "opened watermark card %s for %s tier=%s percent=%s",
        task.id,
        node_id,
        tier,
        percent_text,
    )
    return task


def evaluate_and_maybe_open_card(
    db: Session,
    *,
    node_id: str,
    disk: dict[str, Any] | None,
    load: list[Any] | None,
    data_dir: Path | str | None,
) -> dict[str, WatermarkLevel]:
    """Classify watermark levels; open a disk ops card when enabled and over line."""
    config = load_watermarks_config(data_dir)
    watermark = compute_watermark(disk, load, config)
    disk_level = watermark["disk"]
    if not config.enabled:
        return watermark
    if disk_level in CARD_TIERS:
        try:
            _maybe_open_disk_card(
                db,
                node_id=node_id,
                tier=disk_level,
                disk=disk or {},
                actor_id=config.actor,
            )
        except Exception:
            # Never break the heartbeat path.
            logger.exception(
                "watermark card open failed for %s tier=%s", node_id, disk_level
            )
    elif disk_level == "warn":
        logger.info("watermark disk warn for %s (no card)", node_id)
    return watermark
