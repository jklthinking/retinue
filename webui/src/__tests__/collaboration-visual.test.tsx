import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskCollaboration from "../components/TaskCollaboration";
import DelegateTaskForm from "../components/DelegateTaskForm";
import TaskContextPanel from "../components/TaskContextPanel";
import { ModuleContributions, TaskRelationshipGraph, WorkerTimeline } from "../components/TaskCollaborationVisual";
import { visibleModules, type VisualSnapshot } from "../lib/collaborationVisual";
import type { TaskContextSnapshot } from "../lib/collaboration";
import { AGENT_ONE, AGENT_TWO, ME, makeTask, mockFetch } from "./helpers";

const ROOT = "task-20260812-001", CHILD = "task-20260812-002", REVIEW = "task-20260812-003";
const endpoint = `api/tasks/${ROOT}/collaboration`;
const actors = [AGENT_ONE, AGENT_TWO];

function snapshot(): VisualSnapshot {
  return {
    task_id: ROOT, root_task_id: ROOT, generated_at: "2026-08-12T10:00:00Z", cursor: { event_id: 4, attempt_id: 0 },
    tasks: [
      { ...makeTask({ title: "改进阅读器", status: "doing" }), parent_task_id: null },
      { ...makeTask({ id: CHILD, title: "修复错误提示", holder: "agent-two", status: "doing" }), parent_task_id: ROOT, module: "阅读器" },
      { ...makeTask({ id: REVIEW, title: "审校文档", holder: "agent-one", depends_on: [CHILD] }), parent_task_id: ROOT, module: "文档" },
    ],
    delegations: [
      { id: "d-1", parent_task_id: ROOT, child_task_id: CHILD, parent_run_id: null, delegated_by: "agent-one", delegated_to: "agent-two", title: "修复错误提示", instruction: "消除成功响应的错误提示", acceptance: ["成功时不显示失败"], created_at: "2026-08-12T09:00:00Z" },
      { id: "d-2", parent_task_id: ROOT, child_task_id: REVIEW, parent_run_id: null, delegated_by: "agent-one", delegated_to: "agent-one", title: "审校文档", instruction: "核对返回码说明", acceptance: [], created_at: "2026-08-12T09:00:00Z" },
    ],
    runs: [{
      id: "run-1", task_id: CHILD, parent_task_id: ROOT, actor_id: "agent-two", model: "model-b", node: "device-b", runtime: "runtime-b",
      status: "waiting", execution_state: "started", title: "修复错误提示", delegation_id: "d-1", parent_run_id: null,
      started_at: "2026-08-12T09:10:00Z", ended_at: null, updated_at: "2026-08-12T09:30:00Z", progress: { completed: 1, total: 2, unit: "项" },
      waiting: { kind: "review", owner: "agent-one", reason: "等复核成功分支", since: "2026-08-12T09:30:00Z" },
      progress_report: { completed: [{ summary: "已修复成功分支", refs: ["artifact-1"], revision: "revision-1" }], remaining: ["复核错误分支"], next_action: "复核返回值", next_owner: "agent-one" },
      latest_note: "成功分支已修复并待复核", refs: ["artifact-1"], attempt_id: null, lease_term: 1,
    }],
    relationships: [
      { id: "h-1", kind: "handoff", from_task_id: CHILD, to_task_id: CHILD, from_actor: "agent-two", to_actor: "agent-one", at: "2026-08-12T09:25:00Z", source: "task_event" },
      { id: "r-1", kind: "review_return", from_task_id: CHILD, to_task_id: CHILD, from_actor: "agent-one", to_actor: "agent-two", note: "缺少错误分支验证", at: "2026-08-12T09:30:00Z", source: "task_event" },
    ],
    events: [], attempts: [], recovery: [], coverage: { instrumented_tasks: 1, total_tasks: 3 }, can_delegate: false,
  };
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("每个任务的可视化协作", () => {
  it("无任务标签但有明确run模块时不预建空未标注贡献", () => {
    const data = snapshot();
    data.tasks = [data.tasks[0]];
    data.runs = [{ ...data.runs[0], task_id: ROOT, module: "发布统筹" }];
    const modules = visibleModules(data);
    expect(modules).toHaveLength(1);
    expect(modules[0]).toMatchObject({ name: "发布统筹", source: "explicit", task_ids: [ROOT], run_ids: ["run-1"] });
    render(<ModuleContributions snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} />);
    const region = screen.getByRole("region", { name: "任务功能模块贡献" });
    expect(region).toHaveTextContent("发布统筹");
    expect(region).not.toHaveTextContent("模块未标注");
  });

  it("同任务多模块执行与完成项模块分别归属，不把同一声明重复到其它模块", () => {
    const data = snapshot();
    data.tasks = [data.tasks[0]];
    const initial = { ...data.runs[0], task_id: ROOT, module: "解析" };
    initial.progress_report = { completed: [{ module: "文档", summary: "解析契约已记录", refs: ["artifact-contract"], revision: "revision-contract" }], remaining: [], next_action: null, next_owner: null };
    data.runs = [initial, { ...initial, id: "run-ui", module: "界面", progress_report: null }];
    const modules = visibleModules(data);
    expect(modules.map((module) => module.name)).toEqual(["解析", "文档", "界面"]);
    expect(modules.find((module) => module.name === "解析")!.completed).toEqual([]);
    expect(modules.find((module) => module.name === "界面")!.completed).toEqual([]);
    expect(modules.find((module) => module.name === "文档")!.completed).toEqual([
      { task_id: ROOT, run_id: "run-1", actor_id: "agent-two", summary: "解析契约已记录",
        refs: ["artifact-contract"], revision: "revision-contract", at: null, verification: "unverified" },
    ]);
    render(<ModuleContributions snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} />);
    expect(screen.getAllByText("解析契约已记录")).toHaveLength(1);
  });

  it("旧run身份有模块缺项时保留未标注，无run任务也仍保留", () => {
    const data = snapshot();
    data.tasks = [data.tasks[0], { ...data.tasks[2], module: null }];
    data.runs = [
      { ...data.runs[0], task_id: ROOT, module: "发布统筹" },
      { ...data.runs[0], task_id: ROOT, id: "run-legacy", module: null, progress_report: null },
    ];
    const unknown = visibleModules(data).find((module) => module.source === "unassigned")!;
    expect(new Set(unknown.task_ids)).toEqual(new Set([ROOT, REVIEW]));
    expect(unknown.run_ids).toEqual(["run-legacy"]);
  });

  it("显式任务模块保留原归属，run的其它模块不会覆盖任务登记", () => {
    const data = snapshot();
    data.tasks = [data.tasks[1]];
    data.runs = [{ ...data.runs[0], module: "复核", progress_report: null }];
    const modules = visibleModules(data);
    expect(modules).toEqual([
      expect.objectContaining({ name: "阅读器", task_ids: [CHILD], run_ids: [] }),
      expect.objectContaining({ name: "复核", task_ids: [CHILD], run_ids: ["run-1"] }),
    ]);
  });

  it("以明确证据绘制分支、依赖、交棒和退回，点击节点联动指令与成果", async () => {
    mockFetch({ [endpoint]: () => ({ body: snapshot() }) });
    const user = userEvent.setup();
    render(<TaskCollaboration taskId={ROOT} actors={actors} me={ME} layout="wide" />);
    const graph = await screen.findByRole("region", { name: "任务协作关系图" });
    expect(graph.querySelectorAll(".collab-graph__edge[data-kind='delegation']")).toHaveLength(2);
    expect(graph.querySelectorAll(".collab-graph__edge[data-kind='dependency']")).toHaveLength(1);
    expect(graph.querySelectorAll(".collab-graph__edge[data-kind='handoff']")).toHaveLength(1);
    expect(graph.querySelectorAll(".collab-graph__edge[data-kind='review_return']")).toHaveLength(1);
    await user.click(within(graph).getByRole("button", { name: /协作任务：审校文档/ }));
    expect(screen.getByRole("article", { name: "选中分支的执行证据" })).toHaveTextContent("核对返回码说明");
    await user.click(within(graph).getByRole("button", { name: /协作任务：修复错误提示/ }));
    const evidence = screen.getByRole("article", { name: "选中分支的执行证据" });
    expect(evidence).toHaveTextContent("消除成功响应的错误提示");
    expect(evidence).toHaveTextContent("等复核成功分支");
    expect(evidence).toHaveTextContent("artifact-1");
    expect(evidence).toHaveTextContent("revision-1");
  });

  it("旧任务没有run仍显示状态历史和接棒入口，自由文本不制造委派", async () => {
    const data = snapshot();
    data.tasks = [data.tasks[0]]; data.runs = []; data.delegations = []; data.relationships = [];
    data.coverage = { instrumented_tasks: 0, total_tasks: 1 };
    data.events = [
      { id: 2, task_id: ROOT, seq: 2, type: "task_event", who: "agent-one", did: "已接单；备注提到让其他模型协助", at: "2026-08-12T09:00:00Z", from_status: "queued", to_status: "doing", payload: {} },
      { id: 1, task_id: ROOT, seq: 1, type: "task_event", who: "agent-one", did: "任务已登记", at: "2026-08-12T10:00:00Z", to_status: "queued", payload: {} },
    ];
    mockFetch({ [endpoint]: () => ({ body: data }) });
    render(<TaskCollaboration taskId={ROOT} actors={actors} me={{ ...ME, readonly: true }} />);
    const graph = await screen.findByRole("region", { name: "任务协作关系图" });
    expect(within(graph).getByRole("button", { name: /协作任务：改进阅读器/ })).toBeInTheDocument();
    expect(graph.querySelectorAll(".collab-graph__edge")).toHaveLength(0);
    const history = within(graph).getByRole("region", { name: "任务状态与历史" });
    const rows = within(history).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("任务已登记");
    expect(rows[1]).toHaveTextContent("已接单");
    expect(history).toHaveTextContent("待办 → 进行中");
    expect(screen.getByRole("button", { name: /接棒信息/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "准备重试此分支" })).not.toBeInTheDocument();
  });

  it("时间泳道保留当次设备模型，准备条不表示实际执行，历史缺失不补名册", () => {
    const data = snapshot();
    data.runs[0] = { ...data.runs[0], execution_state: "prepared", started_at: null, prepared_at: "2026-08-12T09:05:00Z", status: "running", waiting: null };
    data.events = [{ id: 1, task_id: ROOT, seq: 1, type: "task_event", who: "agent-one", did: "登记任务", at: "2026-08-12T09:00:00Z", payload: {} }];
    const selected = vi.fn();
    render(<WorkerTimeline snapshot={data} actors={[{ ...AGENT_ONE, node: "current-device", model: "current-model" }, AGENT_TWO]} onSelectTask={vi.fn()} onSelectRun={selected} />);
    const lane = screen.getByRole("region", { name: "设备模型时间泳道" });
    expect(lane).toHaveTextContent("device-b · model-b");
    expect(lane).toHaveTextContent("历史设备/模型未记录");
    expect(lane).not.toHaveTextContent("current-model");
    expect(lane.querySelector(".collab-timeline__bar")).toHaveAttribute("data-prepared", "true");
    expect(lane).toHaveTextContent("待启动");
    expect(lane.querySelectorAll(".collab-timeline__event")).toHaveLength(1);
  });

  it("功能模块贡献展示实际工作、等待和成果，未标注任务不从部门猜模块", async () => {
    mockFetch({ [endpoint]: () => ({ body: snapshot() }) });
    const user = userEvent.setup();
    render(<TaskCollaboration taskId={ROOT} actors={actors} me={ME} />);
    await screen.findByRole("region", { name: "任务协作关系图" });
    await user.click(screen.getByRole("button", { name: "模块贡献" }));
    const modules = screen.getByRole("region", { name: "任务功能模块贡献" });
    expect(modules).toHaveTextContent("阅读器");
    expect(modules).toHaveTextContent("文档");
    expect(modules).toHaveTextContent("模块未标注");
    expect(modules).not.toHaveTextContent("工程");
    expect(modules).toHaveTextContent("成功分支已修复并待复核");
    expect(modules).toHaveTextContent("等复核成功分支");
    expect(modules).toHaveTextContent("artifact-1");
    expect(modules).toHaveTextContent("revision-1");
    expect(modules).toHaveTextContent("待验收");
  });

  it("待办分支没有执行时保留委派要求，明确其不是完成证明", () => {
    const data = snapshot();
    data.tasks = [{ ...data.tasks[1], status: "queued" }]; data.runs = [];
    data.events = [{ id: 1, task_id: CHILD, seq: 1, type: "task_event", who: "agent-one", did: "请核对成功与错误响应", at: data.generated_at, payload: {} }];
    render(<ModuleContributions snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} />);
    const card = screen.getByRole("region", { name: "任务功能模块贡献" });
    expect(card).toHaveTextContent("尚无已完成工作的上报");
    expect(card).toHaveTextContent("任务要求 / 最近记录（非完成证明）");
    expect(card).toHaveTextContent("请核对成功与错误响应");
    expect(card).not.toHaveTextContent("做了什么（执行者声明）");
    expect(card.querySelector(".collab-modules__claims")).not.toBeInTheDocument();
  });

  it("有运行与普通回执但没有completed时不将回执当已完成工作", () => {
    const data = snapshot();
    data.tasks = [data.tasks[1]];
    data.runs[0].progress_report = { completed: [], remaining: ["核对错误分支"], next_action: null, next_owner: null };
    data.runs[0].latest_note = "开始检查错误提示";
    render(<ModuleContributions snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} />);
    const card = screen.getByRole("region", { name: "任务功能模块贡献" });
    expect(card).toHaveTextContent("尚无已完成工作的上报");
    expect(card).toHaveTextContent("开始检查错误提示");
    expect(card).toHaveTextContent("执行回执，不代表工作已完成或已验收");
    expect(card).not.toHaveTextContent("做了什么（执行者声明）");
  });

  it("只有结构化completed形成工作声明，仍标为未验收并保留原回执", () => {
    const data = snapshot(); data.tasks = [data.tasks[1]];
    render(<ModuleContributions snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} />);
    const card = screen.getByRole("region", { name: "任务功能模块贡献" });
    expect(card).toHaveTextContent("做了什么（执行者声明）");
    expect(card).toHaveTextContent("已声明完成 1 项，尚未验收。");
    expect(card).toHaveTextContent("成功分支已修复并待复核");
    expect(card).not.toHaveTextContent("尚无已完成工作的上报");
    const claim = card.querySelector(".collab-modules__claims")!;
    expect(claim).toHaveTextContent("已修复成功分支");
    expect(claim).toHaveTextContent("待验收");
    expect(claim).toHaveTextContent("artifact-1");
  });

  it("多轮执行默认最新，自动同步更新默认选择，但保留手选历史执行", async () => {
    let data = snapshot(); data.task_id = CHILD;
    const initial = { ...data.runs[0], status: "failed" as const, waiting: null, latest_note: "旧执行失败记录" };
    const newest = { ...initial, id: "run-new", actor_id: "agent-one", node: "review-device", model: "review-model", status: "running" as const, latest_note: "新执行记录" };
    data.runs = [initial, newest];
    mockFetch({ [`api/tasks/${CHILD}/collaboration`]: () => ({ body: data }) });
    const user = userEvent.setup();
    render(<TaskCollaboration taskId={CHILD} actors={actors} me={ME} />);
    const detail = await screen.findByRole("article", { name: "选中分支的执行证据" });
    expect(detail).toHaveTextContent("新执行记录");
    expect(detail).not.toHaveTextContent("旧执行失败记录");
    const next = { ...newest, id: "run-newer", model: "newer-model", latest_note: "刷新后的新执行记录" };
    data = { ...data, runs: [...data.runs, next] };
    await user.click(screen.getByRole("button", { name: "刷新协作记录" }));
    expect(await within(detail).findByText("刷新后的新执行记录")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "执行进展" }));
    await user.click(screen.getByRole("button", { name: /device-b · model-b.*Agent Two.*执行失败/ }));
    expect(detail).toHaveTextContent("旧执行失败记录");
    data = { ...data, runs: [...data.runs, { ...next, id: "run-latest", latest_note: "再次刷新后的新执行记录" }] };
    await user.click(screen.getByRole("button", { name: "刷新协作记录" }));
    expect(detail).toHaveTextContent("旧执行失败记录");
    expect(detail).not.toHaveTextContent("再次刷新后的新执行记录");
  });

  it("交棒后尚无新执行，当前持棒与历史执行者分别显示", () => {
    const data = snapshot();
    data.tasks = [{ ...data.tasks[1], holder: "agent-one" }];
    data.runs[0] = { ...data.runs[0], status: "succeeded", waiting: null };
    data.relationships = [{ id: "handoff-latest", kind: "handoff", from_task_id: CHILD, to_task_id: CHILD, from_actor: "agent-two", to_actor: "agent-one", source: "task_event" }];
    render(<><TaskRelationshipGraph snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} /><ModuleContributions snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} /></>);
    const node = screen.getByRole("button", { name: /协作任务：修复错误提示/ });
    expect(node).toHaveTextContent("当前持棒：Agent One");
    expect(node).toHaveTextContent("最近执行：device-b · model-b · 执行成功");
    const module = screen.getByRole("region", { name: "任务功能模块贡献" });
    expect(within(module).getByText("当前持棒").nextElementSibling).toHaveTextContent("Agent One");
    expect(within(module).getByText("最近执行").nextElementSibling).toHaveTextContent("device-b · model-b · Agent Two · 执行成功");
  });

  it("审阅退回更新持棒者，保留历史审阅模型身份并标待验收", () => {
    const data = snapshot(); data.tasks = [data.tasks[1]];
    data.runs[0] = { ...data.runs[0], actor_id: "agent-one", node: "review-device", model: "recorded-review-model", status: "succeeded", waiting: null };
    data.relationships = [{ id: "return-latest", kind: "review_return", from_task_id: CHILD, to_task_id: CHILD, from_actor: "agent-one", to_actor: "agent-two", source: "pipeline_transition" }];
    const changedActors = [{ ...AGENT_ONE, node: "changed-device", model: "changed-model" }, AGENT_TWO];
    render(<><TaskRelationshipGraph snapshot={data} actors={changedActors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} /><ModuleContributions snapshot={data} actors={changedActors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} /></>);
    const graph = screen.getByRole("region", { name: "任务协作关系图" });
    expect(graph.querySelectorAll(".collab-graph__edge[data-kind='review_return']")).toHaveLength(1);
    expect(graph).toHaveTextContent("当前持棒：Agent Two");
    expect(graph).toHaveTextContent("最近执行：review-device · recorded-review-model · 执行成功");
    const module = screen.getByRole("region", { name: "任务功能模块贡献" });
    expect(within(module).getByText("当前持棒").nextElementSibling).toHaveTextContent("Agent Two");
    expect(module).toHaveTextContent("recorded-review-model");
    expect(module).toHaveTextContent("尚未验收");
    expect(module).not.toHaveTextContent("changed-model");
  });

  it("completed已上报成果而run.refs为空时，详情和模块仍展示去重后的实际引用", async () => {
    const data = snapshot(); data.task_id = CHILD;
    data.runs[0].refs = [];
    data.runs[0].progress_report!.completed[0].refs = ["artifact-claim", "commit:example-v1", "artifact-claim"];
    mockFetch({ [`api/tasks/${CHILD}/collaboration`]: () => ({ body: data }) });
    const user = userEvent.setup();
    render(<TaskCollaboration taskId={CHILD} actors={actors} me={ME} />);
    const detail = await screen.findByRole("article", { name: "选中分支的执行证据" });
    const refs = within(detail).getByText("成果引用", { exact: true }).parentElement!;
    expect(refs).not.toHaveTextContent("尚未上报成果引用");
    expect(within(refs).getAllByRole("listitem")).toHaveLength(2);
    expect(refs).toHaveTextContent("artifact-claim");
    expect(refs).toHaveTextContent("commit:example-v1");
    await user.click(screen.getByRole("button", { name: "模块贡献" }));
    const module = screen.getByRole("region", { name: "任务功能模块贡献" });
    const claimedFooter = [...module.querySelectorAll(".collab-modules__refs")].find((element) => element.textContent?.includes("artifact-claim"));
    expect(claimedFooter).toHaveTextContent("成果引用：artifact-claim · commit:example-v1");
    expect(module).toHaveTextContent("尚未验收");
    expect(refs.querySelector("a")).not.toBeInTheDocument();
  });

  it("结束执行的旧报告不提示新进展义务，保留时间、历史成果和待验收标签", async () => {
    const user = userEvent.setup();
    for (const status of ["succeeded", "failed", "cancelled"] as const) {
      const data = snapshot();
      data.runs[0] = { ...data.runs[0], status, waiting: null,
        progress_reported_at: "2026-08-12T08:00:00Z",
        freshness: { state: "stale", age_seconds: 7200, stale_after_seconds: 900 } };
      mockFetch({ [endpoint]: () => ({ body: data }) });
      const rendered = render(<TaskCollaboration taskId={ROOT} actors={actors} me={ME} />);
      const progress = await screen.findByRole("region", { name: "结构化进展" });
      expect(progress).not.toHaveTextContent("进展待更新");
      expect(progress).not.toHaveTextContent("没有新进展");
      expect(progress).toHaveTextContent("进展上报于");
      expect(progress).toHaveTextContent("已修复成功分支");
      expect(progress).toHaveTextContent("artifact-1");
      expect(progress).toHaveTextContent("待验收");
      expect(progress.querySelector("[data-stale]")).toHaveAttribute("data-stale", "false");
      await user.click(screen.getByRole("button", { name: "执行进展" }));
      expect(screen.queryByText(/进展待更新/)).not.toBeInTheDocument();
      rendered.unmount();
    }
  });

  it("活动执行的旧报告继续提醒更新，也不掩盖租约过期", async () => {
    for (const status of ["running", "waiting"] as const) {
      const data = snapshot();
      data.runs[0] = { ...data.runs[0], status,
        waiting: status === "waiting" ? data.runs[0].waiting : null,
        lease_current: false, lease_live: false,
        progress_reported_at: "2026-08-12T08:00:00Z",
        freshness: { state: "stale", age_seconds: 7200, stale_after_seconds: 900 } };
      mockFetch({ [endpoint]: () => ({ body: data }) });
      const rendered = render(<TaskCollaboration taskId={ROOT} actors={actors} me={ME} />);
      const progress = await screen.findByRole("region", { name: "结构化进展" });
      expect(progress).toHaveTextContent("进展待更新");
      expect(progress).toHaveTextContent("已超过 15 分钟没有新进展");
      expect(progress).toHaveTextContent("心跳不代表工作推进");
      expect(progress.querySelector("[data-stale]")).toHaveAttribute("data-stale", "true");
      expect(screen.getByText(/租约已过期或已更换/)).toBeInTheDocument();
      rendered.unmount();
    }
  });

  it("接棒包的历史结束声明不催新进展，活动旧声明仍提醒且证据未核验", async () => {
    const data = snapshot();
    const run = { ...data.runs[0], status: "succeeded" as const, waiting: null,
      progress_reported_at: "2026-08-12T08:00:00Z",
      freshness: { state: "stale" as const, age_seconds: 7200, stale_after_seconds: 900 } };
    const packet: TaskContextSnapshot = {
      version: 1, task_id: CHILD, root_task_id: ROOT, generated_at: data.generated_at,
      revision: { event_id: 4, attempt_id: 1, task_updated_at: data.generated_at, content_hash: "context-history" },
      task: data.tasks[1], collaboration: { tasks: data.tasks, delegations: data.delegations, coverage: data.coverage },
      runs: [run], active_runs: [], confirmed_progress: [],
      unverified_claims: [{ task_id: CHILD, run_id: run.id, actor_id: run.actor_id,
        completed: run.progress_report!.completed, progress: run.progress,
        reported_at: run.progress_reported_at, verification: "unverified", freshness: run.freshness }],
      evidence: [{ task_id: CHILD, run_id: run.id, summary: "历史成果引用", refs: ["artifact-1"], revision: "revision-1", source: "executor_report", verification: "unverified" }],
      next: [], privacy: { contains_private_conversations: false, contains_session_summaries: false, session_links: "owner_or_operator_only" },
    };
    mockFetch({ [`api/tasks/${CHILD}/context`]: () => ({ body: packet }) });
    const user = userEvent.setup();
    render(<TaskContextPanel taskId={CHILD} actors={actors} revision="context-history" />);
    await user.click(screen.getByRole("button", { name: /接棒信息/ }));
    const claims = await screen.findByRole("region", { name: "待验收的执行者声明" });
    expect(claims).toHaveTextContent("已修复成功分支");
    expect(claims).not.toHaveTextContent("进展待更新");
    expect(screen.getByRole("region", { name: "接棒成果引用" })).toHaveTextContent("成果引用 · 待核验");
    expect(screen.getByRole("region", { name: "接棒成果引用" })).toHaveTextContent("artifact-1");
    packet.runs[0] = { ...run, status: "running" };
    packet.active_runs = packet.runs;
    await user.click(screen.getByRole("button", { name: "刷新接棒信息" }));
    expect(await within(claims).findByText("进展待更新")).toBeInTheDocument();
    expect(claims).toHaveTextContent("revision-1");
  });

  it("外部依赖节点只打开自身详情，不伪装为委派子任务", async () => {
    const data = snapshot();
    data.related_tasks = [makeTask({ id: "upstream-task", title: "准备接口", status: "done" })];
    data.tasks[0].depends_on = ["upstream-task"];
    const open = vi.fn();
    const user = userEvent.setup();
    render(<TaskRelationshipGraph snapshot={data} actors={actors} onSelectTask={vi.fn()} onSelectRun={vi.fn()} onOpenTask={open} />);
    await user.click(screen.getByRole("button", { name: /依赖任务：准备接口/ }));
    expect(open).toHaveBeenCalledWith("upstream-task");
    expect(screen.getByRole("region", { name: "任务协作关系图" }).querySelectorAll(".collab-graph__edge[data-kind='delegation']")).toHaveLength(2);
  });

  it("模块登记随委派意图保存，未知结果重试复用键，更换模块生成新意图", async () => {
    let fail = true;
    const { calls } = mockFetch({ [`api/tasks/${ROOT}/delegations`]: () => fail
      ? { status: 503, body: { detail: "暂时不可用" } } : { body: { created: true } } });
    const user = userEvent.setup();
    render(<DelegateTaskForm taskId={ROOT} actors={actors} leaseTerm={1} targets={["agent-two"]} onCreated={vi.fn()} />);
    await user.click(screen.getByText("委派子任务"));
    await user.selectOptions(screen.getByLabelText("执行者"), "agent-two");
    await user.type(screen.getByLabelText("子任务标题"), "修复状态提示");
    await user.type(screen.getByLabelText("功能模块（可选）"), "阅读器");
    await user.type(screen.getByLabelText("委派要求"), "核对成功与失败状态");
    await user.type(screen.getByLabelText("验收标准（每行一条）"), "通过成功分支回归");
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    await screen.findByText("委派失败：暂时不可用");
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    await screen.findByText("委派失败：暂时不可用");
    await user.clear(screen.getByLabelText("功能模块（可选）"));
    await user.type(screen.getByLabelText("功能模块（可选）"), "状态管理");
    fail = false;
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    await screen.findByText("已建立委派子卡，等待执行者开工。");
    const bodies = calls.map((call) => JSON.parse(String(call.init?.body)));
    expect(bodies).toHaveLength(3);
    expect(bodies[0].module).toBe("阅读器");
    expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key);
    expect(bodies[2].idempotency_key).not.toBe(bodies[1].idempotency_key);
    expect(bodies[2].module).toBe("状态管理");
    expect(screen.getByLabelText("功能模块（可选）")).toHaveValue("");
  });

  it("复核名单限定授权模型、排除执行者，复核变化参与幂等意图", async () => {
    let fail = true;
    const { calls } = mockFetch({ [`api/tasks/${ROOT}/delegations`]: () => fail
      ? { status: 503, body: { detail: "暂时不可用" } } : { body: { created: true } } });
    const third = { ...AGENT_ONE, id: "agent-three", display_name: "Agent Three" };
    const sync = { ...AGENT_ONE, id: "device-session-sync", display_name: "Transport", model: "session-index-v1" };
    const unauthorized = { ...AGENT_ONE, id: "agent-other", display_name: "Other" };
    const user = userEvent.setup();
    render(<DelegateTaskForm taskId={ROOT} actors={[...actors, third, sync, unauthorized]} leaseTerm={1} targets={["agent-one", "agent-two", "agent-three", sync.id]} onCreated={vi.fn()} />);
    await user.click(screen.getByText("委派子任务"));
    await user.selectOptions(screen.getByLabelText("执行者"), "agent-two");
    const reviewers = screen.getByLabelText("交付后复核模型（可选）");
    expect(within(reviewers).queryByRole("option", { name: /Agent Two|Transport|Other/ })).not.toBeInTheDocument();
    await user.selectOptions(reviewers, "agent-one");
    await user.type(screen.getByLabelText("子任务标题"), "复核阅读状态");
    await user.type(screen.getByLabelText("委派要求"), "核对状态返回值");
    await user.type(screen.getByLabelText("验收标准（每行一条）"), "通过独立复核");
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    await screen.findByText("委派失败：暂时不可用");
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    await screen.findByText("委派失败：暂时不可用");
    await user.selectOptions(reviewers, "agent-three");
    fail = false;
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    await screen.findByText("已建立委派子卡，等待执行者开工。");
    const bodies = calls.map((call) => JSON.parse(String(call.init?.body)));
    expect(bodies[0].pipeline).toEqual([{ name: "执行", holder: "agent-two", gate: "auto" }, { name: "复核", holder: "agent-one", gate: "review" }]);
    expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key);
    expect(bodies[2].idempotency_key).not.toBe(bodies[1].idempotency_key);
    expect(bodies[2].pipeline[1].holder).toBe("agent-three");
  });
});
