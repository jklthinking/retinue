---
name: retinue
description: Coordinate work through a local Retinue workspace using its MCP tools. Use when asked to create, claim, update, hand off, complete, or inspect Retinue task cards.
---

# Retinue coordination

Retinue task cards are the canonical record. Use the MCP tools; do not edit YAML directly.

1. Call `my_tasks` before starting work.
2. Use `task_new` to create a queued card. Give it a unique `task-YYYYMMDD-NNN` id, a concrete title, an honest priority, and observable acceptance criteria.
3. If you hold a queued or handoff card, call `task_update` with `status="doing"` and a short, factual `note` (server MCP: `task_start`). Starting work does not move the progress bar.
4. While you work, call `task_progress` (or `task_update` with `progress`) with a 0–100 percent and a receipt note. Notes and `doing` do not infer a percent, so a busy-looking card can sit at 0% until you report.
5. If a write returns `lease expired; stale writer is fenced` and you still hold the card, call `task_renew` or `task_start` (续租) then `task_progress`. If the card is back on the dispatch hall, `task_claim` (重新认领) then start and report progress.
6. Do the work and run every item in `acceptance`.
7. Call `task_update` with `status="done"` only after those checks pass. Use `status="handoff"` plus `holder` when another agent must review or continue. Use `status="blocked"` plus `blocked_reason` only for a real blocker.
8. Call `task_receipt` and report the returned receipt exactly.

Only the current holder may update a card. Every status or holder change needs a receipt-quality note. Never skip from `queued` directly to `done`.
