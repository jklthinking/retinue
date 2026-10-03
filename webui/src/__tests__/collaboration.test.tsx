import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskCollaboration from "../components/TaskCollaboration";
import DelegateTaskForm from "../components/DelegateTaskForm";
import TaskContextPanel from "../components/TaskContextPanel";
import type { CollaborationSnapshot, TaskContextSnapshot } from "../lib/collaboration";
import { AGENT_ONE, AGENT_TWO, ME, makeTask, mockFetch } from "./helpers";

const ROOT = "task-20260812-001";
const CHILD = "task-20260812-002";
const endpoint = "api/tasks/" + ROOT + "/collaboration";

function snapshot(overrides: Partial<CollaborationSnapshot> = {}): CollaborationSnapshot {
  return {
    task_id: ROOT, root_task_id: ROOT, generated_at: "2026-08-12T10:00:00Z",
    cursor: { event_id: 4, attempt_id: 0 },
    tasks: [
      { ...makeTask({ status: "doing" }), parent_task_id: null },
      { ...makeTask({ id: CHILD, title: "核查资料", holder: "agent-two", status: "blocked" }), parent_task_id: ROOT },
    ],
    delegations: [{
      id: "delegation-1", parent_task_id: ROOT, child_task_id: CHILD, parent_run_id: null,
      delegated_by: "agent-one", delegated_to: "agent-two", title: "核查资料",
      instruction: "核查三项结论并补充出处", acceptance: ["每项结论均可核查"],
      created_at: "2026-08-12T09:00:00Z",
    }],
    runs: [{
      id: "run-1", task_id: CHILD, parent_task_id: ROOT, actor_id: "agent-two", model: "model-b",
      status: "waiting", title: "核查资料", delegation_id: "delegation-1", parent_run_id: null,
      started_at: "2026-08-12T09:10:00Z", ended_at: null, updated_at: "2026-08-12T09:30:00Z",
      progress: { completed: 1, total: 3, unit: "项" },
      waiting: { kind: "review", owner: "agent-one", reason: "请确认引用范围", since: "2026-08-12T09:30:00Z" },
      latest_note: "已核查首项，等待确认范围", refs: ["artifact-source-notes"],
      attempt_id: null, lease_term: 1, lease_current: true,
    }],
    events: [
      { id: 3, task_id: CHILD, seq: 2, type: "run_update", who: "agent-two", did: "第二个事件", at: "2026-08-12T09:00:00Z", payload: {} },
      { id: 2, task_id: CHILD, seq: 1, type: "run_start", who: "agent-two", did: "第一个事件", at: "2026-08-12T10:00:00Z", payload: {} },
    ],
    attempts: [], recovery: [], coverage: { instrumented_tasks: 1, total_tasks: 2 },
    can_delegate: false, delegation_targets: [], can_manage_delegation_policy: false,
    ...overrides,
  };
}

function view(taskId = ROOT) {
  return <TaskCollaboration taskId={taskId} me={ME} actors={[AGENT_ONE, AGENT_TWO]} />;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("任务协作现场", () => {
  it("展示真实委派、计数进度、等待责任及事件序号，拒绝按时间猜顺序", async () => {
    mockFetch({ [endpoint]: () => ({ body: snapshot() }) });
    const user = userEvent.setup();
    render(view());
    expect(await screen.findByText("核查三项结论并补充出处")).toBeInTheDocument();
    expect(screen.getByText("已完成 1/3 项")).toBeInTheDocument();
    expect(screen.getByText("等待审校 · 责任人 Agent One")).toBeInTheDocument();
    expect(screen.getByText("model-b")).toBeInTheDocument();
    await user.click(screen.getByText("查看分支事件证据（2）"));
    const evidence = screen.getByText("查看分支事件证据（2）").parentElement!;
    const rows = within(evidence).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("第一个事件");
    expect(rows[1]).toHaveTextContent("第二个事件");
    await user.click(screen.getByRole("button", { name: "执行进展" }));
    expect(screen.getByRole("button", { name: /Agent Two.*等待中/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("无结构化记录时明确空态，不把任务历史推断为Agent执行", async () => {
    mockFetch({ [endpoint]: () => ({ body: snapshot({
      delegations: [], runs: [], events: [], coverage: { instrumented_tasks: 0, total_tasks: 1 },
    }) }) });
    render(view());
    expect(await screen.findByText("尚未记录结构化协作")).toBeInTheDocument();
    expect(screen.queryByText("model-b")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "准备重试此分支" })).not.toBeInTheDocument();
  });

  it("旧服务未提供接口时明确未接入，并保留原页面可用", async () => {
    mockFetch({ [endpoint]: () => ({ status: 404, body: { detail: "Not found" } }) });
    render(view());
    expect(await screen.findByText("协作记录尚未接入")).toBeInTheDocument();
  });

  it("旧租约运行标为待核实，未知模型和进度不填假值", async () => {
    const data = snapshot();
    data.runs[0] = { ...data.runs[0], lease_current: false, model: null, progress: null };
    mockFetch({ [endpoint]: () => ({ body: data }) });
    render(view());
    expect(await screen.findByText("状态待核实")).toBeInTheDocument();
    expect(screen.getByText("尚未上报可计数进度")).toBeInTheDocument();
    expect(screen.getByText("模型未知")).toBeInTheDocument();
    expect(screen.getByText(/租约已过期或已更换/)).toBeInTheDocument();
  });

  it("租约term相同但已经过期时不再显示执行中", async () => {
    const data = snapshot();
    data.runs[0] = { ...data.runs[0], status: "running", lease_current: true, lease_live: false, waiting: null };
    mockFetch({ [endpoint]: () => ({ body: data }) });
    const user = userEvent.setup();
    render(view());
    expect(await screen.findByText("状态待核实")).toBeInTheDocument();
    const overview = screen.getByLabelText("已记录的协作概况");
    expect(overview).toHaveTextContent("0 执行中");
    expect(screen.getByText(/租约已过期或已更换/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "执行进展" }));
    expect(screen.getByRole("button", { name: /Agent Two.*状态待核实/ })).toBeInTheDocument();
  });

  it("刷新失败时保留上次快照并报告失败，恢复后清除告警", async () => {
    let fail = false;
    mockFetch({ [endpoint]: () => fail ? { status: 503, body: { detail: "offline" } } : { body: snapshot() } });
    const user = userEvent.setup();
    render(view());
    await screen.findByText("核查三项结论并补充出处");
    fail = true;
    await user.click(screen.getByRole("button", { name: "刷新协作记录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保留上次");
    expect(screen.getByText("核查三项结论并补充出处")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "刷新协作记录" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("切换任务后忽略上一任务迟到的请求", async () => {
    const requests = new Map<string, (response: Response) => void>();
    vi.stubGlobal("fetch", vi.fn((url: string) => new Promise<Response>((resolve) => requests.set(url, resolve))));
    const rendered = render(view());
    await waitFor(() => expect(requests.has(endpoint)).toBe(true));
    const otherId = "task-20260812-009";
    rendered.rerender(view(otherId));
    const otherEndpoint = "api/tasks/" + otherId + "/collaboration";
    await waitFor(() => expect(requests.has(otherEndpoint)).toBe(true));
    const next = snapshot({ task_id: otherId, root_task_id: otherId });
    next.tasks[0] = { ...next.tasks[0], id: otherId, title: "新任务资料" };
    await act(async () => requests.get(otherEndpoint)!(new Response(JSON.stringify(next))));
    expect(await screen.findByText("新任务资料")).toBeInTheDocument();
    const stale = snapshot();
    stale.tasks[0].title = "旧任务迟到";
    await act(async () => requests.get(endpoint)!(new Response(JSON.stringify(stale))));
    expect(screen.queryByText("旧任务迟到")).not.toBeInTheDocument();
    expect(screen.getByText("新任务资料")).toBeInTheDocument();
  });

  it("只向选中子卡准备重试，并明确尚未启动执行", async () => {
    const data = snapshot({ recovery: [{
      task_id: CHILD, available: true, reason: "", action: "prepare_retry",
      endpoint: "/api/untrusted", plan: {},
    }] });
    const { calls } = mockFetch({
      [endpoint]: () => ({ body: data }),
      ["api/tasks/" + CHILD + "/collaboration/retry"]: () => ({ body: { execution_started: false } }),
    });
    const user = userEvent.setup();
    render(view());
    const retry = await screen.findByRole("button", { name: "准备重试此分支" });
    expect(retry).toBeDisabled();
    await user.type(screen.getByLabelText("重试原因"), "引用范围已经确认");
    await user.click(retry);
    expect(await screen.findByText(/尚未开始执行/)).toBeInTheDocument();
    const writes = calls.filter((call) => call.init?.method === "POST");
    expect(writes).toHaveLength(1);
    expect(writes[0].url).toBe("api/tasks/" + CHILD + "/collaboration/retry");
    expect(JSON.parse(String(writes[0].init?.body))).toEqual({ note: "引用范围已经确认" });
  });

  it("只读用户没有创建或恢复写入入口", async () => {
    mockFetch({ [endpoint]: () => ({ body: snapshot({
      can_delegate: true, delegation_targets: ["agent-two"],
      recovery: [{ task_id: CHILD, available: true, reason: "", action: "prepare_retry", endpoint: "", plan: {} }],
    }) }) });
    render(<TaskCollaboration taskId={ROOT} me={{ ...ME, readonly: true }} actors={[AGENT_ONE, AGENT_TWO]} />);
    await screen.findByText("核查三项结论并补充出处");
    expect(screen.queryByText("委派子任务")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "准备重试此分支" })).not.toBeInTheDocument();
  });
});

describe("委派子卡表单", () => {
  it("执行者受服务端授权名单限制，失败重试复用幂等键", async () => {
    let failed = true;
    const { calls } = mockFetch({
      ["api/tasks/" + ROOT + "/delegations"]: () => failed
        ? { status: 503, body: { detail: "暂时不可用" } }
        : { body: { created: true, delegation: { child_task_id: CHILD } } },
    });
    const created = vi.fn();
    const user = userEvent.setup();
    render(<DelegateTaskForm taskId={ROOT} leaseTerm={1} actors={[AGENT_ONE, AGENT_TWO]} targets={["agent-two"]} onCreated={created} />);
    await user.click(screen.getByText("委派子任务"));
    expect(screen.queryByRole("option", { name: /Agent One/ })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("执行者"), "agent-two");
    await user.type(screen.getByLabelText("子任务标题"), "核查资料");
    await user.type(screen.getByLabelText("委派要求"), "核查三个结论");
    await user.type(screen.getByLabelText("验收标准（每行一条）"), "出处可核查");
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    expect(await screen.findByText("委派失败：暂时不可用")).toBeInTheDocument();
    failed = false;
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    expect(await screen.findByText("已建立委派子卡，等待执行者开工。")).toBeInTheDocument();
    const bodies = calls.map((call) => JSON.parse(String(call.init?.body)));
    expect(bodies).toHaveLength(2);
    expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key);
    expect(bodies[0].delegated_to).toBe("agent-two");
    expect(created).toHaveBeenCalledTimes(1);
  });
});

describe("任务委派权限设置", () => {
  it("只读与非根任务不展示策略写入入口", async () => {
    const rootData = snapshot({ can_manage_delegation_policy: true });
    mockFetch({
      [endpoint]: () => ({ body: rootData }),
      ["api/tasks/" + CHILD + "/collaboration"]: () => ({ body: snapshot({
        task_id: CHILD, can_manage_delegation_policy: false,
      }) }),
    });
    const rendered = render(<TaskCollaboration taskId={ROOT} me={{ ...ME, readonly: true, role: "viewer" }} actors={[AGENT_ONE, AGENT_TWO]} />);
    await screen.findByText("核查三项结论并补充出处");
    expect(screen.queryByText(/Agent 委派权限/)).not.toBeInTheDocument();
    rendered.rerender(<TaskCollaboration taskId={CHILD} me={ME} actors={[AGENT_ONE, AGENT_TWO]} />);
    await screen.findByText("核查三项结论并补充出处");
    expect(screen.queryByText(/Agent 委派权限/)).not.toBeInTheDocument();
  });

  it("策略初始不默认勾选Agent，空名单保存可撤销，失败重试复用幂等键", async () => {
    let failed = true;
    const { calls } = mockFetch({
      [endpoint + "/policy"]: () => failed
        ? { status: 503, body: { detail: "暂时不可用" } }
        : { body: { ok: true } },
      [endpoint]: () => ({ body: snapshot({
        can_manage_delegation_policy: true, delegation_policy: null,
      }) }),
    });
    const user = userEvent.setup();
    render(view());
    const summary = await screen.findByText(/Agent 委派权限/);
    await user.click(summary);
    const checkboxes = screen.getAllByRole("checkbox");
    checkboxes.forEach((checkbox) => expect(checkbox).not.toBeChecked());
    await user.click(screen.getByRole("button", { name: "保存此任务的委派权限" }));
    expect(await screen.findByText("保存失败：暂时不可用")).toBeInTheDocument();
    failed = false;
    await user.click(screen.getByRole("button", { name: "保存此任务的委派权限" }));
    expect(await screen.findByText("已关闭 Agent 跨成员委派，已有子任务保留。")).toBeInTheDocument();
    const writes = calls.filter((call) => call.init?.method === "POST");
    expect(writes).toHaveLength(2);
    const bodies = writes.map((call) => JSON.parse(String(call.init?.body)));
    expect(bodies[0].allowed_actor_ids).toEqual([]);
    expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key);
  });
});

describe("本次设备模型与接棒信息", () => {
  it("设备和模型取本次记录，不被当前名册覆盖，并区分来源与报告者", async () => {
    const data = snapshot();
    data.runs[0] = {
      ...data.runs[0], node: "device-a", runtime: "runtime-a", model: "model-old",
      node_source: "registry", runtime_source: "registry", model_source: "reported",
      identity_complete: true, identity_recorded_at: "2026-08-12T09:10:00Z",
      reported_by: { kind: "user", id: "operator-a" },
      last_reported_by: { kind: "agent", id: "agent-two" },
    };
    mockFetch({ [endpoint]: () => ({ body: data }) });
    const user = userEvent.setup();
    render(<TaskCollaboration taskId={ROOT} me={ME} actors={[AGENT_ONE, {
      ...AGENT_TWO, node: "device-changed", model: "model-changed", runtime: "runtime-changed",
    }]} />);
    expect(await screen.findByText("device-a · model-old")).toBeInTheDocument();
    expect(screen.getByText("操作员 operator-a")).toBeInTheDocument();
    expect(screen.getByText("Agent agent-two")).toBeInTheDocument();
    expect(screen.getAllByText("当次登记")).toHaveLength(2);
    expect(screen.getByText("本次上报")).toBeInTheDocument();
    expect(screen.queryByText(/model-changed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/device-changed/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "执行进展" }));
    expect(screen.getByRole("button", { name: /device-a · model-old.*Agent Two.*等待中/ })).toBeInTheDocument();
  });

  it("旧身份缺字段时保留未知；新心跳不会掩盖过期的结构化进展", async () => {
    const data = snapshot();
    data.runs[0] = {
      ...data.runs[0], node: null, runtime: null, model: null, identity_complete: false,
      progress_report: {
        completed: [{ summary: "初稿已整理", refs: ["draft-reference"], revision: "revision-2" }],
        remaining: ["复核原始出处"], next_action: "确认引用范围后开始复核", next_owner: "agent-one",
      },
      progress_reported_at: "2026-08-12T08:00:00Z",
      last_report_at: "2026-08-12T10:00:00Z",
      heartbeat_at: "2026-08-12T10:00:00Z",
      freshness: { state: "stale", age_seconds: 7200, stale_after_seconds: 900 },
    };
    mockFetch({ [endpoint]: () => ({ body: data }) });
    render(<TaskCollaboration taskId={ROOT} me={ME} actors={[AGENT_ONE, {
      ...AGENT_TWO, node: "current-device", model: "current-model",
    }]} />);
    expect(await screen.findByText("设备未知 · 模型未知")).toBeInTheDocument();
    expect(screen.getByText("资料不完整")).toBeInTheDocument();
    expect(screen.queryByText(/current-model/)).not.toBeInTheDocument();
    const progress = screen.getByRole("region", { name: "结构化进展" });
    expect(progress).toHaveTextContent("初稿已整理");
    expect(progress).toHaveTextContent("draft-reference");
    expect(progress).toHaveTextContent("revision-2");
    expect(progress).toHaveTextContent("复核原始出处");
    expect(progress).toHaveTextContent("下一棒：Agent One");
    expect(progress).toHaveTextContent("进展待更新");
    expect(progress).toHaveTextContent("心跳不代表工作推进");
    expect(progress).toHaveTextContent("待验收");
  });

  it("只读接棒包区分完成记录和声明，携带成果版本并且不授予下一棒权限", async () => {
    const data = snapshot();
    const context: TaskContextSnapshot = {
      version: 1, task_id: CHILD, root_task_id: ROOT, generated_at: data.generated_at,
      revision: { event_id: 4, attempt_id: 1, task_updated_at: data.generated_at, content_hash: "revisionhash" },
      task: data.tasks[1], collaboration: { tasks: data.tasks, delegations: data.delegations, coverage: data.coverage },
      runs: data.runs, active_runs: data.runs,
      confirmed_progress: [{ task_id: ROOT, status: "done", event_id: 3, at: data.generated_at, who: "agent-one", confirmation: "recorded_task_state", acceptance_verified: false }],
      unverified_claims: [{ task_id: CHILD, run_id: "run-1", actor_id: "agent-two", completed: [{ summary: "声明资料已整理", refs: ["source-notes"], revision: "rev-two" }], progress: null, reported_at: data.generated_at, verification: "unverified", freshness: { state: "fresh", age_seconds: 0, stale_after_seconds: 900 } }],
      evidence: [{ task_id: CHILD, run_id: "run-1", summary: "引用清单", refs: ["source-notes"], revision: "rev-two", source: "executor_report", verification: "unverified" }],
      next: [{ task_id: CHILD, run_id: "run-1", action: "继续核查来源", owner: "agent-one", remaining: ["确认首条引用"], waiting: data.runs[0].waiting, source: "executor_report", grants_authority: false }],
      privacy: { contains_private_conversations: false, contains_session_summaries: false, session_links: "owner_or_operator_only" },
    };
    const { calls } = mockFetch({
      [endpoint]: () => ({ body: data }),
      ["api/tasks/" + CHILD + "/context"]: () => ({ body: context }),
    });
    const user = userEvent.setup();
    render(<TaskCollaboration taskId={ROOT} me={{ ...ME, readonly: true, role: "viewer" }} actors={[AGENT_ONE, AGENT_TWO]} />);
    await user.click(await screen.findByRole("button", { name: /接棒信息/ }));
    expect(await screen.findByText("以下仅表示任务卡已标记完成，成果内容仍需验收。")).toBeInTheDocument();
    const claims = screen.getByRole("region", { name: "待验收的执行者声明" });
    expect(claims).toHaveTextContent("声明资料已整理");
    const evidence = screen.getByRole("region", { name: "接棒成果引用" });
    expect(evidence).toHaveTextContent("rev-two");
    expect(evidence).toHaveTextContent("待核验");
    expect(screen.getByText("下一步来自执行者建议，不改变持棒或授权范围。")).toBeInTheDocument();
    expect(screen.getByText("继续核查来源")).toBeInTheDocument();
    expect(calls.some((call) => call.init?.method === "POST")).toBe(false);
  });

  it("切换接棒任务后不会显示上一任务的迟到上下文", async () => {
    const requests = new Map<string, (response: Response) => void>();
    vi.stubGlobal("fetch", vi.fn((url: string) => new Promise<Response>((resolve) => requests.set(url, resolve))));
    const user = userEvent.setup();
    const rendered = render(<TaskContextPanel taskId={ROOT} actors={[]} revision="1" />);
    await user.click(screen.getByRole("button", { name: /接棒信息/ }));
    const oldEndpoint = "api/tasks/" + ROOT + "/context";
    await waitFor(() => expect(requests.has(oldEndpoint)).toBe(true));
    rendered.rerender(<TaskContextPanel taskId={CHILD} actors={[]} revision="1" />);
    const newEndpoint = "api/tasks/" + CHILD + "/context";
    await waitFor(() => expect(requests.has(newEndpoint)).toBe(true));
    await act(async () => requests.get(oldEndpoint)!(new Response(JSON.stringify({ version: 1, task_id: ROOT, task: { title: "旧任务泄漏" }, evidence: [], next: [], unverified_claims: [], confirmed_progress: [] }))));
    expect(screen.queryByText("旧任务泄漏")).not.toBeInTheDocument();
    await act(async () => requests.get(newEndpoint)!(new Response(JSON.stringify({ detail: "Not found" }), { status: 404 })));
    expect(await screen.findByText("当前服务尚未提供接棒信息。")).toBeInTheDocument();
  });
});

it("已准备但未实际启动的执行不能计入执行中", async () => {
  const data = snapshot();
  data.runs[0] = {
    ...data.runs[0], status: "running", execution_state: "prepared", waiting: null,
    started_at: null, prepared_at: "2026-08-12T09:00:00Z",
    execution_authorized_at: "2026-08-12T09:05:00Z", lease_live: true, lease_current: true,
  };
  mockFetch({ [endpoint]: () => ({ body: data }) });
  const user = userEvent.setup();
  render(view());
  expect(await screen.findByText("待启动")).toBeInTheDocument();
  expect(screen.getByLabelText("已记录的协作概况")).toHaveTextContent("0 执行中");
  await user.click(screen.getByRole("button", { name: "执行进展" }));
  expect(screen.getByRole("button", { name: /Agent Two.*待启动/ })).toBeInTheDocument();
});
