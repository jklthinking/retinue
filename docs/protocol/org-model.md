# Organization model v0.1 / 组织模型 v0.1

Retinue models an organization with four entity types in one `org.yaml`: one `org`, plus lists of `department`, `agent`, and `node` records. Departments group work, agents perform it, and nodes identify the machines where agents run.

Retinue 在单个 `org.yaml` 中使用四类实体：一个 `org`，以及 `department`、`agent`、`node` 三组记录。部门归集工作，agent 执行工作，node 标识 agent 所在机器。

```yaml
org: acme-inc
departments:
  - id: ops
    name: Operations
    lead: ops-captain
  - id: eng
    name: Engineering
agents:
  - id: ops-captain
    dept: ops
    runtime: runtime-a
    model: model-small
    node: office-node
  - id: coder-1
    dept: eng
    runtime: runtime-b
    model: model-large
    node: build-node
nodes:
  - id: office-node
  - id: build-node
```

## Rules

- IDs are globally unique across departments, agents, and nodes.
- IDs contain lowercase letters, digits, and hyphens only.
- Every `agents[].dept` references an existing department.
- Every `agents[].node` references an existing node.
- An optional `departments[].lead` references an agent in that same department.
- An optional `departments[].im_channel` identifies the coordination channel. If absent, implementations may use an org-wide default.
- `runtime` identifies the agent host/runtime; `model` is display-only free text.

A department may route incoming tasks through its lead, but the core protocol does not enforce that policy. The model intentionally avoids personnel and permission hierarchies.
