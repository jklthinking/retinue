# Adapter contribution guide

Retinue follows a Prometheus-like integration idea: **bring one observable,
scrapeable local fact, not an entire runtime fork**. Exporters and IM adapters
stay thin; canonical task state and policy remain in the core protocol.

## Runtime exporters

An exporter reads a runtime's local records and atomically writes one
`metrics/<agent-id>.json` snapshot. The minimum shape is:

```json
{
  "schema_version": 1,
  "agent_id": "runtime-1",
  "runtime": "example-runtime",
  "generated_at": "2026-07-20T09:15:00+08:00",
  "timezone": "Asia/Shanghai",
  "source": {"kind": "example-jsonl", "read_only": true},
  "token_accounting": {
    "total_definition": "state the exact upstream formula",
    "cross_runtime_comparable": false
  },
  "today": {"total_tokens": 0, "sessions": 0},
  "last_7_days": {"total_tokens": 0, "sessions": 0, "daily": []},
  "last_active_at": null
}
```

Exporter requirements:

- Open the runtime source read-only and reject any destination inside it.
- Write a sibling temporary file and atomically replace the metrics target.
- Use the requested local timezone and document the day boundary.
- Deduplicate repeated events or take deltas from cumulative counters; test the
  exact upstream behavior with neutral synthetic fixtures.
- State included, excluded, and subset token fields. Default
  `cross_runtime_comparable` to false unless equivalence is proven.
- Skip or count malformed records without leaking transcript content, paths,
  account identifiers, or credentials into logs and screenshots.
- Add a CLI entry under `retinue export`, focused tests, and a short accounting
  note in `adapters/exporters/README.md`.

## IM adapters

An IM adapter translates between one provider event and a canonical Retinue
mutation/receipt. It is transport, not the database and not a source of write
authority.

- Normalize provider payloads into a small internal event before applying any
  task operation.
- Require explicit sender allowlists and an addressed mention where the
  provider supports bot-to-bot delivery. Never use wildcard trust.
- Resolve provider-scoped identities in configuration; do not persist concrete
  tenant, chat, user, or bot identifiers in the repository.
- Make retries idempotent so one logical transition appends one chain event.
- Treat receipts as terminal unless this adapter's agent is the addressed next
  holder; prevent echo loops.
- Keep credentials in environment variables or an external secret manager.
- Return useful protocol errors without dumping raw events or secrets.
- Test normalization, authorization denial, duplicate delivery, and disabled
  network behavior with synthetic payloads.

## Contribution checklist

1. Read [`docs/security.md`](security.md), the task-state protocol, and
   [`docs/integration-ladder.md`](integration-ladder.md).
2. Implement independently from public contracts and observed formats. Do not
   copy upstream or third-party source code; record the dependency license for
   any library you add.
3. Keep provider-specific code under `adapters/exporters/` or `adapters/im/`;
   avoid provider branches in the core state machine.
4. Add deterministic tests with neutral names and no live IDs, paths, token
   values, network calls, or credentials.
5. Run focused tests, the full suite, `git diff --check`, compile checks,
   identifier scanning, and credential scanning.
6. Document accounting, capabilities, known gaps, and the platform/OS actually
   exercised. “Expected” is not “verified.”

A good adapter can be removed without changing the task protocol. A good
exporter can be run repeatedly without changing its source. A good IM adapter
can receive the same event twice and still produce one canonical mutation.
