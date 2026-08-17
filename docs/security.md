# Security model

Retinue coordinates local AI runtimes through files and small, explicit
adapters. Its core assumption is that an **agent is an untrusted executor**:
it may misunderstand a task, produce hostile strings, or attempt an operation
outside its baton. Operators retain control of the host, workspace, runtime
credentials, and `org.yaml`.

## SEC-1 Untrusted-agent boundary

Give agents the narrow Retinue MCP surface where possible, not an unrestricted
shell over the coordination workspace. Task titles, acceptance criteria,
notes, refs, receipts, transcript records, and adapter payloads are untrusted
data. Rendering escapes task text. Schema and state-machine validation happen
before canonical mutations. OS-level compromise, a malicious workspace owner,
and direct raw-file rewrites are outside Retinue's application boundary and
must be contained with filesystem permissions, process isolation, and backups.

## SEC-2 Hook authority

`on_claim` may be defined **only in operator-controlled `org.yaml`**. Every
task-card field is data. A task card may contain a key or string named
`on_claim`, `command`, or similar, but the daemon never resolves execution from
it; it selects the holder in the validated roster and executes only that
roster entry's hook. Do not construct shell strings from card content. Prefer
an argv list in `org.yaml`, keep runtime credentials outside the task root, and
review hook changes like code.

Inbound IM publication follows the same boundary. The message body is passed
only as dispatch intent, and the immutable platform message ID is passed as the
idempotency key. The message cannot supply a template selector, holder, URL,
path, token, or executable. Sender authorization comes from an
operator-configured sender-ID to actor-token mapping; the server resolves that
token to the canonical actor. Missing mappings and invalid or disabled tokens
fail closed.

## SEC-3 Append-only chain and legal transitions

`chain` is append-only through Retinue mutation APIs: an update retains every
prior event and appends one receipt-quality event. State changes must follow
the documented transition graph; illegal jumps are rejected before a write.
Each new card starts with a complete state snapshot in its first event, and
later events carry exact before/after values for every changed task-state
field. The shared fold is checked against stored rows throughout the test
suite. Authenticated operators can use the read-only task drift endpoint, and
file-mode operators can use `task audit`, to compare storage with chain-derived
state. Legacy events without these payloads are reported as partially
reconstructible with named unknown fields, never silently completed from the
mutable row. State values entering an event payload reuse the attempts-ledger
refusals for command lines, paths, credential-shaped text, transcripts, and
oversized content.
Because the file format is portable and human-editable, a process with raw
filesystem write access can bypass these controls. Use restricted permissions
and optionally version the data directory with private Git history for
tamper-evident review.

## SEC-4 Holder-only writes

The current holder is the only agent authorized to mutate a card. The MCP
server binds one identity and rejects updates to cards held by another agent.
A holder records handoff by appending the holder change; a relay transports an
already-authorized event and gains no write authority. The local CLI and direct
file access are administrative surfaces without identity authentication, so
do not expose them to untrusted agents.

Execution attempts use a separate append-only ledger and cannot mutate task
fields or the task event chain. Actor-attributed reports require that actor's
bearer and the actor must currently hold the card. Operator sessions remain
operator-attributed. A node token can report only a named node duty for a card
whose holder is assigned to that node; it cannot claim an actor identity. This
lets a managed duty make its own failure visible without receiving an actor
credential or weakening holder-only task writes. Attempt reasons are bounded,
single-line summaries rejected when they resemble a transcript, command line,
absolute path, or credential.

## SEC-5 Read-only panel

The panel accepts GET only. Board, overview, task, and JSON API routes do not
mutate workspace state; other methods return `405 Method Not Allowed`. It has
no authentication and defaults to `127.0.0.1`. Keep that loopback binding, or
place the panel behind an operator-managed authenticated reverse proxy. Treat
task text and metrics as potentially sensitive even though HTML is escaped.

## SEC-6 Data sovereignty and network behavior

Retinue emits no telemetry and the core task bus, panel, daemon, demo, and
exporters can run offline. Exporters read runtime-owned records without
modifying them and reject metrics destinations inside those source
directories. Optional IM adapters contact only the service an operator
explicitly configures; enabling one is an intentional exception to the
no-outbound default.

Canonical Retinue state lives in one operator-chosen directory: `org.yaml`,
`tasks/`, `metrics/`, and `nodes/`. Stop processes and copy that directory to
take the data away or restore it elsewhere. Runtime transcripts and credentials
remain outside it. Retinue has no hosted control plane, remote account, or
mandatory network dependency.

## SEC-7 Interactive-login throttling

Interactive login failures are tracked by both submitted account and ASGI peer
source. Three account failures or ten source failures trigger a one-second
retry gate. Each further failed attempt after its gate expires doubles that
dimension's delay, capped at 60 seconds. A successful password login clears the
account and source counters; 15 quiet minutes also reset them. Demo login checks
the same source gate and does not clear it. Throttled password logins retain the
same generic error detail for existing and unknown accounts, and the gate runs
before database lookup and scrypt verification.

Counters are hashed-key, bounded, in-memory application state. This avoids
turning short-lived abuse signals into durable account records and means a
server-process restart clears all gates; an operator can also wait at most 60
seconds for the current gate, then complete a valid login. In a multi-worker
deployment each process has independent counters, so effective limits increase
with traffic distribution across workers. Deployments needing a strict global
limit must enforce a shared source/account limiter at a trusted gateway or run a
single application worker. The application uses the ASGI peer source and does
not trust a client-supplied forwarding header.

## SEC-8 Explicit node membership and report provenance

Fleet telemetry accepts only an enabled node token bound to the exact admitted
node named by the report. Actor bearers and web sessions cannot impersonate a
node, including admin sessions: admins instead have explicit admission,
retirement, and token-issuance operations. A heartbeat or runtime inventory
never creates a roster row.

Retirement is a soft membership decision. It removes the node from active
fleet read models and disables its node tokens, while preserving the last
heartbeat fields and runtime inventory for audit. The node row records who
admitted and retired it and when. First-token issuance admits an unknown node
atomically for a one-step setup flow, but membership and credentials remain
independent state: admission alone creates no token, token rotation does not
re-admit a retired node, and token disablement does not retire one.

Observer snapshots are discovery inputs, not roster authority. The scheduled
observer import can publish an idempotent proposal card but cannot create or
update executors, imported tasks, nodes, skills, or knowledge sources. Applying
that card is an explicit holder-authorised action; node items reuse the same
admission service as the administrative admission endpoint. The event chain
keeps the authority in `who` and records the performing automation in its
acted-on-behalf-of payload.

## Enforcement summary

| Threat or invariant | Control | Operator responsibility |
|---|---|---|
| Card-injected command | Hook lookup only from validated `org.yaml` | Protect and review `org.yaml` |
| IM-injected command or identity | Message is intent-only; sender maps to a server-validated actor token | Protect the IM app, sender mapping, and token environment |
| Illegal transition or history rewrite | State validation and append-only mutation helper | Restrict raw filesystem writes; retain backups/history |
| Non-holder agent update | Identity-bound MCP rejection | Give agents MCP, not administrative CLI access |
| Failed work hidden outside the truth source | Separate append-only attempt ledger; credential-bound reporters; sanitized reasons | Report every completed external execution |
| Panel-side mutation | GET-only application, escaped HTML, loopback default | Add authentication before any wider exposure |
| Transcript corruption or exfiltration | Read-only exporter and out-of-source atomic metrics | Keep runtime sources and credentials outside task root |
| Vendor telemetry | None in Retinue | Audit and configure optional runtimes/adapters separately |
| Login brute force / scrypt CPU exhaustion | Pre-hash account and source backoff | Use a shared trusted-gateway limiter for strict multi-worker limits |
| Agent-invented fleet members or forged node reports | Explicit node membership and exact node-token scope | Admit/retire nodes through admin operations; protect node tokens |
