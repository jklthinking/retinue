# Fresh install to a closed task loop

This walkthrough starts with a trusted source checkout and ends with a completed
card, its work record, and its append-only receipt chain visible on the board. It
uses only loopback networking. The file, server, and offline Feishu commands and
request bodies below are exercised by `tests/test_closed_loop.py`.

## Install

Python 3.10 or newer is required. Install the base package for the offline file
mode, plus the server extra for the authenticated in-app mode:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[server]'
```

The server's browser application is built from the checked-in frontend. A source
checkout does not contain the generated `server/static/` directory, so a source
installation also needs Node.js and this one-time build:

```bash
(cd webui && npm ci && npm run build)
```

This packaging gap does not affect file mode. A future release artifact should
bundle the built browser application so server adopters do not need Node.js.

## Offline file mode: publish, record work, and inspect the board

Create a new local workspace and publish one card:

```bash
retinue init ./retinue-local --org local-demo
retinue task new ./retinue-local/tasks \
  --id task-20300101-001 \
  --title "Publish, work, and show the result" \
  --created-by operator --holder worker-1 \
  --priority high \
  --acceptance "the completed card and its chain appear on the board"
```

The assigned worker claims it, records an artifact reference, and completes it:

```bash
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --status doing --who worker-1 --note "claimed from the file bus"
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --ref artifact:result-note --who worker-1 \
  --note "recorded the result reference"
retinue task update ./retinue-local/tasks/task-20300101-001.yaml \
  --status done --who worker-1 \
  --note "acceptance checked; work complete"
retinue receipt ./retinue-local/tasks/task-20300101-001.yaml
retinue task lint ./retinue-local/tasks
retinue panel ./retinue-local
```

Open <http://127.0.0.1:8787/>, find the card in `done`, and open it. The task
thread shows creation, claim, result recording, and completion in order. The
receipt command prints the same latest transition.

Two boundaries are deliberate here. The local CLI is an operator/admin surface;
it records `--who` but does not authenticate that identity. Give an untrusted
agent `retinue mcp ./retinue-local --agent worker-1` instead, because MCP enforces
holder-only writes. Also, file-mode `refs` are portable strings: Retinue displays
them but does not check that an artifact exists or copy it into the workspace.

## Server mode: publish in the app, work as an agent, and link a session

Create the first administrator. Omit `--password` so the value is entered without
placing it in shell history:

```bash
python -m server.main --data-dir ./retinue-server-data init-admin \
  --username operator --actor operator
python -m server.main --data-dir ./retinue-server-data serve
```

Open <http://127.0.0.1:9219/> and sign in. In the administration view, onboard an
agent with actor ID `worker-1`, runtime `codex`, and node `local-node`; save the
one-time agent token outside the Retinue data directory. Then use **Task board →
New task → Assign executor** to publish:

- Title: `Publish, work, and show the result`
- Holder: `worker-1`
- Priority: `high`
- Acceptance: `the completed card and its chain appear on the board`

Copy the generated task ID and provide the one-time token to the worker through
its secret configuration. The following HTTP calls are also what an MCP bridge
does under that agent principal:

```bash
export RETINUE_AGENT_TOKEN='<one-time-agent-token>'
export RETINUE_TASK_ID='<task-id-from-the-board>'

curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"status":"doing","note":"claimed through the agent API"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"

curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data "{\"runtime\":\"codex\",\"external_id\":\"closed-loop-session\",\"title\":\"Closed-loop work record\",\"privacy\":\"metadata\",\"cursor\":1,\"message_count\":2,\"task_id\":\"$RETINUE_TASK_ID\"}" \
  http://127.0.0.1:9219/api/sessions/sync
```

The session response contains its numeric `id`. Record it and complete the card:

```bash
export RETINUE_SESSION_ID='<session-id-from-the-response>'

curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data "{\"progress\":80,\"refs\":[\"session:$RETINUE_SESSION_ID\",\"artifact:result-note\"],\"note\":\"recorded the result and linked work session\"}" \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"

curl --fail --request POST \
  --header "Authorization: Bearer $RETINUE_AGENT_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"status":"done","note":"acceptance checked; work complete"}' \
  "http://127.0.0.1:9219/api/tasks/$RETINUE_TASK_ID/update"
```

Refresh the task board and open the card. It is in `done`, progress is 100%, the
result references are visible, and the drawer shows the four-event chain. The
Sessions view shows the work record linked to the same task. A token issued for a
different agent receives `403 holder-only-writes` on these updates.

The metadata request above proves collection without sending prompts or message
bodies. To collect a real local Codex or Claude Code session, run the documented
node session sync instead; linking an automatically discovered session to a task
that already exists currently requires the API. The Sessions view can instead
create a new card from an unlinked session and link those two records.

## Feishu/Lark: publish chat intent through deterministic dispatch

An addressed Feishu/Lark text message can publish a server-backed pipeline card.
The adapter sends the text as `intent` and the immutable platform `message_id` as
`idempotency_key` to `POST /api/dispatch`. Retinue's existing deterministic
matcher selects from the operator-defined pipeline templates; the adapter does
not send `template_name`, use a model, or interpret any message text as a command,
path, executable, holder, token, or URL.

Create `org.yaml` in the adapter workspace with this neutral configuration:

```yaml
org: local-demo
departments:
  - id: work
    name: Work
agents:
  - id: worker-1
    dept: work
    runtime: local
    node: local-node
nodes:
  - id: local-node
adapters:
  feishu:
    enabled: true
    chat_id_env: RETINUE_TEST_CHAT
    profile_env: RETINUE_TEST_PROFILE
    self_mention_id_env: RETINUE_TEST_SELF
    allow_from_env: RETINUE_TEST_SENDERS
    dispatch_url_env: RETINUE_SERVER_URL
    dispatch_senders_env: RETINUE_FEISHU_PUBLISHERS
```

`allow_from_env` remains the separate bot-to-bot receipt allowlist.
`dispatch_senders_env` names an environment variable containing a JSON object
whose keys are Feishu/Lark sender IDs and whose values are bearer-token
environment-variable names. Issue a dedicated token for the registered
actor who owns that sender mapping, then keep the token outside `org.yaml`:

```bash
python -m server.main --data-dir ./retinue-server-data issue-token \
  --actor publisher --label feishu-intake

export RETINUE_TEST_CHAT=offline-chat
export RETINUE_TEST_PROFILE=offline-profile
export RETINUE_TEST_SELF=self-placeholder
export RETINUE_TEST_SENDERS=receipt-sender-placeholder
export RETINUE_SERVER_URL=http://127.0.0.1:9219
export RETINUE_FEISHU_PUBLISHERS='{"publisher-sender-placeholder":"RETINUE_PUBLISHER_TOKEN"}'
export RETINUE_PUBLISHER_TOKEN='<actor-bound-token>'
```

The server must contain that token's actor plus at least one valid pipeline
template with operator-controlled stages and match terms. Feed an addressed
message to the same stdin path used by `retinue feishu listen`:

```bash
retinue feishu receive ./retinue-im <<'JSON'
{"event":{"sender":{"sender_id":{"open_id":"publisher-sender-placeholder"}},"message":{"message_id":"message-publication","content":"{\"text\":\"Prepare a lesson handout\"}","mentions":[{"id":{"open_id":"self-placeholder"}}]}}}
JSON
```

The first delivery prints `published` and replies with the actor attribution,
new task ID, and a direction to open the board. Delivering the identical event
again prints `duplicate`, replies with the same existing task ID, and leaves one
card on the server board. A changed request reusing that message ID is rejected
without changing the existing card.

If no pipeline matches, the adapter creates nothing and tells the sender to
rephrase the request with a configured pipeline name or match term, or ask the
operator to add one. An unmapped sender receives an unauthorised reply and never
reaches dispatch. Chat or group membership is not identity: attribution comes
from the operator's sender-to-token mapping and the Retinue actor bound to that
token; a missing mapping, missing token, invalid token, disabled actor, or
unaddressed message fails closed.

Canonical receipt relays still use the existing two-line protocol and their
independent sender allowlist. For example, after a file-backed card exists:

```bash
retinue feishu receive ./retinue-im <<'JSON'
{"event":{"sender":{"sender_id":{"open_id":"receipt-sender-placeholder"}},"message":{"message_id":"message-doing","content":"{\"text\":\"【任务回执】task-20300101-002 Relayed IM task\\n状态：queued → doing　持棒：worker-1 → worker-1　备注：claimed from the addressed IM receipt\"}","mentions":[{"id":{"open_id":"self-placeholder"}}]}}}
JSON
```

The credential-free tests use `MemoryTransport` and an injected local caller of
the real `dispatch_intent`, so duplicate, unmatched, unauthorised, and hostile
message cases traverse `FeishuAdapter.receive` without an IM account or network.
The existing closed-loop receipt test continues to exercise the CLI stdin
normalisation path.

For a live Feishu/Lark path, the operator must supply and protect their own
enterprise application, app credentials, bound `lark-cli` profile, chat ID, bot
mention ID, receipt sender allowlist, publisher sender mapping, Retinue Server
URL, and actor-bound tokens. Retinue supplies event normalization, addressed
mention checks, fail-closed attribution, deterministic and idempotent dispatch,
canonical server card storage, actionable replies, receipt relaying, and the
board.
