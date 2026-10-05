import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DelegateTaskForm from "../components/DelegateTaskForm";
import { ModuleContributions, TaskRelationshipGraph } from "../components/TaskCollaborationVisual";
import { LanguageSwitcher, LocaleProvider } from "../i18n";
import type { VisualSnapshot } from "../lib/collaborationVisual";
import { AGENT_ONE, AGENT_TWO, makeTask, mockFetch } from "./helpers";

const ROOT = "task-20260812-001", CHILD = "task-20260812-002";
const actors = [AGENT_ONE, AGENT_TWO];

function snapshot(): VisualSnapshot {
  return {
    task_id: ROOT, root_task_id: ROOT, generated_at: "2026-08-12T10:00:00Z",
    cursor: { event_id: 0, attempt_id: 0 },
    // Deliberately match translation keys: these are operator data, not labels.
    tasks: [
      { ...makeTask({ title: "任务看板" }), parent_task_id: null },
      { ...makeTask({ id: CHILD, title: "执行", holder: "agent-two" }), parent_task_id: ROOT, module: "执行" },
    ],
    delegations: [{
      id: "demo-delegation", parent_task_id: ROOT, child_task_id: CHILD, parent_run_id: null,
      delegated_by: "agent-one", delegated_to: "agent-two", title: "执行", instruction: "任务中心",
      acceptance: ["交付物"], created_at: "2026-08-12T09:00:00Z",
    }],
    runs: [{
      id: "demo-run", task_id: CHILD, parent_task_id: ROOT, actor_id: "agent-two",
      model: "recorded-model", node: "recorded-device", runtime: "recorded-runtime", module: "执行",
      status: "waiting", execution_state: "started", title: "执行", delegation_id: "demo-delegation", parent_run_id: null,
      started_at: "2026-08-12T09:10:00Z", ended_at: null, updated_at: "2026-08-12T09:30:00Z",
      progress: { completed: 1, total: 2, unit: "checks" },
      waiting: { kind: "review", owner: "agent-one", reason: "等待审校", since: "2026-08-12T09:30:00Z" },
      progress_report: { completed: [{ summary: "操作员", refs: ["artifact-demo"] }], remaining: ["复核"], next_action: "下一步", next_owner: "agent-one" },
      latest_note: "完成", refs: ["artifact-demo"], attempt_id: null, lease_term: 1,
    }],
    relationships: [{ id: "demo-return", kind: "review_return", from_task_id: CHILD, to_task_id: CHILD }],
    events: [], attempts: [], recovery: [], coverage: { instrumented_tasks: 1, total_tasks: 2 },
  };
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/?lang=en");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("workspace interface language", () => {
  it("switches graph and memoized relationship labels while preserving task titles and recorded identities", async () => {
    const user = userEvent.setup();
    render(<LocaleProvider><LanguageSwitcher /><TaskRelationshipGraph snapshot={snapshot()} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} /></LocaleProvider>);
    const graph = screen.getByRole("region", { name: "Task collaboration graph" });
    expect(graph).toHaveTextContent("Task branch");
    expect(graph).toHaveTextContent("任务看板");
    expect(graph).toHaveTextContent("recorded-device · recorded-model");
    expect(graph.querySelector(".collab-graph__edge[data-kind='review_return'] text")).toHaveTextContent("Return");

    await user.selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
    expect(screen.getByRole("region", { name: "任务协作关系图" })).toBe(graph);
    expect(graph.querySelector(".collab-graph__edge[data-kind='review_return'] text")).toHaveTextContent("退回");
    expect(graph).toHaveTextContent("任务看板");
    expect(graph).toHaveTextContent("recorded-device · recorded-model");
  });

  it("localizes contribution labels without translating module names, progress claims, waits, or next actions", () => {
    render(<LocaleProvider><ModuleContributions snapshot={snapshot()} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} /></LocaleProvider>);
    const modules = screen.getByRole("region", { name: "Feature module contributions" });
    expect(within(modules).getByRole("heading", { name: "执行" })).toBeInTheDocument();
    expect(modules).toHaveTextContent("Work reported by executor");
    expect(modules).toHaveTextContent("Completed items claimed: 1; not yet accepted.");
    expect(modules.querySelector(".collab-modules__claims")).toHaveTextContent("操作员");
    expect(modules).toHaveTextContent("等待审校");
    expect(modules).toHaveTextContent("下一步");
    expect(modules).toHaveTextContent("artifact-demo");
  });

  it("preserves the canonical delegation request and idempotency key when an uncertain write is retried in another language", async () => {
    const user = userEvent.setup();
    let tries = 0;
    const { calls } = mockFetch({ [`api/tasks/${ROOT}/delegations`]: () => ({ status: ++tries === 1 ? 500 : 200, body: { detail: "synthetic failure" } }) });
    render(<LocaleProvider><LanguageSwitcher /><DelegateTaskForm taskId={ROOT} leaseTerm={1} actors={actors} targets={actors.map(actor => actor.id)} onCreated={vi.fn()} /></LocaleProvider>);
    await user.click(screen.getByText("Delegate subtask"));
    await user.selectOptions(screen.getByLabelText("Executor"), "agent-one");
    await user.selectOptions(screen.getByLabelText("Review model after delivery (optional)"), "agent-two");
    await user.type(screen.getByLabelText("Subtask title"), "Synthetic branch");
    await user.type(screen.getByLabelText("Instructions"), "Read the synthetic source");
    await user.type(screen.getByLabelText("Acceptance criteria (one per line)"), "Include a source reference");
    await user.click(screen.getByRole("button", { name: "Create delegated child card" }));
    await screen.findByText(/synthetic failure/);
    await user.selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    await screen.findByText("已建立委派子卡，等待执行者开工。");
    const bodies = calls.map(call => JSON.parse(String(call.init?.body)));
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toEqual(bodies[0]);
    expect(bodies[0].pipeline.map((stage: { name: string }) => stage.name)).toEqual(["执行", "复核"]);
  });
});
