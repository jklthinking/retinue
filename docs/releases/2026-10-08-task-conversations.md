# Task conversations — 0.3.0a4

An operator can associate an existing native session's selected visible messages
with a task. **Task workspace → Conversations** displays their original order,
recorded sender and recipient, timestamps, runtime and identity provenance.
Imported history is explicitly labelled. Registered model names are unverified;
missing names remain unknown. Summaries appear separately from source messages.
The English and Chinese static demos show fictional two-round exchanges.

This first delivery is read-only. It does not add a chat transport, automatically
publish sessions, execute transcript text, render external links, or modify task
status. Association and withdrawal currently use the authenticated operator API;
search, export and a UI for managing associations remain future work.

## Enable and associate

Stop writers, back up the database, explicitly migrate schema 27 to **28**, then
start with `RETINUE_TASK_CONVERSATIONS=1` (default: disabled). Schema 28 adds only
`task_conversation_links` and `conversation_protected_sources`. Published schema
27, including quota refresh, is unchanged. Restore the matching pre-migration
database and application version together if rolling back. Disabling the feature
hides its routes and tab; it never removes protection from associated sources.

An administrator's authenticated session can POST
`/api/tasks/{task_id}/conversations/links` with:

```json
{"session_id": 1, "msg_from": 0, "msg_to": 4, "sender_actor_id": "demo-writer", "capture_mode": "imported"}
```

Indexes select an existing retained range at association time; subsequent reads
locate the same contiguous sequence by hashes of visible role, timestamp and text.
Links store identity snapshots and hashes, without a second copy of message bodies.
Identical active selections deduplicate. Withdrawal is immutable; a later explicit
association gets a new link. A task accepts up to 200 links, each at most 80 messages.
Withdraw using POST `/api/tasks/{task_id}/conversations/links/{link_id}/revoke`.
Only the source actor or an administrator can withdraw.

## Access and retention

Selected messages are available to the administrator, contributing source actor,
and a non-viewer human task publisher. Holding a task, reading a shared task card,
or publishing as an agent/channel does not grant transcript access. Human task
publishers see only the selected range, without the source's full-session summary.

The first association permanently protects that source's legacy detail, list,
search, capture, export queue and synchronization entry points. Full-source access
requires its actor or an administrator. Revoking all links, disabling the feature,
or moving the source window does not restore old broad access. Protected-source
synchronization requires its own actor token or an administrator. An owner may
lower privacy even with an older cursor; the stored cursor never moves backwards.
Lowering privacy clears queued capture text and summaries, and prevents new exports.
Previously exported offline files and previously distilled knowledge entries cannot be remotely recalled. Re-enabling full privacy can restore the original selected range if it remains in the sync window; cleared captures remain withheld.

If privacy falls below full, source identity changes, or a selected range leaves
the latest 80-message sync window, messages fail closed with an explicit status.
This version therefore shows available synchronized history rather than promising
permanent transcript retention. At the 4,000-character per-message ingestion limit,
the UI warns that later text may not have been retained. Visible pages revalidate
at most every 15 seconds; failing a read clears previously displayed bodies.
Responses carry `Cache-Control: no-store`. Hidden reasoning and unrestricted tool
output are outside this capture path. Public demo data and screenshots are synthetic.

## Validation

Selected-range, source-identity, sliding-window, withdrawal, role revocation,
legacy capture/search/export and stale-cursor privacy tests cover the access boundary.
Browser checks cover English/Chinese desktop and mobile, literal Markdown escaping,
message order and continued read-only quota refresh. The standard repository gates
and Claude's independent review apply before deployment.

For a new private transcript, its source actor token or an administrator can send
`protect_conversation: true` in the existing `/api/sessions/sync` request while
this feature is enabled. This creates protection in the same transaction as the
source, avoiding an interval of broad legacy access before association. The flag
only narrows access; omitting it or sending false never removes protection. Native
Claude imports exclude `isMeta`, injected system/hook/command blocks, thinking
and tool output before applying text limits. Ambiguous repeated message sequences
fail closed rather than selecting an arbitrary occurrence.
