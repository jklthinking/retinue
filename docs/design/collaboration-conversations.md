# Proposal: a conversation timeline for each task

Status: product design; this document does not add a chat transport or publish
private conversations. All examples below are fictional.

## What an owner should be able to see

Open a task and choose **Conversations / 沟通记录** beside its existing
collaboration and progress views. Read the actual questions, replies, review
feedback and decisions between workers, without asking a worker to paste logs
into another messenger. The Sessions inbox remains the source-conversation
archive. A task timeline provides the curated, explicitly linked view.

Each entry shows the recorded sender and recipient, timestamp, task, runtime,
model and their attribution sources. A user-role message submitted by another
agent must display that agent's name, rather than the ambiguous label “You”.
Unknown sender/model metadata stays unknown. Registry names or worker reports
are not provider-verified model identities.

For example:

| Time | Route | Kind | Visible text |
|---|---|---|---|
| 09:10 | Agent A → Agent B | Question | How should the reading counter handle idle tabs? |
| 09:12 | Agent B → Agent A | Reply | Pause active time after the idle threshold. |
| 09:15 | Agent A → Agent B | Clarification | How should a scanned page be counted? |
| 09:18 | Agent B → Agent A | Reply | Show viewed pages and time when text is unavailable. |
| 09:20 | Agent A | Recorded decision | Use estimated text counts and a separate page metric. |

## Readable UI

- A chronological timeline with a compact summary above it. Separate actual
  messages from generated summaries and executor-reported decisions.
- Filters for worker, model, question/reply/review/decision, time and search.
  Thread replies preserve their source order and link back to the question.
- Expandable Markdown message bodies, copy and owner-authorized Markdown
  export. Escape unsafe HTML and use the existing safe Markdown renderer.
- A source link opens the corresponding session and message range. Show
  “live report”, “imported transcript”, “metadata only” or “content withheld”,
  plus collection time and any truncation. Imported history must not look live.
- Review and decision entries can point to artifacts or accepted task events.
  A message saying “done” never completes a task or proves an accepted result.
- An owner-visible home activity card shows the latest discussion and decision,
  with an **Open conversation** action; it does not expose private excerpts to
  every authenticated task reader.

## Data and permissions

Keep long message bodies out of the shared append-only task ledger. Reuse the
session archive for redacted, explicitly opted-in user/assistant messages.
Add a dedicated conversation read model with references to session snapshots,
message ranges and reply parents. Record immutable sender/recipient identity
snapshots, attribution source, capture mode and timestamps when linked.

An owner/operator or an appropriately scoped session owner may link a session
or selected message range to a task. This is a separate permission boundary:
being a task holder or reader does not grant another worker's transcript access.
The default audience is the task owner and the contributing session owner;
broader sharing requires an explicit grant. Validate source/task ownership and
the message range on every link, read and export. A link is a pointer, never a
command or an instruction to a runtime.

Use a stable source snapshot/message key for idempotent import and chronological
pagination. Keep the latest source cursor and explicit coverage so gaps and
truncated history are visible. When source privacy changes from full to metadata,
or sharing is revoked, hide bodies and excerpts immediately, including exports
and cached summaries. Retain only the permitted audit metadata; immutable task
receipts must not preserve a secret body that was later withdrawn.

Capture visible prompts, answers and collaboration receipts. Do not capture
hidden reasoning, environment dumps or unrestricted tool logs. Redact credentials
before transfer and retain the existing private-network authentication boundary.
The public demo and screenshots use synthetic examples exclusively.

## Delivery sequence and acceptance

1. Link existing Sessions to tasks; provide a read-only timeline, correct sender
   labels, source navigation and Markdown export for authorized readers.
2. Let authenticated collaboration adapters append structured question/reply
   receipts only after delivery/result evidence exists. A queued request is shown
   as queued; an uncertain delivery stays uncertain and must not invent a reply.
3. Add decision links and the owner-only home activity view. Incremental updates
   reuse cursors; reading records never launches or resumes a vendor session.

Acceptance: a two-worker, two-round exchange renders four source messages with
correct attribution and order; retransmission creates no duplicates; an imported
exchange is labelled imported; unauthorized readers cannot see bodies; privacy
withdrawal removes cached excerpts and exports; a failed transport never appears
as a completed exchange. Existing task state and holder-only permissions remain
authoritative.
