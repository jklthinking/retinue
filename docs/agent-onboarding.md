# Agent onboarding

Retinue gives any MCP-capable coding agent a small, local coordination surface. The task card on disk is canonical; chat messages are only notifications.

## Connect the server

Install Retinue with the MCP extra — the surface is optional, so a base
install does not carry it — initialize a workspace, then register this stdio
command in the agent's MCP configuration:

```bash
pip install '.[mcp]'
retinue mcp /absolute/path/to/workspace --agent coder-1
```

For clients that accept a JSON command definition:

```json
{
  "mcpServers": {
    "retinue": {
      "command": "retinue",
      "args": ["mcp", "/absolute/path/to/workspace", "--agent", "coder-1"]
    }
  }
}
```

Use an absolute workspace path because MCP clients may start servers from a different working directory. The server uses stdio and makes no network port public.

## Minimal system prompt

```text
Coordinate work with the Retinue MCP server. Start by calling my_tasks. For a
card you hold, move queued or handoff to doing before work. After acceptance
checks pass, update it to done or handoff, then return task_receipt verbatim.
Task cards are canonical. Only the current holder writes a card.
```

The same behavior can be installed as an agent skill from [`../skills/retinue/SKILL.md`](../skills/retinue/SKILL.md).

## Tools

| Tool | Purpose |
|---|---|
| `task_list` | List cards, with optional status or holder filters |
| `ready_work` | List queued cards whose prerequisites are all done |
| `my_tasks` | List cards assigned to the configured agent |
| `task_new` | Create a queued card with priority, acceptance checks, and optional prerequisites |
| `task_dependency_add` / `task_dependency_remove` | Change finish-to-start prerequisites on a held queued card |
| `task_update` | Claim, record progress, edit task fields, block, hand off, or complete a held card |
| `task_receipt` | Render the latest canonical receipt |

`task_update` enforces holder-only writes at the MCP boundary. Protocol transition rules still apply, so invalid skips such as `queued → done` fail without changing the card.

New cards accept `priority` (`urgent|high|medium|low|none`) and an `acceptance` list. Put observable checks on the card instead of relying on prompt memory; a completion note should name the checks that actually passed.

Call `ready_work` when selecting a new card. A prerequisite must be `done`;
queued cards with unfinished prerequisites cannot start or be claimed.

HTTP clients can opt into bounded summary listing with
`GET /api/tasks?page_size=100`. The response contains `items`, `has_more`, and
an opaque `next_cursor`; pass that cursor with the same filters and page size to
request the next page. Summary items omit `chain`, `attempts`, and `reviews`; use
`GET /api/tasks/{task_id}` for the full card. For compatibility, a request that
omits both `page_size` and `cursor` retains the legacy JSON-array response with
full cards.

## Client examples

Claude Code:

```bash
claude mcp add --transport stdio retinue -- \
  retinue mcp /absolute/path/to/workspace --agent claude-1
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.retinue]
command = "retinue"
args = ["mcp", "/absolute/path/to/workspace", "--agent", "codex-1"]
default_tools_approval_mode = "approve"
```

For unattended Codex runs, `default_tools_approval_mode = "approve"` means the local server's tools do not wait for an interactive approval. Set that only after reviewing the command and workspace path; otherwise keep your client's prompt-on-write policy.

Keep the workspace local for the MVP. Multi-machine synchronization belongs to a later integration stage.

## Connecting to a server instead of a workspace

The examples above use the file-backed shape, where the agent's own process reads and writes
task files. If you run the server, agents talk to it through `server.mcp_bridge` instead, so
the board stays the single place a card exists and holder-only writes are enforced by the
server rather than by convention between processes.

```bash
claude mcp add --scope user retinue \
  -e RETINUE_SERVER_URL=http://127.0.0.1:9219 \
  -e RETINUE_TOKEN_FILE=/path/to/agent-tokens/<agent>.token \
  -- /path/to/venv/bin/python -m server.mcp_bridge
```

```bash
codex mcp add retinue \
  --env RETINUE_SERVER_URL=http://127.0.0.1:9219 \
  --env RETINUE_TOKEN_FILE=/path/to/agent-tokens/<agent>.token \
  -- /path/to/venv/bin/python -m server.mcp_bridge
```

Prefer `RETINUE_TOKEN_FILE` over `RETINUE_TOKEN`. An MCP configuration is a file, and for
some clients it lives inside a project directory, so a configuration naming a path can be
read and shared while one carrying the token cannot. Keep the token directory at `700` and
the files at `600`; the bridge refuses to start rather than falling back to `RETINUE_TOKEN`
if the named file is missing or empty, so a mistyped path is reported instead of silently
using a different identity. `RETINUE_TOKEN` remains for environments where no such file is
available.

Give each agent its own token. The identity on the token is the identity recorded in the
chain, so a shared token makes every card say the same thing about who did the work, and
revoking one agent's access would revoke all of them.

Point the command at your deployed checkout rather than a working tree. An agent that
launches the bridge from a tree under development will follow that tree's code, which is the
same coupling the deployment guidance warns about for the services themselves.
