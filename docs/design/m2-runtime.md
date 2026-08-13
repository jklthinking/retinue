# M2 runtime design

Retinue M2 keeps the YAML task card as the only durable task state. Runtime
components observe or mutate that state; IM messages never replace it.

## Daemon

`retinue daemon ROOT --node NODE` scans `ROOT/tasks`, resolves the agents owned
by `NODE` from `ROOT/org.yaml`, and invokes an agent's `on_claim` argv when the
latest chain event assigns the task to that agent. A JSON checkpoint under
`ROOT/nodes` stores the handled chain length per task. The checkpoint is
atomically replaced after a successful hook, so restarts do not replay work.

Hooks are argv lists (or shell-like strings parsed without a shell). Task
metadata is passed through `RETINUE_*` environment variables.

## Feishu adapter

The adapter has a pure protocol layer and an external transport boundary. Task
mutations call the configured adapter automatically. The default transport
invokes `lark-cli`; tests use an in-memory transport.

Each task gets a persisted root message id. Creation opens the task thread;
normal transitions reply to that root. `blocked` and `done` are visible
milestones in the main chat. Terminal receipts mention the creator; handoffs
mention the next holder. Deployment identifiers are resolved from environment
variable names stored in `org.yaml`.

Inbound `im.message.receive_v1` events must mention the local bot and come from
an allowed sender. Receipt senders and human publishers have separate trust
configuration. Receipts are parsed, checked against the local card, and then
applied. Addressed publisher text is attributed through an operator-configured
sender-to-actor-token mapping and sent to the server's deterministic dispatch;
the platform message ID makes retries idempotent. Unknown senders fail closed,
and unmatched text receives an actionable reply without creating a card.
Duplicates are no-ops; invalid receipts receive a thread error reply.
`retinue feishu listen` consumes NDJSON from a configurable long-connection
command such as `lark-cli event consume im.message.receive_v1`.

## Panel

`retinue panel ROOT` runs a read-only standard-library HTTP server. `/api/tasks`
returns cards and derived timestamps. `/` renders a five-column board and
`/tasks/<id>` renders the chain as a conversation timeline. M2 has no write API.
