import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import TaskDrawer from "../components/TaskDrawer";
import { ThemeProvider } from "../theme";
import type { Me, TodoItem } from "../types";
import { localTodayISO } from "../types";
import { ME, makeTask, mockFetch } from "./helpers";

// Keep App navigation, KingdomHub, hash links, Agenda and the real task drawer in this
// test. Other pages have their own data/visual tests and are costly to mount.
vi.mock("../pages/Home", () => ({ default: ({ onOpenTask }: { onOpenTask: (id: string) => void }) => <div><h1>首页内容</h1><button onClick={() => onOpenTask("task-20260812-001")}>查看样例任务</button></div> }));
vi.mock("../pages/TaskFlowPage", () => ({ default: () => <h1>协作图内容</h1> }));
vi.mock("../pages/Affairs", () => ({ default: () => <h1>事务内容</h1> }));
vi.mock("../pages/Board", () => ({ default: () => <h1>看板内容</h1> }));
vi.mock("../pages/TaskCenter", () => ({ default: () => <h1>任务列表内容</h1> }));
vi.mock("../pages/Workroom", () => ({ default: () => <h1>协作空间内容</h1> }));
vi.mock("../pages/Collab", () => ({ default: () => <h1>进度概览内容</h1> }));
vi.mock("../pages/Sessions", () => ({ default: () => <h1>历史会话内容</h1> }));
vi.mock("../pages/LiveSessions", () => ({ default: ({ me }: { me: Me }) => <h1 data-readonly={String(me.readonly)}>实时会话内容</h1> }));
vi.mock("../pages/Overview", () => ({ default: () => <h1>系统健康内容</h1> }));
vi.mock("../pages/Operations", () => ({ default: () => <h1>运营效率内容</h1> }));
vi.mock("../pages/Roster", () => ({ default: () => <h1>模型名册内容</h1> }));
vi.mock("../pages/Skills", () => ({ default: () => <h1>技能内容</h1> }));
vi.mock("../pages/Knowledge", () => ({ default: () => <h1>知识内容</h1> }));
vi.mock("../pages/DataCatalog", () => ({ default: () => <h1>数据内容</h1> }));
vi.mock("../pages/Infra", () => ({ default: () => <h1>设备内容</h1> }));
vi.mock("../pages/Admin", () => ({ default: () => <h1>管理内容</h1> }));
vi.mock("../pages/KingdomPage", () => ({ KingdomPage: ({ view }: { view: string }) => <h1 data-view={view}>中枢内容</h1> }));
vi.mock("../pages/KingdomKnowledgePage", () => ({ default: () => <h1>中枢知识内容</h1> }));
vi.mock("../components/TaskCollaboration", () => ({ default: () => <p>协作详情证据</p> }));

function todo(overrides: Partial<TodoItem> = {}): TodoItem {
  return {
    id: "todo-one", owner_user_id: 1, title: "模块说明", notes: "", status: "open",
    due_at: localTodayISO(), event_on: null, parent_id: null, progress: 0, children: [],
    remind_at: null, proposal_id: null, source_channel: null, source_backlink: null, task_id: null,
    created_at: "2026-08-12T09:00:00Z", updated_at: "2026-08-12T09:00:00Z", ...overrides,
  };
}

function seed(me: Me = ME) {
  let serial = 0;
  return mockFetch({
    "api/auth/me": () => ({ body: me }),
    "api/todos/home": () => ({ body: { pending_proposals: [], due_today: [todo()], overdue: [], waiting_on_others: [], events_tomorrow: [], anytime: [] } }),
    "api/todos": (init) => {
      if (init?.method === "POST") {
        serial += 1;
        return { body: todo({ id: `todo-created-${serial}`, ...JSON.parse(String(init.body || "{}")) }) };
      }
      return { body: { todos: [todo()] } };
    },
    "api/actors": () => ({ body: [] }),
    "api/tasks/task-20260812-001": () => ({ body: makeTask({ title: "样例任务卡", status: "doing" }) }),
    "api/sessions": () => ({ body: [] }),
  });
}

function showApp(me: Me = ME, search = "/") {
  window.history.replaceState(null, "", search);
  const network = seed(me);
  render(<ThemeProvider initialTheme="neutral"><App /></ThemeProvider>);
  return network;
}

function route(search: string) {
  act(() => {
    window.history.replaceState(null, "", search);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

describe("App navigation integration and permission preservation", () => {
  it("consolidates the sidebar while retaining every previous task/session/system view as reachable modes", async () => {
    showApp();
    await screen.findByRole("heading", { name: "首页内容" });
    const sidebar = screen.getByRole("navigation");
    expect(within(sidebar).queryByRole("button", { name: "任务看板" })).not.toBeInTheDocument();
    expect(within(sidebar).queryByRole("button", { name: "协作空间" })).not.toBeInTheDocument();
    expect(within(sidebar).queryByRole("button", { name: "实时会话" })).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(within(sidebar).getByRole("button", { name: "任务工作台" }));
    expect(screen.getByRole("heading", { name: "协作图内容" })).toBeInTheDocument();
    for (const [tab, content, page] of [
      ["任务看板", "看板内容", "board"], ["任务列表", "任务列表内容", "taskcenter"],
      ["协作空间", "协作空间内容", "workroom"], ["进度概览", "进度概览内容", "collab"],
    ]) {
      await user.click(within(screen.getByRole("group", { name: "工作台视图" })).getByRole("button", { name: tab }));
      expect(screen.getByRole("heading", { name: content })).toBeInTheDocument();
      expect(new URLSearchParams(window.location.search).get("page")).toBe(page);
      expect(within(sidebar).getByRole("button", { name: "任务工作台" })).toHaveClass("is-active");
      expect(screen.getByLabelText("当前页面用途").textContent!.length).toBeGreaterThan(12);
    }
    await user.click(within(sidebar).getByRole("button", { name: "会话中心" }));
    expect(screen.getByRole("heading", { name: "历史会话内容" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "实时会话" }));
    expect(screen.getByRole("heading", { name: "实时会话内容" })).toBeInTheDocument();
    await user.click(within(sidebar).getByRole("button", { name: "系统总览" }));
    await user.click(screen.getByRole("button", { name: "运营效率" }));
    expect(screen.getByRole("heading", { name: "运营效率内容" })).toBeInTheDocument();
  });

  it("retains the actual Agenda progress and parent/child creation through the personal-affairs group", async () => {
    const { calls } = showApp();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "首页内容" });
    await user.click(within(screen.getByRole("navigation")).getByRole("button", { name: "我的事务" }));
    await user.click(screen.getByRole("button", { name: "日程" }));
    await user.click(await screen.findByRole("button", { name: "进度 50%：模块说明" }));
    await waitFor(() => expect(calls.some(({ url, init }) => url === "api/todos/todo-one/update" && JSON.parse(String(init?.body || "{}")).progress === 50)).toBe(true));
    await user.click(screen.getByRole("button", { name: "记下一条" }));
    await user.type(screen.getByLabelText("标题"), "发布准备");
    await user.click(screen.getByRole("button", { name: "添加子项" }));
    await user.type(screen.getByLabelText("子项 1", { exact: true }), "核对成果");
    await user.click(screen.getByRole("button", { name: "记下" }));
    await waitFor(() => {
      const bodies = calls.filter(({ url, init }) => url === "api/todos" && init?.method === "POST").map(({ init }) => JSON.parse(String(init?.body)));
      expect(bodies).toEqual(expect.arrayContaining([
        expect.objectContaining({ title: "发布准备", parent_id: null }),
        expect.objectContaining({ title: "核对成果", parent_id: "todo-created-2" }),
      ]));
    });
    expect(new URLSearchParams(window.location.search).get("page")).toBe("agenda");
  });

  it("keeps viewer-only pages read-only and rejects private views even through pasted URLs", async () => {
    const { calls } = showApp({ ...ME, role: "viewer", readonly: true }, "/?page=workroom");
    await screen.findByRole("heading", { name: "首页内容" });
    const sidebar = screen.getByRole("navigation");
    expect(within(sidebar).queryByRole("button", { name: "我的事务" })).not.toBeInTheDocument();
    expect(within(sidebar).queryByRole("button", { name: "管理" })).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(within(sidebar).getByRole("button", { name: "任务工作台" }));
    expect(screen.queryByRole("button", { name: "任务列表" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "协作空间" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "任务看板" })).toBeInTheDocument();
    await user.click(within(sidebar).getByRole("button", { name: "会话中心" }));
    expect(screen.getByRole("heading", { name: "实时会话内容" })).toHaveAttribute("data-readonly", "true");
    expect(screen.queryByRole("button", { name: "历史会话" })).not.toBeInTheDocument();
    for (const page of ["agenda", "affairs", "taskcenter", "admin", "kingdom"]) {
      route(`/?page=${page}`);
      expect(screen.getByRole("heading", { name: "首页内容" })).toBeInTheDocument();
    }
    expect(calls.some(({ url }) => url.startsWith("api/todos"))).toBe(false);
    expect(calls.some(({ init }) => init?.method === "POST")).toBe(false);
  });

  it("preserves teacher-mode limits and allows administrators only their configured console", async () => {
    showApp({ ...ME, mode: "teacher" }, "/?page=ops");
    await screen.findByRole("heading", { name: "首页内容" });
    const sidebar = screen.getByRole("navigation");
    expect(within(sidebar).getByRole("button", { name: "AI 助理" })).toBeInTheDocument();
    expect(within(sidebar).queryByRole("button", { name: "系统总览" })).not.toBeInTheDocument();
    route("/?page=knowledge");
    expect(screen.queryByRole("heading", { name: "知识内容" })).not.toBeInTheDocument();
    cleanup();
    showApp({ ...ME, role: "admin", site_console: false }, "/?page=kingdom");
    await screen.findByRole("heading", { name: "首页内容" });
    expect(within(screen.getByRole("navigation")).getByRole("button", { name: "管理" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "中枢" })).not.toBeInTheDocument();
    route("/?page=admin");
    expect(screen.getByRole("heading", { name: "管理内容" })).toBeInTheDocument();
    cleanup();
    showApp({ ...ME, role: "admin", site_console: true }, "/?page=kingdom");
    expect(await screen.findByRole("heading", { name: "中枢内容" })).toBeInTheDocument();
  });

  it("restores an actual page from its URL and safely falls back for unknown pages or browser navigation", async () => {
    showApp(ME, "/retinue/?page=board");
    expect(await screen.findByRole("heading", { name: "看板内容" })).toBeInTheDocument();
    route("/retinue/?page=untrusted-page");
    expect(screen.getByRole("heading", { name: "首页内容" })).toBeInTheDocument();
    route("/retinue/?page=ops");
    expect(screen.getByRole("heading", { name: "运营效率内容" })).toBeInTheDocument();
    expect(screen.getByLabelText("当前页面用途")).toHaveTextContent("运营效率汇总");
  });

  it("keeps the legacy hub URL and control modules while forwarding its operations shortcut to the one system view", async () => {
    showApp({ ...ME, role: "admin", site_console: true }, "/retinue/?page=kingdom&q=reader");
    expect(await screen.findByRole("heading", { name: "中枢内容" })).toHaveAttribute("data-view", "overview");
    const shortcut = screen.getByRole("button", { name: "运营效率快捷入口" });
    expect(shortcut).toHaveAttribute("title", "前往系统总览 → 运营效率");
    expect(screen.queryByRole("heading", { name: "运营效率内容" })).not.toBeInTheDocument();
    await userEvent.click(shortcut);
    expect(screen.getAllByRole("heading", { name: "运营效率内容" })).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "中枢内容" })).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/retinue/");
    expect(new URLSearchParams(window.location.search).get("page")).toBe("ops");
    expect(new URLSearchParams(window.location.search).get("q")).toBe("reader");
    expect(within(screen.getByRole("navigation")).getByRole("button", { name: "系统总览" })).toHaveClass("is-active");
    expect(within(screen.getByRole("group", { name: "工作台视图" })).getByRole("button", { name: "运营效率" })).toHaveAttribute("aria-pressed", "true");
    act(() => window.history.back());
    await waitFor(() => expect(screen.getByRole("heading", { name: "中枢内容" })).toBeInTheDocument());
    expect(new URLSearchParams(window.location.search).get("page")).toBe("kingdom");
    const hubTabs = within(document.querySelector(".kingdom-hub-tabs")! as HTMLElement);
    for (const [label, view] of [["智能体", "agents"], ["任务中心", "tasks"], ["技能中心", "skills"], ["基础设施", "infrastructure"]]) {
      await userEvent.click(hubTabs.getByRole("button", { name: label }));
      expect(screen.getByRole("heading", { name: "中枢内容" })).toHaveAttribute("data-view", view);
    }
    await userEvent.click(hubTabs.getByRole("button", { name: "知识库" }));
    expect(screen.getByRole("heading", { name: "中枢知识内容" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运营效率快捷入口" })).toBeInTheDocument();
  });

  it("applies teacher presentation restrictions before admin URLs", async () => {
    showApp({ ...ME, role: "admin", mode: "teacher", site_console: true }, "/?page=admin");
    expect(await screen.findByRole("heading", { name: "首页内容" })).toBeInTheDocument();
    route("/?page=kingdom");
    expect(screen.getByRole("heading", { name: "首页内容" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "中枢内容" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("navigation")).queryByRole("button", { name: "管理" })).not.toBeInTheDocument();
  });

  it("opens the same task in the full collaboration workspace from the actual drawer", async () => {
    const { calls } = showApp();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "查看样例任务" }));
    await user.click(await screen.findByRole("button", { name: "打开完整协作图与时间泳道" }));
    expect(screen.getByRole("heading", { name: "协作图内容" })).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("task")).toBe("task-20260812-001");
    expect(new URLSearchParams(window.location.search).get("page")).toBe("taskflow");
    expect(window.location.hash).toBe("");
    expect(screen.queryByRole("button", { name: "关闭任务详情" })).not.toBeInTheDocument();
    expect(calls.some(({ init }) => init?.method === "POST")).toBe(false);
  });

  it("lets legacy local drawers open the full view while a viewer receives no task mutation controls", async () => {
    window.history.replaceState(null, "", "/retinue/?page=collab&q=reader");
    const viewer = { ...ME, role: "viewer" as const, readonly: true };
    const { calls } = seed(viewer);
    const onClose = vi.fn();
    render(<ThemeProvider><TaskDrawer taskId="task-20260812-001" me={viewer} actors={[]} onClose={onClose} onChanged={vi.fn()} /></ThemeProvider>);
    const entry = await screen.findByRole("button", { name: "打开完整协作图与时间泳道" });
    expect(screen.queryByRole("button", { name: "→ 已完成" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "改派" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "上报进度" })).not.toBeInTheDocument();
    await userEvent.setup().click(entry);
    expect(onClose).toHaveBeenCalledOnce();
    expect(new URLSearchParams(window.location.search).get("page")).toBe("taskflow");
    expect(new URLSearchParams(window.location.search).get("task")).toBe("task-20260812-001");
    expect(new URLSearchParams(window.location.search).get("q")).toBe("reader");
    expect(window.location.pathname).toBe("/retinue/");
    expect(calls.some(({ init }) => init?.method === "POST")).toBe(false);
  });
});
