# Task state protocol v0.2 / 任务状态协议 v0.2

A task is one YAML file under `tasks/`; its filename is `<id>.yaml`. The file is the machine-readable source of truth, while an IM receipt is its human-visible projection.

每项任务在 `tasks/` 下对应一个 YAML 文件，文件名为 `<id>.yaml`。任务卡是机器可读的事实源，IM 回执是面向人的投影。

## Task card schema

```yaml
id: task-20260719-001
title: Extract dashboard skeleton
created_by: boss
dept: eng
priority: high
acceptance:
  - overview renders the runtime snapshot
  - pytest passes
depends_on:
  - task-20260719-000
status: doing
holder: coder-1
blocked_reason: null
chain:
  - who: lead-1
    did: split task
    at: "2026-07-19T02:02:00.000000Z"
    from_status: queued
    to_status: doing
    from_holder: lead-1
    to_holder: coder-1
next: lead-1
refs: []
```

Required fields are `id`, `title`, `created_by`, `status`, `holder`, `chain`, and `refs`. New cards also emit `priority`, `acceptance`, and `depends_on`; readers continue to accept legacy cards without those fields. `dept`, `blocked_reason`, and `next` are optional or nullable. Task IDs match `task-YYYYMMDD-NNN`; all entity IDs use lowercase letters, digits, and hyphens.

`priority` is one of `urgent`, `high`, `medium`, `low`, or `none`. `acceptance` is a list of non-empty, observable completion criteria; it may be empty while a task is being triaged. Agents should read it before claiming work and cite the checks they ran in the completion note.

`priority` 取 `urgent`、`high`、`medium`、`low` 或 `none`。`acceptance` 是非空字符串组成的可观测验收标准列表；任务分诊阶段可以为空。Agent 接棒前应先读取它，完成回执中应写明实际运行的验收检查。

`chain` is append-only. Every change to `priority`, `acceptance`, `dept`,
`refs`, `progress`, `blocked_reason`, `holder`, or `status` appends one event
with `who`, `did`, `at`, and a versioned `payload.changes` map. Each changed
field records exact `before` and `after` values. The creation event records an
initial value for all eight fields, so folding the ordered chain recreates the
complete current task state without reading the mutable card or database row.
Status and holder remain duplicated in the `from_*` and `to_*` receipt fields.
A note-only update may append an event with an empty changes map. Existing
events must never be edited or removed.

```yaml
payload:
  state_version: 1
  changes:
    acceptance:
      before: [draft is reviewable]
      after: [review is approved]
```

Server storage uses the existing `TaskEvent.payload_json` column for this map;
the column was introduced for review events, so this protocol addition needs
no database migration. Review payloads retain their existing event-specific
shape and do not mutate folded task state.

An automation acting for an authorising identity keeps `who` as that identity
and adds execution provenance to the same event payload:

```yaml
acted_on_behalf_of:
  authorising_identity: owner
  performing_agent: roster-import
```

The fold returns these ordered attributions alongside folded state. Receipts
and the task panel render the performer as acting for the authority. Direct
human events omit this object; legacy events remain valid. This is an
extension of the task chain, not a second audit ledger.

Roster discoveries use ordinary queued cards with a `roster_proposal` creation
event. Its payload lists each proposed entity, action, stable identity, and
fields that will be created, without including the observer input location.
Only the card holder can authorise the dedicated apply action. Rejection uses
the ordinary `queued -> cancelled` transition. Applying a proposal uses the
ordinary `queued -> doing -> done` path and records acted-on-behalf-of
provenance on the application event.

The fold reports `complete`, `partial`, or `invalid`. A pre-payload chain can
usually prove status and holder from the receipt columns, but the other fields
remain named in `unknown_fields`; the fold never fills them from the current
row. Later evidence may make an individual field known, but it does not
fabricate the missing earlier history. Broken before/after continuity is
reported as invalid evidence.

Operators can run the read-only drift checks through
`retinue task audit <card.yaml>` for file mode or authenticated
`GET /api/tasks/{task_id}/drift` for a live server database. A result is
`in_sync` only when every field is reconstructible and agrees with storage;
legacy uncertainty is `partial`, and a proved disagreement is `drift`.

Values copied into state payloads cross the same refusal boundary used by the
attempts ledger: command-line-shaped text, absolute paths, credential-shaped
text, multiline content, control characters, and oversized strings are not
accepted. This prevents the richer event record from becoming a second secret
or transcript sink.

New writers store `at` as fixed-width UTC with microsecond precision, for
example `2026-07-19T02:02:00.000000Z`. Readers also accept legacy ISO 8601
values with a local offset and minute precision, such as
`2026-07-19T10:02+08:00`; reading or appending to a legacy card does not rewrite
its existing events. Chain order is the YAML list order, and server-backed
cards use `TaskEvent.seq` as the authority; consumers must not sort a task's
events by `at`.

Quality-control comments and replies are also append-only events on the same
task chain. A `review_comment` has a stable event ID and may point at one
artifact reference. A `review_reply` names its parent comment, records one of
`accepted`, `needs_info`, or `declined`, and may cite acceptance evidence.
These events keep status and holder unchanged, including after `done` or
`cancelled`; they are review history, never a second task-state machine.

## Execution attempts

Server-backed cards have a related, append-only attempt ledger. An attempt is
not a task-chain event: the task chain is the ordered authority for state and
holder mutations, while an attempt records the outcome of work performed under
that state. Keeping the ledgers separate means reporting success, failure, or
cancellation cannot consume a task-event sequence, change a state, move the
baton, or create a transition outside the table below. Neither ledger permits
an existing record to be edited or removed.

Each completed attempt records an attempt sequence, reporter identity and kind,
start and end timestamps, and one of `succeeded`, `failed`, or `cancelled`.
Failures require a single-line reason of at most 240 characters and may include
an integer exit status. The reason must contain no transcript, command line,
absolute path, or credential-shaped text. Task detail responses expose attempts
in their stored sequence order; clients must not sort them by timestamps.

An actor bearer may report only its own attempt while it holds the card. An
authenticated operator reports as an operator, even when that account is bound
to an actor; actor attribution always requires the actor bearer. A node token
may report only as its bound node, must name a duty, and may attach the record
only to a task held by an actor assigned to that node. This narrow exception is
safe because it can append only a sanitized outcome record: it has no task,
chain, holder, or actor write authority.

`blocked_reason` is required and non-empty when `status` is `blocked`; it must be null otherwise.

## Inter-card dependencies and ready work

Retinue supports one dependency kind: a finish-to-start `blocks` edge. If card
A lists card B in `depends_on`, B blocks A until B reaches `done`. This one kind
answers the operational question without claiming semantics for parentage,
provenance, or partial completion that the protocol does not enforce. The
inverse (`blocks`) is derived and indexed in server storage, so it is queryable
without scanning every card.

A card is ready when it is `queued` and every card in `depends_on` is `done`.
Dependencies gate `queued -> doing` and dispatch-hall claims; they do not add a
state. The `blocked` state remains an execution-time condition on work that has
already started. Adding an edge that would form a cycle is rejected with the
card IDs in the cycle.

Dependencies connect cards. Pipeline stages and queen approvals remain the
ordered, intra-card baton flow and are unchanged. The `next` field also remains
unchanged: it is an optional successor-holder hint naming an actor, not a task,
so converting it into a dependency edge would conflate routing with work
ordering.

Only `done` satisfies a prerequisite. Cancelling a prerequisite while any
nonterminal card depends on it is refused and names those dependents; the
operator must first remove or resolve the edges. This makes cancellation an
explicit graph edit instead of silently stranding queued work. File-mode lint
also rejects cycles, missing cards, and a cancelled prerequisite of an active
dependent.

## State machine

```text
queued  -> doing | cancelled
doing   -> handoff | blocked | done | cancelled
handoff -> doing | cancelled
blocked -> doing | cancelled
done / cancelled -> terminal
```

Any edge not listed above is invalid. A holder-only change does not alter status, but it still requires a new chain event.

## Holder-only writes

Only the current `holder` may mutate a task card. A different actor must first receive the baton through a holder transition recorded in the prior holder's receipt. Coordinators and read-only panels may inspect cards but may not rewrite them on an agent's behalf. The MCP boundary enforces this rule; wider conflict-copy detection in `task lint` is deferred to the multi-machine phase.

只有当前 `holder` 可以修改任务卡。其他人或 agent 必须先由上一棒通过带回执的 holder 变更正式交棒。协调器与只读面板可以读取任务卡，但不能代替 holder 改卡。MCP 边界已强制执行该规则；`task lint` 的跨机冲突副本检测留待多机阶段。


## Multi-stage pipelines and queen gates

Server-backed task cards may add an ordered `pipeline` and a zero-based
`pipeline_stage`. Each stage contains `name`, `holder`, and `gate`:

- `auto`: the holder starts work, then calls `stage-done` to pass the baton.
- `review`: the same forward path, plus `stage-reject` while `doing` to return
  to the immediately preceding stage.
- `queen`: a human-held approval node. Entering it parks the task in `handoff`
  and opens one pending approval; only an approval decision may move the card.

Pipeline cards still use the six-state graph and append exactly one task event
for each baton movement. Generic task updates may start/resume a non-queen
stage (`queued|handoff|blocked -> doing`), block active work
(`doing -> blocked`), or report progress. They may not reassign the holder,
complete/handoff the card, or move a queen gate; those movements belong to
`stage-done`, `stage-reject`, and approval endpoints. Progress resets to zero
whenever the baton enters a stage and reaches 100 only at terminal delivery.

Queen-gate holders must be registered human actors. Card links use separate,
decision-bound bearer tokens for approve and reject. An HTTP `GET` only renders
an escaped, no-cache confirmation page; the explicit confirmation `POST`
settles the approval with an atomic `pending -> approved|rejected` guard. A
rejection that lands on another queen gate opens a fresh approval. Pending
approvals left behind by a legitimate flow movement are voided so stale links
cannot seize the baton later.

流程卡仍遵守六态状态机与 append-only 事件链。普通更新只能开工、恢复、阻塞或
上报进度；交棒、打回和人工审批裁决必须走专用接口。每次进入新节点进度归零；
人工审批门由人类 actor 持棒，飞书链接先展示确认页，GET 永不产生状态变更。协议标识仍是 queen，以免破坏已有流水线。
## IM receipt

The canonical receipt is at least two lines and uses literal machine-readable status and holder IDs:

```text
【任务回执】<id> <title>
状态：<old-status> → <new-status>　持棒：<old-holder> → <new-holder>　备注：<one-line-note>
```

For the initial creation event, the missing old value is rendered as `—`.
