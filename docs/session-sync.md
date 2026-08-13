# Runtime session sync v1

Retinue can present existing Claude Code and Codex conversations in one
mobile-friendly inbox. The runtime remains authoritative: sync reads JSONL
records without modifying them, and no web action is allowed to write back to
the transcript directory.

## Privacy levels

| Level | Synced data | Default |
|---|---|---|
| `metadata` | Runtime, actor, node, timestamps, message count, generic title | Yes |
| `summary` | Metadata plus a short locally generated, redacted excerpt | No |
| `full` | Summary plus at most 80 recent redacted user/assistant messages | No |

The exporter removes common credentials, bearer tokens, private-key blocks,
and home-directory paths before network transfer. Operators should still treat
`summary` and `full` as sensitive and expose the Retinue Server only through
their authenticated private network.

## Sync contract

`POST /api/sessions/sync` uses the bearer token's actor identity. An agent token
cannot upload or read another actor's sessions. A unique session is identified
by `(actor_id, runtime, external_id)`.

Each snapshot includes a monotonically increasing integer cursor. Retinue:

1. creates an unseen session;
2. treats the same cursor and content as an idempotent retry;
3. rejects an older cursor;
4. rejects different content at the same cursor;
5. permits an explicit privacy-level change at the same cursor.

Changing from `full` to `summary` or `metadata` removes the previously stored
message body on the next successful sync.

## Local command

```bash
retinue-server sync-sessions \
  --runtime codex \
  --source ~/.codex/sessions \
  --actor material-maker \
  --url http://127.0.0.1:9219 \
  --token-file /path/to/retinue-token \
  --privacy metadata
```

Use `~/.claude/projects` with `--runtime claude-code`. Schedule the command on
the machine that owns the runtime records. The command writes no state into
the source directory and relies on the server cursor for idempotency.

## Scope of v1

The web inbox is read-only. Copying a transcript does not make a vendor session
resumable. Mobile follow-up requires a separate, authenticated runtime relay
that routes a message to the original machine and native session identifier;
that relay is intentionally outside v1.


## Multi-node recap archive

Kimi supports both the current sessions/agents/main/wire.jsonl layout and the
legacy sessions/wire.jsonl layout. Hermes reads only top-level transcript
JSONL files. scripts/sync_runtime_sessions.py accepts multiple runtime/path
sources and is intended to run every 30 minutes with summary privacy. It makes
no model calls and consumes no LLM tokens.

When a redacted summary has been idle for 45 minutes, Retinue queues an
idempotent recap capture. The vault bridge exports it to
40_Commons/话题归档/<来源>/<YYYY-MM>/. Repeated synchronization never
overwrites an exported, unchanged recap.


Federated collection keeps bearer credentials only on the Retinue host. Remote
nodes emit locally redacted summary rows over the existing SSH trust path; they
never receive Retinue API tokens. The Windows vault bridge also uses its
existing SSH trust path to list and settle queued recap files.
