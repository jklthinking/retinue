# Board snapshots (M0)

Static projection of the active task board for read-only fallback when the API
process is unavailable. The snapshot never writes to the database.

## Files

Under `<data-dir>/snapshots/`:

- `board.json` — deterministic projection (`id`, `title`, `status`, `holder`,
  `progress`, `priority`), per-status counts, and `generated_at` derived from
  the latest task `updated_at` when tasks exist.
- `board.html` — the same content as a self-contained page (no scripts, no
  external assets).

## CLI

```bash
python -m server.main --data-dir DIR snapshot
```

Exit status is `0` on success. Re-running with no board changes leaves the files
byte-identical. There is no built-in scheduler; operators or an external cron
invoke the command.
