# Runtime exporters

Exporters observe a runtime's own local records without changing that runtime.

Claude Code and Codex are built-in exporters:

```bash
retinue export claude-code ./workspace \
  --agent claude-1 \
  --source ~/.claude/projects \
  --timezone Asia/Shanghai
```

It writes `workspace/metrics/claude-1.json` atomically. Token totals include
`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, and
`output_tokens`. Repeated transcript events for the same Claude message are
deduplicated by session id plus `message.id`; the component-wise maximum is
counted once. The source directory is opened read-only.

```bash
retinue export codex ./workspace \
  --agent codex-1 \
  --source ~/.codex/sessions \
  --timezone Asia/Shanghai
```

The Codex exporter reads cumulative `total_token_usage` snapshots and assigns
their deltas to local calendar days. Its authoritative total is Codex's
upstream `total_tokens` (`input_tokens + output_tokens`); cached input is a
subset of input and reasoning output is a subset of output, so neither is
added again. Claude Code and Codex expose different runtime-native accounting
semantics. Their absolute totals are intentionally marked non-comparable;
Retinue presents both channels without pretending they share one billing
unit.

Both exporters reject destinations inside their transcript/session source and
replace the destination snapshot atomically. They never write to the runtime's
own record directory.

## Mobile session inbox

The server CLI can also push privacy-scoped conversation snapshots:

```bash
retinue-server sync-sessions \
  --runtime codex \
  --source ~/.codex/sessions \
  --actor codex-1 \
  --token-file /path/to/retinue-token \
  --privacy metadata
```

`metadata` is the default and never copies prompts or responses. `summary`
adds short redacted excerpts; `full` adds at most 80 recent redacted messages.
The same adapter supports `claude-code`. See `docs/session-sync.md` for the
cursor, permission, and privacy contract.
