import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Operations from "../pages/Operations";
import KingdomOperationsPage from "../pages/KingdomOperationsPage";
import Reports from "../pages/Reports";
import DataCatalog from "../pages/DataCatalog";
import Overview from "../pages/Overview";
import { KingdomPage } from "../pages/KingdomPage";
import { gatewayProcessState, observationState } from "../lib/observation";
import { formatTokens, sourceTime } from "../lib/operations";
import { AGENT_ONE, AGENT_TWO, mockFetch } from "./helpers";
import type { MetricsSummary } from "../types";
import { requestDataRefresh } from "../lib/refresh";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const old = "2026-01-01T10:00:00Z";
const fresh = () => new Date().toISOString();
const ledger = { start: "2026-01-01", days: [{ date: "2026-01-01", receipts: 3, done: 1 }], done_by_actor: [{ actor_id: AGENT_ONE.id, done: 1 }], diagnostics: { timezone_unknown_timestamps: 2, invalid_timestamps: 0, future_events_in_candidate_window: 0 } };
const missing = { actor_id: AGENT_TWO.id, days: {}, input: null, output: null, usage_available: false, records: 0, runtimes: [] };
const usage = { start: "2026-01-01", end: "2026-01-07", days: 7, actors: [
  { actor_id: AGENT_ONE.id, days: {}, input: 1200, output: 100, usage_available: true, records: 1, last_reported_at: old, stale: true, runtimes: [{ runtime: "runtime-a", input: 1200, output: 100, records: 1 }] }, missing,
], coverage: { expected_actors: 2, reported_actors: 1, missing_actors: [AGENT_TWO.id], complete: false } };
function hermes() {
  return { schema_version: 1, source: "hermes_session_snapshots", generated_at: fresh(),
    nodes: [{ id: "node-a", generated_at: old, freshness_seconds: 7200, stale: true }],
    agents: [{ id: "profile-one", node: "node-a", display_name: "Profile One", role: "例示职责", kind: "technology", model: "model-a", provider: "provider-a",
      observed_at: old, snapshot_stale: true, gateway: { healthy: true, current_process_healthy: null as boolean | null, observed_at: old, snapshot_stale: true }, sessions: 4, messages: 9,
      usage: { available: true, observed_at: old, stat_date: "2026-01-01", window_current: false, snapshot_stale: true, today_input: 100, today_output: 20,
        today_tokens: 120, week_tokens: 120, today_sessions: 0, last_active_at: old,
        current_today_input: null as number | null, current_today_output: null as number | null, current_today_tokens: null as number | null,
        daily: [{ day: "2026-01-01", tokens: 120, sessions: 1 }], hourly: [{ hour: 1, tokens: 120, sessions: 1 }] } }],
    totals: { agents: 1, online_agents: 1, process_healthy_gateways: 1, current_process_healthy_gateways: null as number | null,
      active_today: 0, today_input: 100, today_output: 20, today_tokens: 120, today_sessions: 0, open_tasks: 1, all_tasks: 8,
      current_today_input: null as number | null, current_today_output: null as number | null, current_today_tokens: null as number | null, available_agents: 1 },
  };
}
const panel = (title: string) => within(screen.getByRole("heading", { name: title }).closest("section")!);

function currentHermes(stamp: string, day: string) {
  const data = hermes();
  data.nodes[0] = { ...data.nodes[0], generated_at: stamp, freshness_seconds: 0, stale: false };
  data.agents[0] = { ...data.agents[0], observed_at: stamp, snapshot_stale: false,
    gateway: { healthy: true, current_process_healthy: true, observed_at: stamp, snapshot_stale: false },
    usage: { ...data.agents[0].usage, observed_at: stamp, stat_date: day, window_current: true, snapshot_stale: false,
      current_today_input: 100, current_today_output: 20, current_today_tokens: 120,
      daily: [{ day, tokens: 120, sessions: 1 }], hourly: [{ hour: 1, tokens: 120, sessions: 1 }] },
  };
  data.totals = { ...data.totals, current_process_healthy_gateways: 1, current_today_input: 100, current_today_output: 20, current_today_tokens: 120 };
  return data;
}

describe("当前计量不能沿用失效的来源观测", () => {
  it("fails current totals closed after a read failure, while preserving historical charts", async () => {
    const stamp = "2030-01-02T04:00:00Z";
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(stamp));
    let failed = false;
    mockFetch({ "api/kingdom/operations": () => failed ? { status: 503, body: { detail: "source read failed" } } : { body: currentHermes(stamp, "2030-01-02") } });
    render(<KingdomOperationsPage days={1} />);
    await screen.findByText("Gateway 运行");
    expect(screen.getByText("Gateway 进程正常").closest("article")).toHaveTextContent("1/1");
    expect(screen.getByText("当前统计日会话记录 Tokens").closest("article")).toHaveTextContent("120");
    failed = true;
    act(requestDataRefresh);
    await screen.findByText("source read failed");
    expect(screen.getByText("Gateway 进程正常").closest("article")).toHaveTextContent("未知");
    expect(screen.getByText("当前统计日会话记录 Tokens").closest("article")).toHaveTextContent("未知");
    expect(screen.getByRole("img", { name: "Hermes 快照模型 Token 趋势" })).toBeInTheDocument();
    expect(screen.getByText("来源快照记录").previousElementSibling).toHaveTextContent("120");
  });

  it("does not carry yesterday's current total over the Shanghai midnight", async () => {
    const before = "2030-01-01T15:59:00Z";
    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.parse(before));
    const { calls } = mockFetch({ "api/kingdom/operations": () => ({ body: currentHermes(before, "2030-01-01") }) });
    const { rerender } = render(<KingdomOperationsPage days={1} />);
    await screen.findByText("Gateway 运行");
    expect(screen.getByText("当前统计日会话记录 Tokens").closest("article")).toHaveTextContent("120");
    clock.mockReturnValue(Date.parse("2030-01-01T16:01:00Z"));
    rerender(<KingdomOperationsPage days={1} />);
    expect(screen.getByText("当前统计日会话记录 Tokens").closest("article")).toHaveTextContent("未知");
    expect(screen.getByText("Gateway 进程正常").closest("article")).toHaveTextContent("1/1");
    expect(screen.getByText("来源快照记录").previousElementSibling).toHaveTextContent("120");
    expect(calls).toHaveLength(1);
  });

  it("ages cached observations dynamically without treating old process flags as current", async () => {
    const stamp = "2030-01-02T04:00:00Z";
    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.parse(stamp));
    mockFetch({ "api/kingdom/operations": () => ({ body: currentHermes(stamp, "2030-01-02") }) });
    const { rerender } = render(<KingdomOperationsPage days={1} />);
    await screen.findByText("Gateway 运行");
    clock.mockReturnValue(Date.parse("2030-01-02T04:31:00Z"));
    rerender(<KingdomOperationsPage days={1} />);
    expect(screen.getByText("Gateway 未确认")).toBeInTheDocument();
    expect(screen.getByText("Gateway 进程正常").closest("article")).toHaveTextContent("未知");
    expect(screen.getByText("当前统计日会话记录 Tokens").closest("article")).toHaveTextContent("未知");
    expect(screen.getByText(/node-a 采集/)).toHaveTextContent("来源过期");
    expect(screen.getByText("来源快照记录").previousElementSibling).toHaveTextContent("120");
  });
});

describe("运营来源、计量与统一入口", () => {
  it("keeps one title/range and distinct sources, and does not add Hermes to Retinue", async () => {
    const { calls } = mockFetch({ "api/metrics/throughput": () => ({ body: ledger }), "api/actors": () => ({ body: [AGENT_ONE, AGENT_TWO] }),
      "api/metrics/summary": () => ({ body: usage }), "api/kingdom/operations": () => ({ body: hermes() }) });
    render(<Operations kingdomOn />);
    await screen.findByText("1.2k / 100");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "今天" })).toHaveLength(1);
    const retinue = panel("Retinue 模型响应用量");
    expect(retinue.getByText("1.2k")).toBeInTheDocument();
    expect(retinue.getByText("1.2k")).toHaveAttribute("title", "1200 个模型 token（原始整数）");
    expect(retinue.getByText("1.2k / 100")).toHaveAttribute("title", "输入 1200 个模型 token（原始整数）；输出 100 个模型 token（原始整数）");
    expect(retinue.getByText("未上报")).toBeInTheDocument();
    expect(retinue.getByText(/1 个未上报/)).toBeInTheDocument();
    expect(retinue.getByText(/历史扫描可能覆盖多个实际模型/)).toHaveTextContent("不能将汇总视为该登记型号的精确用量");
    expect(retinue.getAllByText(/当前登记型号：/)).toHaveLength(2);
    const hermesSection = screen.getByRole("region", { name: "Hermes 会话来源" });
    expect(within(hermesSection).getByText("Gateway 未确认")).toBeInTheDocument();
    expect(within(hermesSection).getByText("Gateway 进程正常").closest("article")).toHaveTextContent("未知");
    await userEvent.click(screen.getByRole("button", { name: "近 30 日" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("metrics/summary?days=30"))).toBe(true));
    expect(screen.getByText(/无法给出 30 日用量/)).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("metrics/throughput?days=30"))).toBe(true);
    expect(screen.getByRole("heading", { name: "任务吞吐" })).toBeInTheDocument();
    expect(screen.getByText(/2 条历史事件的时区未知/)).toHaveTextContent("原始任务事件与历史仍保留");
    expect(screen.getByRole("heading", { name: "来源快照模型 Token 趋势" })).toBeInTheDocument();
  });

  it("distinguishes missing usage from an explicitly reported zero", async () => {
    let summary: MetricsSummary = { ...usage, actors: [missing] };
    mockFetch({ "api/metrics/summary": () => ({ body: summary }) });
    const { rerender } = render(<Reports days={1} actors={[AGENT_TWO]} />);
    await screen.findByText(/未知用量不能记为 0/);
    expect(panel("Retinue 模型响应用量").getAllByText("未知")).toHaveLength(2);
    summary = { ...usage, actors: [{ ...missing, input: 0, output: 0, usage_available: true, records: 1 }] };
    rerender(<Reports days={7} actors={[AGENT_TWO]} />);
    expect(await screen.findByText("0 / 0")).toBeInTheDocument();
    expect(screen.queryByText(/未知用量不能记为 0/)).not.toBeInTheDocument();
  });

  it("keeps a successful snapshot and source time after refresh fails", async () => {
    let fail = false;
    mockFetch({ "api/metrics/throughput": () => ({ body: ledger }), "api/actors": () => ({ body: [AGENT_ONE] }),
      "api/metrics/summary": () => fail ? { status: 503, body: { detail: "collector unavailable" } } : { body: usage } });
    render(<Operations kingdomOn={false} />);
    await screen.findByText("1.2k / 100");
    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "刷新页面数据" }));
    expect(await screen.findByText("collector unavailable")).toBeInTheDocument();
    expect(screen.getByText("1.2k / 100")).toBeInTheDocument();
    expect(screen.getByText(/来源最近上报/)).toHaveTextContent(sourceTime(old));
    expect(screen.getByText(/读取失败，数据可能已过期/)).toBeInTheDocument();
  });

  it("does not replace gaps in the Hermes trend with zero observations", async () => {
    mockFetch({ "api/metrics/throughput": () => ({ body: ledger }), "api/actors": () => ({ body: [] }),
      "api/metrics/summary": () => ({ body: { ...usage, actors: [] } }), "api/kingdom/operations": () => ({ body: hermes() }) });
    render(<Operations kingdomOn />);
    await screen.findByText("Profile One", { selector: ".ko-agent strong" });
    await userEvent.click(screen.getByRole("button", { name: "今天" }));
    const chart = screen.getByRole("img", { name: "Hermes 快照模型 Token 趋势" });
    expect(chart.querySelectorAll("circle")).toHaveLength(1);
    expect(screen.getByText("当前统计日会话记录 Tokens").closest("article")).toHaveTextContent("未知");
  });
});

describe("来源新鲜度与保留的质量检查", () => {
  it("treats absent, invalid and future timestamps as unknown, and process health as a separate observation", () => {
    expect(observationState(null)).toBe("unknown");
    expect(observationState("not-a-date")).toBe("unknown");
    expect(observationState(new Date(Date.now() + 600_000).toISOString())).toBe("clock_skew");
    expect(observationState(old)).toBe("stale");
    expect(gatewayProcessState({ healthy: true }, { stale: true, generated_at: old })).toBeNull();
    expect(gatewayProcessState({ healthy: true, current_process_healthy: null, observed_at: fresh() })).toBeNull();
    expect(gatewayProcessState({ healthy: false, current_process_healthy: false, observed_at: fresh() })).toBe(false);
    expect(formatTokens(undefined)).toBe("未知");
  });

  it("retains field quality, exposes read-only health exceptions and labels lease expiry", async () => {
    const catalog = { schema_version: "test", generated_at: fresh(),
      storage_contract: { documents: "结构化文档", operational: "任务事件链", json_fields: [], canonical: "Retinue" },
      summary: { tasks: 9, actors: 2, skills: 1, nodes: 1, knowledge_sources: 1, sessions: 4, events: 10, pipeline_templates: 1, quality_score: 100 },
      layers: [], quality: { score: 100, checks: [{ key: "identity", label: "已有字段检查", observed: 2, total: 2, status: "good", detail: "保留原质量检查" }] },
      recommendations: [], privacy: { web_catalog: "目录元数据", excluded: [] },
      health: { generated_at: fresh(), mode: "read_only", history_policy: "历史事件保留，不删除。", thresholds_seconds: { node_telemetry: 1800 },
        checks: [{ key: "task_leases", label: "执行租约", observed: 0, total: 1, status: "attention", detail: "到期不代表工作失败", issue_count: 1,
          items: [{ kind: "task", id: "task-example", actor_id: AGENT_ONE.id, state: "expired", observed_at: old }] }] } };
    mockFetch({ "api/data-catalog": () => ({ body: catalog }) });
    render(<DataCatalog />);
    await screen.findByRole("heading", { name: "数据整理台" });
    expect(screen.getByRole("heading", { name: "基础结构完整度" }).closest(".rt-metric")).toHaveTextContent("100%");
    expect(screen.getByText(/基础结构完整度仅衡量格式与关联检查/)).toHaveTextContent("不代表来源新鲜度");
    await userEvent.click(screen.getByRole("tab", { name: "质量检查" }));
    expect(screen.getByRole("heading", { name: "基础字段与关联检查" })).toBeInTheDocument();
    expect(screen.getByText("基础结构得分 100%")).toBeInTheDocument();
    expect(screen.getByText("已有字段检查")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "运行数据健康" })).toBeInTheDocument();
    await userEvent.click(screen.getByText("查看 1 项待核对记录"));
    expect(screen.getByText("task-example")).toBeInTheDocument();
    expect(screen.getByText("租约到期")).toBeInTheDocument();
    expect(screen.getByText(/到期时间/)).toHaveTextContent(sourceTime(old));
    expect(screen.queryByRole("button", { name: /删除|合并|重跑/ })).not.toBeInTheDocument();
  });

  it("labels retained history separately from passing health and keeps current source/configuration warnings", async () => {
    const catalog = { schema_version: "test", generated_at: fresh(),
      storage_contract: { documents: "文档", operational: "事件链", json_fields: [], canonical: "Retinue" },
      summary: { tasks: 1, actors: 2, skills: 0, nodes: 1, knowledge_sources: 0, sessions: 3, events: 1, pipeline_templates: 0, quality_score: 100 },
      layers: [], quality: { score: 100, checks: [] }, recommendations: [], privacy: { web_catalog: "元数据", excluded: [] },
      health: { generated_at: fresh(), mode: "read_only", history_policy: "历史来源保留，不代表当前采集新鲜。", thresholds_seconds: { session_sync: 1800 },
        checks: [
          { key: "session_history", label: "已分类历史来源", observed: 0, total: 2, retained: 2, status: "info", detail: "当前采集未配置，记录保留。", issue_count: 0,
            items: [{ kind: "source", id: "historical-source-a", runtime: "runtime-a", state: "retained_history", observed_at: old },
              { kind: "source", id: "historical-source-b", runtime: "runtime-b", state: "retained_history", observed_at: old }] },
          { key: "session_sync", label: "当前会话同步", observed: 1, total: 2, status: "attention", detail: "当前来源仍须核对。", issue_count: 1,
            items: [{ kind: "source", id: "current-source", runtime: "runtime-a", state: "stale", observed_at: old }] },
          { key: "session_history_config", label: "历史来源分类配置", observed: 0, total: 1, status: "attention", detail: "历史来源配置无效；所有同步来源继续参与当前检查。", issue_count: 1,
            items: [{ kind: "configuration", id: "history-config", state: "invalid_operator_configuration" }] },
        ] } };
    mockFetch({ "api/data-catalog": () => ({ body: catalog }) });
    render(<DataCatalog />);
    await screen.findByRole("heading", { name: "数据整理台" });
    await userEvent.click(screen.getByRole("tab", { name: "质量检查" }));
    const history = within(screen.getByText("已分类历史来源").closest("article")!);
    expect(history.getByText("2 项历史来源保留")).toBeInTheDocument();
    expect(history.queryByText(/项通过|待核对记录/)).not.toBeInTheDocument();
    await userEvent.click(history.getByText("查看 2 项历史来源"));
    expect(history.getAllByText("历史来源（当前采集未配置）")).toHaveLength(2);
    expect(history.getByText("historical-source-a")).toBeInTheDocument();
    const current = within(screen.getByText("当前会话同步").closest("article")!);
    expect(current.getByText("1 / 2 项通过")).toBeInTheDocument();
    expect(current.getByText(/当前来源仍须核对/)).toHaveTextContent("时效阈值 30 分钟");
    await userEvent.click(current.getByText("查看 1 项待核对记录"));
    expect(current.getByText("来源过期")).toBeInTheDocument();
    expect(current.getByText("current-source")).toBeInTheDocument();
    const config = within(screen.getByText("历史来源分类配置").closest("article")!);
    await userEvent.click(config.getByText("查看 1 项待核对记录"));
    expect(config.getByText("历史来源配置无效")).toBeInTheDocument();
    expect(config.queryByText(/项历史来源保留/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".data-health-panel .quality-icon--attention")).toHaveLength(2);
  });

  it("uses eligible workers and telemetry freshness instead of the legacy online total", async () => {
    const node = (id: string, at: string | null) => ({ id, label: id, hostname: "example", uptime_seconds: 1, load: [], memory: {}, disk: {}, services: [], updated_at: at });
    mockFetch({ "api/status": () => ({ body: { version: "test", online_actors: 99, task_counts: { done: 9 }, skills: 0, nodes: 4, knowledge_sources: 0 } }),
      "api/actors": () => ({ body: [AGENT_ONE, AGENT_TWO, { ...AGENT_ONE, id: "human", kind: "human" }, { ...AGENT_ONE, id: "disabled", disabled: true }, { ...AGENT_ONE, id: "example-session-sync" }] }),
      "api/nodes": () => ({ body: [node("missing", null), node("invalid", "bad-time"), node("future", new Date(Date.now() + 600_000).toISOString()), node("old", old)] }),
      "api/skills": () => ({ body: [] }) });
    render(<Overview />);
    await screen.findByText("启用的模型 Worker");
    const worker = screen.getByText("启用的模型 Worker").closest(".rt-metric")!;
    expect(worker).toHaveTextContent("1/ 2");
    expect(worker).not.toHaveTextContent("99");
    expect(screen.getAllByText("遥测时间未知")).toHaveLength(2);
    expect(screen.getByText("时钟待核对")).toBeInTheDocument();
    expect(screen.getByText("遥测已旧")).toBeInTheDocument();
    expect(screen.queryByText("在线", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByText("任务").closest(".rt-metric")).toHaveTextContent("9");
  });

  it("does not label an old Hermes gateway snapshot as a currently online worker", async () => {
    const response = { ...hermes(), agents: hermes().agents.map((agent) => ({ ...agent, profile: "profile-one", skill_visible: 1, skill_local: 1 })),
      tasks: { items: [], by_status: {}, total: 0 }, crons: [], skills: [], services: [], knowledge_sources: [],
      totals: { nodes: 1, agents: 1, active_gateways: 1, skills: 0, crons: 0, sessions: 4, messages: 9, tasks: 0, vault_notes: 0, alerts: 0 },
      vault: { available: false }, vault_replicas: [], alerts: [], sessions: { total: 4, messages: 9, recent: [] } };
    mockFetch({ "api/kingdom/overview": () => ({ body: response }) });
    render(<KingdomPage view="agents" />);
    expect(await screen.findByText("Gateway 未确认")).toBeInTheDocument();
    expect(screen.queryByText("在线", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重启 Gateway" })).toBeInTheDocument();
    expect(screen.getByText(/采集/)).toHaveTextContent(sourceTime(old));
  });
});
