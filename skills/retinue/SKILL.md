---
name: retinue
description: Coordinate work through a local Retinue workspace using its MCP tools. Use when asked to create, claim, update, hand off, complete, or inspect Retinue task cards.
---

# Retinue coordination

Retinue task cards are the canonical record. Use the MCP tools; do not edit YAML directly.

1. Call `my_tasks` before starting work.
2. Use `task_new` to create a queued card. Give it a unique `task-YYYYMMDD-NNN` id, a concrete title, an honest priority, and observable acceptance criteria.
3. If you hold a queued or handoff card, call `task_update` with `status="doing"` and a short, factual `note`.
4. Do the work and run every item in `acceptance`.
5. Call `task_update` with `status="done"` only after those checks pass. Use `status="handoff"` plus `holder` when another agent must review or continue. Use `status="blocked"` plus `blocked_reason` only for a real blocker.
6. Call `task_receipt` and report the returned receipt exactly.

Only the current holder may update a card. Every status or holder change needs a receipt-quality note. Never skip from `queued` directly to `done`.
