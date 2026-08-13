# Integration ladder

Retinue separates registration, observation, dispatch, and native coordination so a runtime can join incrementally. An integration does not need to implement every level on day one.

| Level | Capability | Minimum integration |
|---|---|---|
| L0 Register | Roster visibility | One `org.yaml` agent entry |
| L1 Observe | Activity and token visibility | One read-only exporter |
| L2 Dispatch | Automatic work after assignment | One single-shot `on_claim` CLI command |
| L3 Collaborate | Native claim, update, handoff, and receipt flow | Retinue MCP or a native protocol wrapper |

## L0 — Register

Add one agent entry to `org.yaml` with its runtime, model, and node. It immediately appears in the overview even when no exporter or command hook exists.

```yaml
agents:
  - id: agent-1
    dept: studio
    runtime: example-runtime
    model: example-model
    node: laptop
```

- Capability: roster visibility
- Code required: none
- Useful for: any local or remote agent that needs an inventory entry

## L1 — Observe

Add a small read-only exporter that translates the runtime's own logs, transcript files, or session database into `metrics/<agent-id>.json`.

- Capability: recent activity, inferred online state, session counts, and token totals
- Code required: one runtime-specific reader
- Contract: never modify the runtime's source; write snapshots atomically; state the token fields and deduplication rule
- MVP example: `retinue export claude-code`

Token figures from different runtimes are not automatically billing-equivalent. Exporters must preserve honest field names and explain what their displayed total includes.

## L2 — Dispatch

Configure the runtime's single-shot CLI under `on_claim`. The node daemon invokes that command directly when a new holder event lands on the node.

```yaml
on_claim:
  - example-agent
  - run
  - Read RETINUE_TASK_FILE, execute the acceptance checks, update the card, and print its receipt.
```

- Capability: automatic work after assignment
- Code required: none when the runtime already has a non-interactive CLI
- Runtime context: `RETINUE_ROOT`, `RETINUE_TASK_FILE`, `RETINUE_TASK_ID`, and `RETINUE_AGENT_ID`
- Safety: executable hooks belong only in administrator-owned `org.yaml`; task cards never contain executable commands

Claude Code and Codex both provide single-shot CLI modes suitable for this level. Any equivalent runtime can use the same environment contract.

## L3 — Collaborate

Connect `retinue mcp` or implement the same operations through the runtime's native plugin mechanism.

- Capability: discover assigned work, create or claim cards, append progress, hand off, finish, and return canonical receipts
- Code required: MCP configuration or one native plugin
- Write rule: only the current holder may mutate a card
- Portable tools: `task_list`, `ready_work`, `my_tasks`, `task_new`,
  `task_dependency_add`, `task_dependency_remove`, `task_update`, and
  `task_receipt`

MCP-capable runtimes can use the bundled onboarding package without custom code. Runtimes with plugin systems may provide a native L3 wrapper, but the wrapper should call the public Retinue protocol instead of copying third-party implementation code.

## Choosing a starting level

| Need | Start at | Next step |
|---|---|---|
| Inventory only | L0 | Add an exporter when local telemetry is available |
| Monitor an existing runtime | L1 | Add `on_claim` after its CLI is stable |
| Assign work from Retinue | L2 | Add MCP when the agent should manage its own cards |
| Full agent-to-agent coordination | L3 | Keep L1 for independent, failure-resistant telemetry |

L3 does not replace L1. Native coordination is the control path; exporters are the independent observation path and remain useful when an agent crashes, forgets to report, or cannot spend tokens on self-reporting.

## Contribution boundary

An exporter or plugin contribution should include a synthetic fixture, a real read-only smoke test when possible, documented token accounting, and proof that it does not write to the runtime's source directory. Respect every upstream license. Design study does not authorize copying source into an Apache-2.0 integration.
