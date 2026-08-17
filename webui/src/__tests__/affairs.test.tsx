import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Affairs from "../pages/Affairs";
import { ThemeProvider } from "../theme";
import type { TodoHome, TodoItem } from "../types";
import { mockFetch } from "./helpers";

function makeItem(overrides: Partial<TodoItem> = {}): TodoItem {
  return {
    id: "todo-1",
    owner_user_id: 1,
    title: "样例事项",
    notes: "",
    status: "open",
    due_at: null,
    remind_at: null,
    proposal_id: null,
    source_channel: null,
    source_backlink: null,
    task_id: null,
    created_at: "2026-08-12T09:00:00Z",
    updated_at: "2026-08-12T09:00:00Z",
    ...overrides,
  };
}

function makeHome(overrides: Partial<TodoHome> = {}): TodoHome {
  return {
    pending_proposals: [
      {
        id: "proposal-1",
        owner_user_id: 1,
        proposed_by: "xiaohei",
        title: "买牛奶",
        notes: "",
        due_at: "2026-08-14",
        remind_at: null,
        source_channel: "feishu",
        source_backlink: null,
        status: "pending",
        todo_item_id: null,
        created_at: "2026-08-12T09:00:00Z",
        updated_at: "2026-08-12T09:00:00Z",
      },
    ],
    due_today: [makeItem({ id: "todo-1", title: "交周报", due_at: "2026-08-13" })],
    overdue: [makeItem({ id: "todo-2", title: "还书", due_at: "2026-08-10" })],
    waiting_on_others: [
      {
        ...makeItem({ id: "todo-3", title: "审阅稿件", status: "promoted", task_id: "task-x" }),
        task_holder: "throne-grok",
        task_status: "doing",
      },
    ],
    ...overrides,
  };
}

/** Route table shared by the tests; `state` lets a test mutate what the next
 * aggregate fetch returns after an action lands. */
function stubTodoRoutes(state: { home: TodoHome; todos: TodoItem[] }) {
  return mockFetch({
    "api/todos/home": () => ({ body: state.home }),
    "api/todos/proposals/": () => ({ body: {} }),
    "api/todos": (_init, url) => {
      if (url && /\/(complete|snooze|cancel)$/.test(url)) return { body: {} };
      return { body: { todos: state.todos } };
    },
  });
}

function renderAffairs(theme: "neutral" | "court" = "neutral") {
  const onOpenTask = vi.fn();
  render(
    <ThemeProvider initialTheme={theme}>
      <Affairs onOpenTask={onOpenTask} />
    </ThemeProvider>
  );
  return { onOpenTask };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Affairs 聚合分区", () => {
  it("renders all five lanes with their items", async () => {
    stubTodoRoutes({
      home: makeHome(),
      todos: [
        makeItem({ id: "todo-3", title: "审阅稿件", status: "promoted", task_id: "task-x" }),
        makeItem({ id: "todo-4", title: "旧事项", status: "promoted", task_id: "task-y" }),
      ],
    });
    renderAffairs();

    expect(await screen.findByRole("region", { name: "待确认提案" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "今天到期" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "已逾期" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "等待他人" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "已升级共享任务" })).toBeInTheDocument();

    expect(screen.getByText("买牛奶")).toBeInTheDocument();
    expect(screen.getByText("交周报")).toBeInTheDocument();
    expect(screen.getByText("还书")).toBeInTheDocument();
    // 审阅稿件 appears in both 等待他人 and 已升级共享任务 lanes.
    expect(screen.getAllByText("审阅稿件").length).toBe(2);
    expect(screen.getByText("旧事项")).toBeInTheDocument();
  });

  it("renders empty states when every lane is empty", async () => {
    stubTodoRoutes({
      home: makeHome({
        pending_proposals: [],
        due_today: [],
        overdue: [],
        waiting_on_others: [],
      }),
      todos: [],
    });
    renderAffairs();

    expect(await screen.findByText("没有待确认的提案")).toBeInTheDocument();
    expect(screen.getByText("今天没有到期事项")).toBeInTheDocument();
    expect(screen.getByText("没有逾期事项")).toBeInTheDocument();
    expect(screen.getByText("没有等待他人的事项")).toBeInTheDocument();
    expect(screen.getByText("还没有升级到共享看板的事项")).toBeInTheDocument();
  });

  it("uses theme vocab for lane titles (court preset)", async () => {
    stubTodoRoutes({ home: makeHome(), todos: [] });
    renderAffairs("court");

    expect(await screen.findByRole("region", { name: "待批奏章" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "候他人办结" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "已升朝议" })).toBeInTheDocument();
  });
});

describe("Affairs 操作", () => {
  it("confirms a proposal through the backend endpoint and refetches", async () => {
    const state = {
      home: makeHome(),
      todos: [] as TodoItem[],
    };
    const { calls } = stubTodoRoutes(state);
    const user = userEvent.setup();
    renderAffairs();

    const confirmButton = await screen.findByRole("button", { name: "确认" });
    await user.click(confirmButton);

    await waitFor(() => {
      expect(
        calls.some(
          ({ url, init }) =>
            url === "api/todos/proposals/proposal-1/confirm" && init?.method === "POST"
        )
      ).toBe(true);
    });
    // The aggregate is refetched after the mutation lands.
    await waitFor(() => {
      expect(calls.filter(({ url }) => url === "api/todos/home").length).toBeGreaterThanOrEqual(2);
    });
  });

  it("rejects a proposal with an optional note", async () => {
    const state = { home: makeHome(), todos: [] as TodoItem[] };
    const { calls } = stubTodoRoutes(state);
    const user = userEvent.setup();
    renderAffairs();

    await user.click(await screen.findByRole("button", { name: "驳回" }));
    const noteInput = screen.getByRole("textbox", { name: "驳回理由:买牛奶" });
    await user.type(noteInput, "不需要");
    await user.click(screen.getByRole("button", { name: "确认驳回" }));

    await waitFor(() => {
      const rejectCall = calls.find(
        ({ url }) => url === "api/todos/proposals/proposal-1/reject"
      );
      expect(rejectCall).toBeDefined();
      expect(JSON.parse(String(rejectCall?.init?.body))).toEqual({ note: "不需要" });
    });
  });

  it("completes a due-today item through the backend endpoint", async () => {
    const state = { home: makeHome(), todos: [] as TodoItem[] };
    const { calls } = stubTodoRoutes(state);
    const user = userEvent.setup();
    renderAffairs();

    const dueLane = await screen.findByRole("region", { name: "今天到期" });
    const completeButton = within(dueLane).getByRole("button", { name: "完成" });
    await user.click(completeButton);

    await waitFor(() => {
      expect(
        calls.some(
          ({ url, init }) => url === "api/todos/todo-1/complete" && init?.method === "POST"
        )
      ).toBe(true);
    });
  });

  it("opens the linked shared card from the waiting lane", async () => {
    stubTodoRoutes({ home: makeHome(), todos: [] });
    const user = userEvent.setup();
    const { onOpenTask } = renderAffairs();

    const waitingLane = await screen.findByRole("region", { name: "等待他人" });
    await user.click(within(waitingLane).getByRole("button", { name: /审阅稿件/ }));
    expect(onOpenTask).toHaveBeenCalledWith("task-x");
  });
});
