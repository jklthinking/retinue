import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "../pages/Home";
import { ThemeProvider } from "../theme";
import type { Me, RuntimeSessionInfo } from "../types";
import { STATUS_LABEL } from "../types";
import { DATA_REFRESH_EVENT } from "../lib/refresh";
import { AGENT_ONE, AGENT_TWO, ME, makeSummary, makeTask, mockFetch } from "./helpers";

const task = makeTask({ title: "模块开发", status: "doing", holder: "agent-two" });
const sync = { ...AGENT_ONE, id: "example-session-sync", model: "session-index-v2", display_name: "同步代理" };
const actors = [{ ...AGENT_ONE, node: "host-a", model: "model-a" }, { ...AGENT_TWO, node: "host-b", model: "model-b" }, sync];
const session: RuntimeSessionInfo = {
  id: 23, actor_id: "agent-one", actor_name: "Agent One", runtime: "codex", node: "host-a",
  title: "讨论开发范围", summary: "模块边界已整理", privacy: "summary", cursor: 0, message_count: 4,
  messages: [], task_id: task.id, task_title: task.title, resume_capable: false,
  started_at: null, updated_at: null, synced_at: null,
};
const summary = () => makeSummary({
  task_counts: { queued: 2, doing: 1, blocked: 0, handoff: 0, done: 3 }, actors, tasks: [task],
  recent_events: [{ who: "agent-one", did: "移交模块开发", at: "2026-08-12T10:00:00Z", from_status: "doing", to_status: "handoff", task_id: task.id, task_title: task.title }],
});

function stubHome({ sessions = [session], fail = () => false }: { sessions?: RuntimeSessionInfo[]; fail?: () => boolean } = {}) {
  return mockFetch({
    "api/summary": () => fail() ? { status: 500, body: { detail: "summary unavailable" } } : { body: summary() },
    "api/status": () => ({ body: { version: "test", task_counts: {}, actors: 3, online_actors: 2, skills: 4, nodes: 2, knowledge_sources: 1 } }),
    "api/sessions": () => ({ body: sessions }),
    "api/quota": () => ({ body: { generated_at: "2026-10-04T03:00:00Z", providers: [] } }),
    "api/inbox": () => ({ body: { decisions: { count: 0, items: [] }, reviews: { count: 0, items: [] }, blocked: { count: 0, items: [] }, stale: { count: 0, items: [] } } }),
  });
}
function renderHome(me: Me = ME) {
  const onNavigate = vi.fn();
  const onOpenTask = vi.fn();
  const onOpenSession = vi.fn();
  render(<ThemeProvider><Home me={me} onNavigate={onNavigate} onOpenTask={onOpenTask} onOpenSession={onOpenSession} /></ThemeProvider>);
  return { onNavigate, onOpenTask, onOpenSession };
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Visual collaboration Home", () => {
  it("preserves all three original visual panels using real summary and session data", async () => {
    stubHome();
    const { onOpenTask, onOpenSession } = renderHome();
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "任务流转" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "派单协调" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "会话流转台" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "任务状态流转图" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "派单协调图" })).toHaveTextContent("host-b · model-b");
    expect(screen.queryByRole("tablist", { name: "日程分区" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `查看${STATUS_LABEL.doing}任务：1` }));
    await user.click(within(screen.getByRole("region", { name: `${STATUS_LABEL.doing}任务` })).getByRole("button", { name: /模块开发/ }));
    expect(onOpenTask).toHaveBeenCalledWith(task.id);
    await user.click(screen.getByRole("button", { name: "查看派单：模块开发" }));
    expect(onOpenTask).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: /讨论开发范围/ }));
    expect(onOpenSession).toHaveBeenCalledWith(23);
    await user.click(screen.getByRole("button", { name: "查看任务：模块开发" }));
    expect(onOpenTask).toHaveBeenCalledTimes(3);
  });

  it("counts model workers separately from session synchronization and keeps affairs navigation", async () => {
    stubHome();
    const { onNavigate } = renderHome();
    const user = userEvent.setup();
    const metric = await screen.findByRole("button", { name: /近期上报的模型 Worker/ });
    expect(metric).toHaveTextContent("1/ 2");
    expect(metric).toHaveTextContent("认证 API 活动，不代表正在执行");
    expect(screen.getByRole("img", { name: "派单协调图" })).not.toHaveTextContent("同步代理");
    await user.click(screen.getByRole("button", { name: /我的事务/ }));
    expect(onNavigate).toHaveBeenCalledWith("affairs");
  });

  it("shows the diagrams for viewers without fetching personal todos or rendering metadata-only summaries", async () => {
    const { calls } = stubHome({ sessions: [{ ...session, privacy: "metadata", summary: "should not render" }] });
    renderHome({ ...ME, role: "viewer" });
    expect(await screen.findByRole("heading", { name: "会话流转台" })).toBeInTheDocument();
    expect(screen.queryByText("should not render")).not.toBeInTheDocument();
    expect(screen.getByText("4 条原生消息，仅同步元数据")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /我的事务/ })).not.toBeInTheDocument();
    expect(calls.some(({ url }) => url.startsWith("api/todos"))).toBe(false);
  });

  it("keeps the previously loaded visual state visible when a refresh fails", async () => {
    let failed = false;
    stubHome({ fail: () => failed });
    renderHome();
    await screen.findByRole("button", { name: `查看${STATUS_LABEL.doing}任务：1` });
    failed = true;
    window.dispatchEvent(new Event(DATA_REFRESH_EVENT));
    await waitFor(() => expect(screen.getAllByText("summary unavailable").length).toBeGreaterThan(0));
    expect(screen.getByRole("img", { name: "任务状态流转图" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `查看${STATUS_LABEL.doing}任务：1` })).toBeInTheDocument();
  });
});
