# Collaboration evidence protocol v1

This server-backed extension uses `TaskEvent.payload_json`; no schema migration
or new database ledger is required. The ordinary task state machine, holder-only
writes, claim leases and separate attempt ledger remain authoritative.

## Authority and delegation

`POST /api/tasks/{task_id}/delegations` creates a queued **child task** and appends
one `delegation` event to its parent in the same transaction. The child creation
event attests the root, parent and delegation IDs; the read projection follows
only matching attestations on both sides. The parent keeps its holder. The child
has its own holder, state, lease and attempts. A delegate cannot write its parent's
card. A delegation queues work; it does not launch a runtime or imply acceptance.

A normal agent retains the existing self-only creation authority. Cross-actor
delegation requires an operator-authored root policy naming **both** the current
holder and target. Only enabled agent identities can be included in the grant.
Members/admins can explicitly delegate to existing enabled actors, including
human actors, as on the ordinary task creation surface. Every agent write also
requires the current holder and a live, matching lease when a lease exists.

Session-index transport agents cannot receive or issue new collaboration
delegations, enter a delegation policy, create a run, consume an execution
permission, or report a prepared run as started. This role is identified only
by an agent ID ending in `-session-sync` or a model beginning with
`session-index-v`, after trimming and case normalization. Runtime `multi`, a
display name mentioning sessions, and an unknown model do not establish that
role: real workers with incomplete model metadata remain eligible. The server
rechecks the current registry at these write boundaries, even for operators
and previously granted policies. Old policies, cards and run evidence remain
readable; this restriction does not rewrite history or affect the existing
session synchronization and live control surfaces.

Operators set or revoke the scope with:

```json
POST /api/tasks/{root_task_id}/collaboration/policy
{
  "allowed_actor_ids": ["agent-a", "agent-b"],
  "max_depth": 3,
  "max_children": 12,
  "idempotency_key": "scope-review-001"
}
```

Only the root accepts policies. An empty list revokes future cross-actor
delegation; it never rewrites prior events or removes existing work. Policies
apply to the entire tree, including descendants. Depth cannot exceed three and
the total number of created descendants cannot exceed twelve, even for an
operator. A policy can reduce those limits. Completed/cancelled children still
count. Revocation and capacity decisions are checked again at write time.

```json
POST /api/tasks/{task_id}/delegations
{
  "delegated_to": "agent-b",
  "title": "Check sources",
  "instruction": "Verify the cited facts",
  "acceptance": ["Every claim has a source"],
  "parent_run_id": null,
  "lease_term": 1,
  "idempotency_key": "source-delegation-001"
}
```

`parent_run_id` is optional; when provided it must name an active execution on
this parent and its current lease. The result is
`{created, delegation: {id, parent_task_id, child_task_id, parent_run_id,
delegated_by, delegated_to, title, instruction, acceptance, created_at}}`.

## Reported execution lifecycle

Start the held task through the ordinary task start/update API first, and report
a run only when actual execution has started. `POST /api/tasks/{id}/runs` takes
`{title, model?, session_ref?, lease_term?, idempotency_key}` and returns
`{created, run}`. There is at most one active run per task lease. Parallel work
must use separately held child cards. A new lease can open a new run; old runs
remain visible with `lease_current: false` and cannot receive new reports.
`lease_live` separately reports current lease liveness. An active run with an
expired lease must be shown as requiring verification even before the sweeper
changes its term; terminal history remains a completed historical observation.

`POST /api/tasks/{id}/runs/{run_id}/events` appends a report:

```json
{
  "status": "waiting",
  "note": "Source selection needs review",
  "progress": {"completed": 1, "total": 3, "unit": "checks"},
  "waiting": {"kind": "review", "owner": "reviewer", "reason": "Confirm the source selection"},
  "refs": [],
  "attempt_id": null,
  "lease_term": 1,
  "idempotency_key": "waiting-review-001"
}
```

Allowed transitions:

- `running -> running | waiting | succeeded | failed | cancelled`
- `waiting -> waiting | running | failed | cancelled`
- Terminal runs never reopen. A resumed execution must first report `running`.

Waiting requires a registered enabled owner and one of `input`, `review`,
`human`, `external`, `dependency`. Repeated identical waiting reports preserve
the original `since`. A running/terminal report clears waiting. Progress is a
bounded numerator and denominator **reported by the executor**, not independent
acceptance or task completion. Omit it when the denominator is unknown. The
ordinary task percentage is never inferred from a run report.

Every terminal run report must link an existing completed attempt on the same
task with exactly the same lease term and outcome. Actor-attributed attempts must
also match the run actor. An attempt cannot be linked to two runs. Report the
attempt with `lease_term` before closing the run. Legacy attempts lacking a term
remain visible but cannot attest a new run. Neither linking an attempt nor
closing a run completes the task; use the ordinary completion or pipeline action.

Models retain their source: `model_source` is `reported`, `registry` or `unknown`.
`reported_by` preserves whether an operator or an agent supplied the observation.
Retinue does not verify a model's identity or infer an unrecorded model version,
or manufacture runs by parsing old chats. Live run writes heartbeat an existing
live lease; they never silently renew an expired lease. Old holder/lease runs are
fenced even when the request omits `lease_term`.
A failed attempt can itself expire the lease under existing failure policy.
Only a terminal receipt supported by that already-recorded same-actor, same-task,
same-term attempt may then close the run while the actor still holds the card.
This narrow evidence-only path never renews the lease or reopens activity; a
changed holder or term is still fenced.

All new text, artifact labels, idempotency keys and session references obey the
existing ledger refusal boundary: no credentials, absolute paths, command lines,
multiline transcripts or control characters. References are safe artifact labels,
not execution commands or unrestricted filesystem links. Bodies reject unknown
fields. Delegation and execution inputs cannot install hooks.

## Projection and incremental reads

`GET /api/tasks/{requested_id}/collaboration` returns:

- `task_id` (the requested card), `root_task_id`, `generated_at`.
- `tasks` (existing summary fields plus `parent_task_id`), `delegations`, `runs`.
- `events` with numeric global `id`, local `seq`, `task_id`, type, author, note,
  timestamp and payload; `attempts` keep their existing fields plus `task_id`.
- `cursor: {event_id, attempt_id}`, and `coverage: {instrumented_tasks,total_tasks}`.
- `can_delegate`, `delegation_targets`, `delegation_reason`,
  `delegation_lease_term`, `delegation_policy`, `can_manage_delegation_policy`.
- `recovery` entries for each card, described below.

The UI must offer only `delegation_targets`. Policy management is available only
when the requested card is the root and the caller is an operator. Capabilities
are advisory projections; every mutation rechecks the current permissions.
Uninstrumented and historical cards have an empty `runs` list, never an inferred
execution. A lack of reports does not prove a task was executed by one agent.

Historical cards still expose their recorded status/holder transitions through
`events.from_status`, `to_status`, `from_holder` and `to_holder`. `relationships`
keeps explicit dependency edges separate from delegations and baton movements.
`related_tasks` contains summaries of direct external dependency endpoints; it
does not add those cards to the delegation tree or imply peer collaboration.
New pipeline stage movement appends a `pipeline_transition` receipt referring
to the original state event. Review returns are identified by that typed receipt,
never by parsing a free-form note. Older untyped movements remain handoffs with
an unknown review intent. No existing event is rewritten.

Delegations, run creation and completed-work claims accept an optional bounded
`module` label. `modules` groups explicitly recorded task/run contributions and
artifact references. Department and task-title similarity do not establish a
product module. Unlabelled history remains `unassigned`, and executor claims
stay `verification: unverified` until independently reviewed.

A delegation may include a two-to-eight-stage `pipeline`. Its first holder must
match `delegated_to`. Every stage holder must be an enabled execution identity
within the existing delegation targets; an agent cannot use a pipeline to evade
the operator's cross-actor allowlist. The normal pipeline holder-only and gate
rules govern all subsequent advancement, return and acceptance actions.

The optional `tools/retinue_supervisor.py` launcher consumes the controlled-run
grant before starting a fixed, operator-configured POSIX argv. Task context is
stdin data, never executable configuration. A stable request key reserves a
durable local journal; repeated requests never relaunch. The supervisor keeps
the current lease alive, stops its owned process group after fencing/timeout,
and persists an exact completed-attempt plus terminal receipt. An uncertain
terminal response can be reconciled with `--reconcile-journal` without execution.
There is no automatic restart or takeover of an older process.

The supervisor keeps raw stdout/stderr local. `result_format: claude-json`
extracts only a strict structured final report from `structured_output` or a
JSON `result` string. `result_format: report-json` reads a configured
`progress_file` only after process exit, suitable for a final-message output
file. Without that final-only mode a structured progress file may supply live
running/waiting reports. Reports cannot claim startup or terminal authority.
Nonzero exit always produces a failed attempt, even when partial findings exist.
Process success does not complete or accept the task. Native cc-connect and
arbitrary direct runtime launches remain outside this optional adapter gate.

Incremental polling uses
`GET /api/tasks/{id}/collaboration/events?after_event_id=0&after_attempt_id=0&limit=100`.
It returns `{root_task_id, events, attempts, cursor, has_more}`. Each ledger has
its own numeric cursor; advance to the returned cursor only after consuming the
page. Continue while `has_more`. New descendants are included automatically. Each read captures ledger ceilings
before refreshing the tree and limits the feed to those ceilings, so a child
created during the read cannot be skipped by advancing past its parent event.
The global IDs provide pagination, while a task's `seq` remains its canonical
history order. Timestamps are display metadata, not a sorting/cursor authority.
Run reports never masquerade as attempt-ledger records.

The projection is a read model and performs no sweeps or state mutations. It uses
the existing authenticated task visibility, and channel-scoped credentials remain
excluded by the centralized channel gate. Full projections are suitable for the
bounded tree; incremental feeds support lightweight change detection.

## Concurrency and idempotency

All collaboration writes lock the root before inspecting mutable policy, holder,
lease, capacity or run state. On SQLite, a no-op `UPDATE id = id` obtains a real
writer lock; `SELECT FOR UPDATE` would not. Loaded ORM snapshots are refreshed
after the lock. Child creation and parent annotation commit or roll back together.
No-op locks do not change `updated_at`, task state or history. The request-scoped
transaction owns the commit.

Creation, policies and run reports use a caller-scoped stable idempotency key.
An exact replay returns `created: false`; a changed payload with the same key is
`409`. Retrying an old operation does not grant new holder or lease authority.
SQLite busy/locked conflicts return `409` so callers can retry the unchanged
request. Other database errors are not swallowed.

## Preparing one blocked branch for retry

Each `recovery` entry has `{task_id, available, reason, action: "prepare_retry",
endpoint, plan}`. `plan` reuses `retry_plan` (`workdir_key`, `resume_session`,
`new_session`, `checkpoint_ref`, `polluted`). Availability requires the holder or
an operator, a blocked card, fulfilled prerequisites and a non-queen stage.

`POST /api/tasks/{id}/collaboration/retry` accepts `{note}` and calls the existing
`retry_task` only for that card. It returns
`{task, retry_plan, execution_started: false}`. The plan is captured before retry
clears failure metadata. It preserves successful siblings and historical runs.
The operation prepares a fresh lease; it does not kill, restart or launch a
runtime, and a checkpoint reference is not a claim of automatic code restoration.
The executor must actually resume work and report a new run. A repeated call while
the card is already doing is refused rather than minting another lease.

## MCP lifecycle and runnable local example

The server MCP bridge exposes `task_collaboration`, `task_delegate`,
`task_run_start`, `task_run_report`, `task_prepare_retry`, and the expanded
`task_attempt` (including lease/session/checkpoint references). Root policy writes
are deliberately absent from the agent MCP surface: an operator sets the scope.
The bridge instructions and tool descriptions describe when reports are evidence.

After an operator grants the two actors the scope above:

1. A calls `task_delegate` and passes `delegation.child_task_id` to B.
2. B actually starts work, calls `task_start`, then `task_run_start`; keep the
   returned `run.id`, `run.started_at` and `run.lease_term`.
3. B calls `task_run_report` with `waiting` and a named owner when blocked on input.
4. B calls `task_run_report` with `running` and measured progress after resuming.
5. B calls `task_attempt` with the same lease term and actual start/end times.
6. B calls `task_run_report` with a terminal status and the returned attempt ID.
7. B completes its child through the usual task or pipeline action when the
   acceptance criteria are met. A independently accepts/finishes the parent.

Run the complete synthetic flow without a server, credentials, or network:

```sh
python scripts/demo_collaboration_flow.py
```

The example uses the same HTTP endpoints as MCP, an isolated temporary database,
and synthetic identities. It demonstrates reporting mechanics, not real model
execution, and deletes its temporary database on exit.

## Device/model snapshots and resumable task context

The existing Actor remains the worker identity: its registered device (`node`),
model and runtime identify the member. Run IDs and session IDs are evidence
references, not additional worker identities. At run creation the server freezes
`node`, `runtime`, `model`, `node_source`, `runtime_source`, `model_source`,
`identity_recorded_at` and `identity_complete` in the append-only start event.
Device/runtime sources are `registry` or `unknown`. An explicitly reported known
model takes precedence over the registered model and is labelled `reported`;
neither is provider-verified. Blank or placeholder models such as
`configured-at-runtime` remain `null`/`unknown`. Later roster edits never rewrite
historical runs. Legacy runs without snapshots keep unknown device/runtime.
Model family/configuration aliases do not make `identity_complete` true.
`reported_by` is the principal who opened the record; `last_reported_by` is the
principal who appended the most recent report. They do not replace the run actor.

`POST /api/tasks/{id}/runs` additionally accepts `runtime_session_id`. This is a
Retinue numeric session ID, not a native session identifier. The existing session
must match the held task, holder, registered device and runtime. Unknown/foreign
or mismatched sessions are refused with the same generic error.
Runtime matching recognizes only the shared explicit inventory aliases
(`openai-codex`/`codex`, `kimi-cli`/`kimi`), while snapshots retain the original
registration. `multi` is never a wildcard. No messages,
summary, title or external session ID are copied into a run or context packet.
A free-form legacy `session_ref` remains supported but is not a verified binding.

Run reports can carry the complete current work snapshot:

```json
{
  "status": "running",
  "note": "Parser implementation ready for review",
  "progress_report": {
    "completed": [{"summary": "Implemented parser", "refs": ["artifact:parser-review"], "revision": "abcdef0123456789"}],
    "remaining": ["Review edge cases"],
    "next_action": "Review parser",
    "next_owner": "reviewer"
  },
  "lease_term": 1,
  "idempotency_key": "parser-progress-001"
}
```

Each completed item is an executor claim, including when it includes an evidence
reference or a revision. Retinue does not fetch or validate external artifact
contents. Versions should be immutable commit IDs or digests; branch names and
mutable links must not be presented as verified revisions. `next_owner` must be
an enabled actor and records intent only; it neither changes the holder nor
grants write authority. Omitting `progress_report` preserves the last report;
supplying it replaces the projected work snapshot while retaining every prior
event. The ordinary task progress and acceptance workflow are unchanged.

`progress_reported_at` changes only for explicit counts or a work snapshot.
`last_report_at` also records status/note reports. `heartbeat_at` is separately
the current task lease heartbeat, and never a progress timestamp. `freshness`
contains `state: unknown|fresh|stale`, `age_seconds`, and the explicit display
threshold `stale_after_seconds: 900`. Age beyond that threshold asks the reader
to revalidate progress; it does not prove a failed executor or change task state.

`GET /api/tasks/{id}/context` and MCP `task_context` produce a read-only packet:

- `version`, `task_id`, `root_task_id`, `generated_at`, and `revision` containing
  ledger ceilings, `task_updated_at`, and a hash of the caller-visible packet.
- Current `task`, bounded `collaboration` tree, `runs`, and `active_runs` whose
  execution is reported started and whose current lease is live.
- `confirmed_progress`: only explicit recorded `done` task transitions, labelled
  `confirmation: recorded_task_state`, `acceptance_verified: false`. This confirms
  a ledger state, not independent quality verification or artifact validation.
- `unverified_claims`, version-labelled `evidence`, sanitized `attempts`, and
  reported `next` actions with `grants_authority: false`.
- `privacy` explicitly stating that no runtime messages or session summaries are
  present. A `session_link` ID is exposed only to the owning actor or an operator,
  and only while the stored session association still matches. Viewer and foreign
  actor packets omit the link. The packet excludes native/free-form session refs.

Task evidence remains shared under the existing authenticated task visibility;
private runtime session contents do not become shared task memory. Anonymous and
channel callers cannot read this endpoint. The context does not sweep, renew a
lease, append events, load artifact file bodies, launch a runtime or sync a chat.
`revision.content_hash` identifies the exact visible packet, including its
freshness labels; numeric event/attempt fields are feed ceilings, not a claim of
an atomic cross-system GitHub/Retinue snapshot.

MCP `orientation` exposes the existing sanitized organisation orientation API.
`task_claim` now retains `start_briefing`, `skill_briefing` and `lease` returned by
HTTP. A new session should read orientation, its task context and acceptance
before continuing work. Retinue is authoritative for task state; referenced code,
PRs or CI results retain their external authority and are not silently copied into
accepted task state.

## One-use permission for integrated execution adapters

An integrated adapter prepares a run with `execution_state: prepared`. It has a
`prepared_at` timestamp and `started_at: null`; although the compatibility status
is `running`, UIs must show it as **waiting to start**, not an executing model.
Existing/manual calls default to `execution_state: started` and keep their
reported-execution semantics. Prepared runs do not enter context `active_runs`.

`POST /api/tasks/{id}/runs/{run_id}/authorize-execution` accepts
`{lease_term: 1, request_key: "launch-request-001"}`. It requires the current
holder, a live matching lease, and an active prepared run. It atomically appends
one `run_execution_authorized` event and returns `allow_execute: true`,
`execution_authorized_at` and `request_key_hash`. Only a hash of the request key
is recorded. When available, the authenticating credential's audit ID is recorded
separately; it is not a worker identity or a bearer token.

Every later request for that run returns `allow_execute: false`, even the same key
after a transport timeout, and even a different token for the same actor. The
adapter must not automatically repeat a launch on an unknown result. The root
write lock refreshes stale ORM snapshots before checking the consumed grant.
A permission does not prove launch success and does not switch a prepared run to
started. After its trusted local launch succeeds, the holder reports
`execution_state: started` with `status: running`; the server requires the
consumed grant and a live current lease before recording `started_at`.

No command is accepted by this endpoint. Only integrated adapters obey the gate;
Retinue cannot prevent a process from bypassing it and starting a CLI directly.
An already reported started run cannot receive a new launch grant. Terminal or
expired runs cannot use this endpoint to renew, restart or regain execution.
