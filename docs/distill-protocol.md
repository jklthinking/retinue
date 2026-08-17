# Distillation protocol (M0)

Session → organisation-memory distillation on the board. This batch is the
substrate only: candidate entities, a cooling gate, an approval gate, and an
append-only audit chain. Summary and distillation intelligence stay with
external executors; they register candidates through the API. The server never
calls a model.

Obsidian / vault export adapters are M1. Embedding and retrieval baselines are
M2. Neither is in scope here.

## Three iron laws

1. **原件绝不删改** — This pipeline has no code path that rewrites a
   `runtime_sessions` row (title, summary, messages, hashes, cursors). Source
   sessions remain authoritative where they already live.
2. **候选绝不自动转正** — A candidate stays `pending` until an explicit
   privileged promote or reject decision. No scanner, cron, or side effect
   flips status on its own.
3. **正文不出本机** — Candidates carry only a bounded summary (at most 2000
   characters) plus optional back-link anchors. The API does not copy full
   session transcripts into distill tables.

## Entities (schema v19)

- `distill_candidates` — `pending` / `promoted` / `rejected`, with
  `cooldown_until` (default create-time + 24 hours, overridable via
  `RETINUE_DISTILL_COOLDOWN_HOURS` or the register body's `cooldown_hours`),
  `promoted_entry_id` back to `knowledge_sources` when promoted.
- `distill_events` — append-only audit (`registered`, `promoted`, `rejected`),
  same shape as private todo events.

## API

| Method | Path | Who |
|--------|------|-----|
| `POST` | `/api/distill/candidates` | authenticated agent token or human session |
| `GET` | `/api/distill/candidates` | authenticated (`status` filter optional) |
| `GET` | `/api/distill/candidates/{id}` | authenticated |
| `POST` | `/api/distill/candidates/{id}/promote` | privileged human (`admin` / `member`) |
| `POST` | `/api/distill/candidates/{id}/reject` | privileged human (`admin` / `member`) |

## Gates on promote

1. **Cooling gate** — if `utcnow() < cooldown_until`, the route returns 409.
2. **Approval gate** — board `Approval` rows are task-pipeline queen gates
   (`task_id` + `stage_index`). Distill candidates are not pipeline stages, so
   M0 reuses privileged-identity enforcement rather than inventing a parallel
   approval entity. Agents and viewers cannot promote or reject.

A successful promote creates a `knowledge_sources` row (`kind=distill`,
`notes` = candidate summary) and writes `promoted_entry_id`. Reject requires a
non-empty `decision_note` and leaves an audit event.
