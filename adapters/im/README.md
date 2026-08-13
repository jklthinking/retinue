# IM adapters

The Feishu adapter emits protocol v0.1 receipts automatically after CLI task
mutations when `adapters.feishu.enabled` is true in `org.yaml`. Deployment ids
and the bound `lark-cli` profile are referenced only by environment-variable
name. See `examples/org.yaml` for the complete neutral configuration.

```bash
retinue feishu emit ./demo ./demo/tasks/task-20260719-001.yaml
retinue feishu listen ./demo
```

The listener consumes `im.message.receive_v1` NDJSON from the configured long
connection command. Addressed protocol receipts are accepted only from the
receipt allowlist, validated against the local card, and applied through legal
transitions.

Addressed human text may also call Retinue Server's authenticated
`POST /api/dispatch`. `dispatch_senders_env` names a JSON sender-ID to token-env
mapping, while `dispatch_url_env` names the server URL environment variable.
The server token binds each allowed sender to a canonical actor. Unknown
senders fail closed; the platform message ID is the idempotency key; unmatched
text gets an actionable reply and no card. Message text is passed only as
intent to deterministic template matching and never selects executable
configuration.

Message policy is one task, one thread: creation establishes the root; ordinary
handoffs reply to it. Only creation, blocked, and done milestones open a new
main-flow message. Terminal receipts mention the creator.
