"""Feishu interactive cards for pipeline flows (custom-bot webhook).

Custom bots cannot receive button callbacks, so decision buttons are URL
buttons pointing at signed, decision-bound confirmation pages. GET only shows
the confirmation; the human's POST settles the approval.
Off unless RETINUE_FEISHU_WEBHOOK is set; links built from
RETINUE_PUBLIC_URL (e.g. https://retinue.example).
"""

from __future__ import annotations

import os
from typing import Any


def _send(card: dict[str, Any]) -> None:
    """Post an interactive card through the notifier group-webhook channel."""
    from .notify import fire_and_forget_group_webhook

    fire_and_forget_group_webhook({"msg_type": "interactive", "card": card})


def _base_url() -> str:
    return os.environ.get("RETINUE_PUBLIC_URL", "http://127.0.0.1:9219").rstrip("/")


def _header(title: str, template: str) -> dict[str, Any]:
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def _md(text: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def send_baton_card(task: dict[str, Any], stage_name: str, holder_name: str) -> None:
    """发牌卡:the baton just moved to `holder_name` for `stage_name`."""
    _send(
        {
            "header": _header("🎴 接力发牌", "blue"),
            "elements": [
                _md(
                    f"**{task['title']}**\n"
                    f"节点:**{stage_name}** → 下一棒:**{holder_name}**\n"
                    f"编号:{task['id']} · 请接手后转入 doing 并按两行制式回执"
                ),
            ],
        }
    )


def send_decision_card(
    task: dict[str, Any],
    stage_name: str,
    approval_id: int,
    approve_token: str,
    reject_token: str,
) -> None:
    """决策卡:queen gate opened; buttons carry signed one-time links."""
    base = _base_url()
    approve_link = f"{base}/api/approvals/{approval_id}/act?token={approve_token}&decision=approve"
    reject_link = f"{base}/api/approvals/{approval_id}/act?token={reject_token}&decision=reject"
    _send(
        {
            "header": _header("审批门 · 待拍板", "orange"),
            "elements": [
                _md(
                    f"**{task['title']}**\n"
                    f"节点:**{stage_name}** · 编号:{task['id']}\n"
                    f"最新回执:{task['chain'][-1]['did'] if task.get('chain') else '—'}"
                ),
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 批准"},
                            "type": "primary",
                            "url": approve_link,
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 驳回"},
                            "type": "danger",
                            "url": reject_link,
                        },
                    ],
                },
            ],
        }
    )


def send_delivery_card(task: dict[str, Any]) -> None:
    """交付卡:the pipeline completed end to end."""
    _send(
        {
            "header": _header("📦 流程交付", "green"),
            "elements": [
                _md(
                    f"**{task['title']}**\n"
                    f"编号:{task['id']} · 全部节点完成,进度 100%\n"
                    f"收官回执:{task['chain'][-1]['did'] if task.get('chain') else '—'}"
                ),
            ],
        }
    )
