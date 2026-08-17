# RETINUE data governance

RETINUE uses three complementary persistence layers:

- **SQLite** stores operational facts: actors, tasks, append-only task events, nodes, skills, actor-skill bindings and their event chain, knowledge-source metadata, pipeline templates, accounts and privacy-scoped session indexes.
- **Obsidian Markdown** stores human-readable inputs, research, decisions, project context and reusable knowledge. YAML Properties and wikilinks provide a lightweight document index.
- **References** (`refs`, `source`, `responds_to`, `related`, `evidence`) connect the layers without copying private正文 or session transcripts.

The web **数据整理** page reads `/api/data-catalog`. It reports storage layers, row counts, quality checks, JSON contracts, recommendations and privacy boundaries. It intentionally does not expose note bodies, prompts, private memory, passwords or tokens.

## Quality indicators

The catalog checks acceptance coverage, holder validity, department and evidence references, actor runtime coverage, and event-chain completeness. A task is not considered “healthy” merely because it has a status; its acceptance and evidence should be queryable.

## JSON fields

Some fields remain JSON because they are protocol-shaped or variable-length: `tasks.acceptance_json`, `tasks.refs_json`, `tasks.pipeline_json`, `skills.owners_json`, `skills.source_snapshot_json`, and `skill_binding_events.payload_json`. They are still validated at the API boundary and should not be populated by arbitrary frontend data.

## Obsidian boundary

Keep raw input immutable, produce structured output with `responds_to`, and promote only reviewed notes to canonical knowledge. Do not turn Obsidian into a second task database; use RETINUE for ownership, state transitions, permissions and receipts.