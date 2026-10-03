# Live Sessions design boundary

Status: implemented for the `0.3.0a1` release candidate. Stage 3/3.5 adds
expiring, idempotent control envelopes plus generation-fenced `tell`, bounded
`peek`, and permissioned soft `interrupt`.

## Product promise

Retinue can reach the runtime session doing the work without replacing the
task card, terminal multiplexer, or native runtime. The card remains the
durable work ledger. Live Sessions add an ephemeral observation and control
layer between actor identity and the environment where an agent happens to
run.

```text
Task --held by--> Actor --runs--> LiveSession --located at--> Endpoint
```

The identities are deliberately separate:

- `Actor` is the durable, authorized Retinue participant;
- `LiveSession` is one running Codex, Claude, or other runtime instance;
- `SessionEndpointObservation` is an untrusted, privacy-bounded probe fact;
- `SessionEndpointBinding` is that instance's current backend location.

The existing `RuntimeSession` model keeps its v1 meaning: a privacy-scoped,
read-only transcript snapshot. A live session may later reference such a
snapshot, but neither model replaces the other.

## Existing contracts

Live Sessions extend the existing Hub and node package. They do not introduce
a second daemon or a message broker.

```text
runtime_probe  = what this admitted node can run
session_probe  = what is running now
session_relay  = which authorized actions the node can execute
```

`runtime_probe` and session-sync v1 remain backward compatible. A node token
continues to represent exactly one admitted infrastructure node and cannot
claim actor identity or mutate task state.

## Discovery and endpoint identity

Stage 1 enumerates tmux panes with argv-only subprocess calls. It reports
stable tmux IDs, an opaque endpoint ID, a generation fence, sanitized display
labels, executable basenames, and a basename-only cwd hint. It does not report
the tmux socket path, environment variables, command-line arguments, terminal
content, credentials, or transcripts.

Binding evidence is ranked:

```text
explicit tmux metadata
        > native runtime metadata
        > process observation
        > terminal heuristic
```

An unrecognized pane is `unknown`, never `idle`. Automatic discovery grants no
control capability. `retinue-node session-bind` writes operator-chosen tmux
user options to one exact pane only after verifying the foreground runtime; it
never sends terminal input. The Hub independently checks the explicit
metadata, enabled Actor, Actor runtime, optional task holder, occupant evidence,
and endpoint generation before materializing a binding. Every later control
action must match that generation to prevent delivery into a reused pane.

## Two ledgers

The task event ledger records durable work semantics: claim, progress,
handoff, block, done, and accept. A separate append-only control event ledger
records message, attach, lease, delivery, acknowledgement,
failure, and expiry.

The product rule is concise:

> Talk can be direct. Work requires a card.

A question or correction may be transient control. A request for a review,
implementation, or other durable deliverable needs a task (or child task), a
holder, and acceptance before the relay notifies the target runtime.

## Relay boundary

The Hub authorizes an actor request and creates an expiring, idempotent control
envelope. The target node may pull only envelopes for its own node identity,
verify the exact endpoint generation, execute the narrow verb, and append an
outcome. It cannot reinterpret the request as permission to change a task.

Native message protocols remain preferred. The P0 tmux adapter pastes text
through a buffer and sends Enter separately; `send-keys` carries no message
text. Tell additionally requires the operator-set `codex-prompt` input-mode
contract and a fresh occupant/generation check. Unknown input mode, a bare
shell, or a reused pane fails closed. Once leased, text is never automatically
returned to the queue after an unconfirmed delivery. Peek captures only an
explicitly requested bounded tail and redacts credentials and private home
paths on the node before acknowledgement.

P0 interruption means only a permissioned soft `C-c`, limited to an operator
or the target session's owning Actor, with a 30-second envelope lifetime and a
fresh generation/occupant check on the Node. Terminating a process, killing a
pane, or cleaning a worktree is outside P0.

## Privacy and retention

The node keeps machine-local endpoint details. The Hub stores an opaque
endpoint ID and non-sensitive display metadata, not an absolute tmux socket
path. Peek is bounded and redacted on the node; full scrollback is not stored
by default.

A future relay may retain a message body only until acknowledgement plus a
short retry window. Its append-only event history keeps metadata, payload
hash, and result, not a default transcript copy. Task receipts never become a
chat transcript.

## P0 supported surface

The first conformance target is Linux, tmux, and Codex. Other runtimes may be
identified by read-only discovery but are not advertised as controllable
until their adapter contract tests pass.

The first product loop remains deliberately small:

```text
retinue sessions
retinue-node session-bind
retinue tell
retinue peek
retinue interrupt
retinue jump
retinue-node live-cycle
```

The release candidate implements discovery, Node report ingestion, explicit
binding metadata, an authorization-filtered read model, `tell`, `peek`, and
permissioned soft `interrupt` across API, CLI, MCP, and Web. The optional
`live` enrollment duty runs one reconcile-and-relay cycle every two seconds on
Linux. It cannot terminate a process, kill a pane, renew a task lease, or
publish externally.
