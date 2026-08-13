import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskDrawer from "../components/TaskDrawer";
import { AGENT_ONE, AGENT_TWO, ME, makeTask, mockFetch } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TaskDrawer 认领", () => {
  it("挂单卡点击接单后调用 claim 接口", async () => {
    const task = makeTask({
      open_dispatch: true,
      holder: "agent-two",
    });
    const { calls } = mockFetch({
      "api/tasks/task-20260812-001": () => ({ body: task }),
    });
    const user = userEvent.setup();

    render(
      <TaskDrawer
        taskId={task.id}
        me={ME}
        actors={[AGENT_ONE, AGENT_TWO]}
        onClose={() => undefined}
        onChanged={() => undefined}
      />
    );

    const claimButton = await screen.findByRole("button", { name: "接单" });
    await user.click(claimButton);

    await waitFor(() => {
      const claim = calls.find(
        (call) =>
          call.url === "api/tasks/task-20260812-001/claim" &&
          call.init?.method === "POST"
      );
      expect(claim).toBeDefined();
    });
  });
});

describe("TaskDrawer 状态更新", () => {
  it("进行中的卡点击流转后调用 update 接口", async () => {
    const task = makeTask({ status: "doing", progress: 40 });
    const { calls } = mockFetch({
      "api/tasks/task-20260812-001": () => ({ body: task }),
    });
    const user = userEvent.setup();

    render(
      <TaskDrawer
        taskId={task.id}
        me={ME}
        actors={[AGENT_ONE]}
        onClose={() => undefined}
        onChanged={() => undefined}
      />
    );

    const doneButton = await screen.findByRole("button", { name: "→ 已完成" });
    await user.click(doneButton);

    await waitFor(() => {
      const update = calls.find(
        (call) =>
          call.url === "api/tasks/task-20260812-001/update" &&
          call.init?.method === "POST"
      );
      expect(update).toBeDefined();
      const body = JSON.parse(String(update!.init?.body));
      expect(body.status).toBe("done");
      expect(typeof body.note).toBe("string");
    });
  });

  it("Escape 关闭抽屉", async () => {
    const task = makeTask({ status: "doing" });
    mockFetch({ "api/tasks/task-20260812-001": () => ({ body: task }) });
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(
      <TaskDrawer
        taskId={task.id}
        me={ME}
        actors={[AGENT_ONE]}
        onClose={onClose}
        onChanged={() => undefined}
      />
    );

    await screen.findByRole("button", { name: "→ 已完成" });
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("TaskDrawer 执行时间线", () => {
  it("尝试与关联会话合入同一条时间轴", async () => {
    const task = makeTask({
      status: "doing",
      attempts: [
        {
          id: "attempt-1",
          seq: 1,
          reporter: { kind: "actor", id: "agent-one", duty: null },
          started_at: "2026-08-12T09:00:00Z",
          ended_at: "2026-08-12T09:30:00Z",
          outcome: "succeeded",
          reason: null,
          exit_status: null,
          reported_at: "2026-08-12T09:30:01Z",
        },
      ],
    });
    mockFetch({
      "api/tasks/task-20260812-001": () => ({ body: task }),
      "api/sessions": () => ({
        body: [
          {
            id: 42,
            actor_id: "agent-one",
            actor_name: "Agent One",
            runtime: "kimi",
            node: "throne",
            title: "样例施工会话",
            summary: "",
            privacy: "metadata",
            cursor: 3,
            message_count: 12,
            messages: [],
            task_id: task.id,
            task_title: task.title,
            resume_capable: false,
            started_at: "2026-08-12T10:00:00Z",
            updated_at: "2026-08-12T10:20:00Z",
            synced_at: "2026-08-12T10:21:00Z",
          },
        ],
      }),
    });

    render(
      <TaskDrawer
        taskId={task.id}
        me={ME}
        actors={[AGENT_ONE]}
        onClose={() => undefined}
        onChanged={() => undefined}
      />
    );

    expect(await screen.findByText("执行时间线(2)")).toBeInTheDocument();
    expect(await screen.findByText("样例施工会话")).toBeInTheDocument();
    expect(screen.getByText(/尝试 #1/)).toBeInTheDocument();
    expect(screen.getByText("12 条消息")).toBeInTheDocument();
  });

  it("会话接口失败时降级为仅尝试记录", async () => {
    const task = makeTask({
      status: "doing",
      attempts: [
        {
          id: "attempt-1",
          seq: 1,
          reporter: { kind: "actor", id: "agent-one", duty: null },
          started_at: "2026-08-12T09:00:00Z",
          ended_at: "2026-08-12T09:30:00Z",
          outcome: "succeeded",
          reason: null,
          exit_status: null,
          reported_at: "2026-08-12T09:30:01Z",
        },
      ],
    });
    mockFetch({
      "api/tasks/task-20260812-001": () => ({ body: task }),
      "api/sessions": () => ({ status: 500, body: { detail: "boom" } }),
    });

    render(
      <TaskDrawer
        taskId={task.id}
        me={ME}
        actors={[AGENT_ONE]}
        onClose={() => undefined}
        onChanged={() => undefined}
      />
    );

    expect(await screen.findByText("执行时间线(1)")).toBeInTheDocument();
    expect(screen.getByText(/尝试 #1/)).toBeInTheDocument();
  });
});
