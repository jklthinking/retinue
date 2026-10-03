import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "../pages/Agenda";
import { ThemeProvider } from "../theme";
import type { TodoHome, TodoItem } from "../types";
import { localTodayISO } from "../types";
import { ME, mockFetch } from "./helpers";

function shiftLocalISO(days: number): string {
  const now = new Date();
  now.setDate(now.getDate() + days);
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function makeItem(overrides: Partial<TodoItem> = {}): TodoItem {
  return {
    id: "todo-1",
    owner_user_id: 1,
    title: "样例事项",
    notes: "",
    status: "open",
    due_at: null,
    event_on: null,
    parent_id: null,
    progress: 0,
    children: [],
    remind_at: null,
    proposal_id: null,
    source_channel: null,
    source_backlink: null,
    task_id: null,
    created_at: "2026-08-21T09:00:00Z",
    updated_at: "2026-08-21T09:00:00Z",
    ...overrides,
  };
}

function makeHome(overrides: Partial<TodoHome> = {}): TodoHome {
  const today = localTodayISO();
  const tomorrow = shiftLocalISO(1);
  return {
    pending_proposals: [
      {
        id: "proposal-1",
        owner_user_id: 1,
        proposed_by: "xiaohei",
        title: "买牛奶",
        notes: "",
        due_at: today,
        remind_at: null,
        source_channel: "feishu",
        source_backlink: null,
        status: "pending",
        todo_item_id: null,
        created_at: "2026-08-21T09:00:00Z",
        updated_at: "2026-08-21T09:00:00Z",
      },
    ],
    due_today: [
      makeItem({
        id: "todo-script",
        title: "写完讲稿",
        due_at: today,
        parent_id: "todo-share",
        progress: 60,
      }),
    ],
    overdue: [makeItem({ id: "todo-overdue", title: "还书", due_at: "2026-08-10", progress: 0 })],
    waiting_on_others: [],
    events_tomorrow: [
      makeItem({
        id: "todo-share",
        title: "分享会",
        event_on: tomorrow,
        children: [
          makeItem({
            id: "todo-script",
            title: "写完讲稿",
            due_at: today,
            parent_id: "todo-share",
            progress: 60,
          }),
        ],
      }),
    ],
    anytime: [makeItem({ id: "todo-notes", title: "整理配色笔记" })],
    ...overrides,
  };
}

function stubAgenda(state: { home: TodoHome; todos: TodoItem[] }) {
  let serial = 200;
  return mockFetch({
    "api/todos/home": () => ({ body: state.home }),
    "api/todos": (init, url) => {
      const path = url || "";
      if (/\/complete$/.test(path)) return { body: makeItem({ id: "done", status: "done" }) };
      if (/\/update$/.test(path)) {
        const body = JSON.parse(String(init?.body || "{}")) as { progress?: number };
        return { body: makeItem({ id: "updated", progress: body.progress ?? 0 }) };
      }
      if ((init?.method || "GET").toUpperCase() === "POST") {
        const body = JSON.parse(String(init?.body || "{}")) as Partial<TodoItem> & { title: string };
        serial += 1;
        const item = makeItem({
          id: `todo-new-${serial}`,
          title: body.title,
          due_at: body.due_at ?? null,
          event_on: body.event_on ?? null,
          parent_id: body.parent_id ?? null,
          progress: body.progress ?? 0,
        });
        state.todos = [item, ...state.todos];
        return { body: item };
      }
      return { body: { todos: state.todos } };
    },
  });
}

function renderHome() {
  const onNavigate = vi.fn();
  render(
    <ThemeProvider>
      <Home
        me={ME}
        onNavigate={onNavigate}
        onOpenTask={vi.fn()}
        onOpenSession={vi.fn()}
      />
    </ThemeProvider>
  );
  return { onNavigate };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Agenda Home", () => {
  it("uses the greeting as H1 and does not show command-home fleet metrics", async () => {
    stubAgenda({ home: makeHome(), todos: [] });
    renderHome();

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Operator");
    expect(screen.queryByText(/COMMAND HOME/)).not.toBeInTheDocument();
    expect(screen.queryByText("RETINUE · COMMAND HOME")).not.toBeInTheDocument();
    expect(screen.queryByText("在线智能体")).not.toBeInTheDocument();
    expect(screen.queryByText("知识源")).not.toBeInTheDocument();
    expect(screen.queryByText("派单协调")).not.toBeInTheDocument();
    expect(screen.getByText("今日必须结束")).toBeInTheDocument();
    expect(screen.getByText("逾期")).toBeInTheDocument();
    expect(screen.getByText("待确认提案")).toBeInTheDocument();
    expect(document.querySelector(".rt-agenda-hero__bar")).not.toBeNull();
    expect(screen.getByRole("button", { name: "记下一条" })).toHaveClass("rt-button--gold");
  });

  it("puts overdue above due-today on the today tab", async () => {
    stubAgenda({ home: makeHome(), todos: [] });
    renderHome();

    const overdue = await screen.findByRole("region", { name: "逾期" });
    const due = screen.getByRole("region", { name: "必须今天结束" });
    expect(within(overdue).getByText("还书")).toBeInTheDocument();
    expect(within(due).getByText("写完讲稿")).toBeInTheDocument();
    expect(overdue.compareDocumentPosition(due) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("整理配色笔记")).not.toBeInTheDocument();
    expect(screen.queryByText("分享会")).not.toBeInTheDocument();
  });

  it("shows tomorrow events and anytime on their tabs", async () => {
    const user = userEvent.setup();
    stubAgenda({
      home: makeHome(),
      todos: [makeItem({ id: "todo-bag", title: "收拾包", due_at: shiftLocalISO(1) })],
    });
    renderHome();
    await screen.findByText("写完讲稿");

    await user.click(screen.getByRole("tab", { name: "明天" }));
    expect(screen.getByText("分享会")).toBeInTheDocument();
    expect(screen.getAllByText("写完讲稿").length).toBeGreaterThan(0);
    expect(screen.getByText("收拾包")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "结束这场：分享会" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "随时" }));
    expect(screen.getByText("整理配色笔记")).toBeInTheDocument();
    expect(screen.queryByText("分享会")).not.toBeInTheDocument();
  });

  it("completes an item with the owner checkbox", async () => {
    const user = userEvent.setup();
    const { calls } = stubAgenda({ home: makeHome(), todos: [] });
    renderHome();

    const box = await screen.findByRole("checkbox", { name: "完成：写完讲稿" });
    await user.click(box);

    await waitFor(() => {
      expect(
        calls.some(
          ({ url, init }) => url === "api/todos/todo-script/complete" && init?.method === "POST"
        )
      ).toBe(true);
    });
  });

  it("sets progress with the 0/50/100 controls", async () => {
    const user = userEvent.setup();
    const { calls } = stubAgenda({ home: makeHome(), todos: [] });
    renderHome();

    await user.click(await screen.findByRole("button", { name: "进度 50%：写完讲稿" }));
    await waitFor(() => {
      const update = calls.find(({ url }) => url === "api/todos/todo-script/update");
      expect(update).toBeDefined();
      expect(JSON.parse(String(update?.init?.body))).toEqual({ progress: 50 });
    });
  });

  it("creates a parent then children from 记下一条", async () => {
    const user = userEvent.setup();
    const { calls } = stubAgenda({ home: makeHome(), todos: [] });
    renderHome();
    const today = localTodayISO();

    await user.click(await screen.findByRole("button", { name: "记下一条" }));
    await user.type(screen.getByLabelText("标题"), "分享会稿件场");
    await user.click(screen.getByRole("button", { name: "添加子项" }));
    expect((screen.getByLabelText("子项 1 必须哪天结束") as HTMLInputElement).value).toBe(today);
    await user.type(screen.getByLabelText("子项 1", { exact: true }), "写稿");
    await user.click(screen.getByRole("button", { name: "记下" }));

    await waitFor(() => {
      const creates = calls.filter(
        ({ url, init }) => url === "api/todos" && init?.method === "POST"
      );
      expect(creates.length).toBeGreaterThanOrEqual(2);
      const bodies = creates.map(({ init }) => JSON.parse(String(init?.body)));
      expect(bodies[0]).toMatchObject({ title: "分享会稿件场", parent_id: null });
      expect(bodies[1]).toMatchObject({
        title: "写稿",
        parent_id: expect.stringMatching(/^todo-new-/),
        due_at: today,
      });
    });
  });

  it("asks to close the parent when every child is done", async () => {
    const user = userEvent.setup();
    const tomorrow = shiftLocalISO(1);
    const { calls } = stubAgenda({
      home: makeHome({
        due_today: [],
        events_tomorrow: [
          makeItem({
            id: "todo-share",
            title: "分享会",
            event_on: tomorrow,
            ready_to_close: true,
            children: [
              makeItem({
                id: "todo-script",
                title: "写完讲稿",
                status: "done",
                parent_id: "todo-share",
              }),
              makeItem({
                id: "todo-slides",
                title: "做好课件",
                status: "done",
                parent_id: "todo-share",
              }),
            ],
          }),
        ],
      }),
      todos: [],
    });
    renderHome();

    await user.click(await screen.findByRole("tab", { name: "明天" }));
    expect(screen.getByText("子项都做完了，这场是否也结束了？")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "结束这场：分享会" }));
    await waitFor(() => {
      expect(
        calls.some(
          ({ url, init }) => url === "api/todos/todo-share/complete" && init?.method === "POST"
        )
      ).toBe(true);
    });
  });

  it("links the right rail to 我的事务", async () => {
    const user = userEvent.setup();
    stubAgenda({ home: makeHome(), todos: [] });
    const { onNavigate } = renderHome();
    await screen.findByText("写完讲稿");
    await user.click(screen.getByRole("button", { name: /我的事务/ }));
    expect(onNavigate).toHaveBeenCalledWith("affairs");
  });
});
