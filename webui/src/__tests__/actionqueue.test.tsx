import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ActionQueue from "../components/ActionQueue";
import { AGENT_ONE, AGENT_TWO, makeSummary, makeTask, mockFetch } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PENDING_APPROVAL = {
  id: 7,
  task_id: "task-20260812-005",
  task_title: "待审批的卡",
  stage_index: 1,
  stage_name: "女王闸",
  status: "pending" as const,
  requested_by: "agent-one",
  decided_by: null,
  decision_note: "",
  created_at: "2026-08-12T09:00:00",
  decided_at: null,
};

describe("ActionQueue 行动队列", () => {
  it("五个泳道渲染，条目点击打开深链接", async () => {
    const blocked = makeTask({
      id: "task-20260812-003",
      title: "被阻塞的卡",
      status: "blocked",
      blocked_reason: "等上游数据",
    });
    const lost = makeTask({
      id: "task-20260812-004",
      title: "失联持有的卡",
      status: "doing",
      holder: "agent-two",
    });
    const { calls } = mockFetch({
      "api/summary": () => ({
        body: makeSummary({
          lanes: {
            decisions: { count: 1, items: [PENDING_APPROVAL] },
            due_today: { count: 0, items: [] },
            overdue: { count: 0, items: [] },
            blocked: { count: 1, items: [blocked] },
            lost_executors: {
              count: 1,
              items: [{ task: lost, actor: AGENT_TWO }],
            },
          },
          approvals: [PENDING_APPROVAL],
          actors: [AGENT_ONE, AGENT_TWO],
          tasks: null,
        }),
      }),
    });
    const onOpenTask = vi.fn();
    const user = userEvent.setup();

    render(<ActionQueue onOpenTask={onOpenTask} />);

    expect(await screen.findByText("被阻塞的卡")).toBeInTheDocument();
    // Lost-executor rows lead with the agent name; the task title sits in the
    // meta line together with the last-seen stamp, so match a substring.
    expect(screen.getByText(/失联持有的卡/)).toBeInTheDocument();
    expect(screen.getByText("待审批的卡")).toBeInTheDocument();
    expect(screen.getByText("今日到期")).toBeInTheDocument();
    expect(screen.getByText("已逾期")).toBeInTheDocument();

    // The queue no longer pulls the full task table for the first screen.
    expect(calls[0].url).toContain("api/summary?");
    expect(calls[0].url).toContain("include_tasks=false");
    expect(calls[0].url).toContain("today=");

    await user.click(screen.getByText("被阻塞的卡"));
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-003");

    await user.click(screen.getByText("待审批的卡"));
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-005");
  });

  it("摘要接口失败降级为重试，恢复后泳道照常渲染", async () => {
    const blocked = makeTask({ status: "blocked", blocked_reason: "x" });
    let fail = true;
    mockFetch({
      "api/summary": () =>
        fail
          ? { status: 500, body: { detail: "boom" } }
          : {
              body: makeSummary({
                lanes: {
                  decisions: { count: 0, items: [] },
                  due_today: { count: 0, items: [] },
                  overdue: { count: 0, items: [] },
                  blocked: { count: 1, items: [blocked] },
                  lost_executors: { count: 0, items: [] },
                },
                tasks: null,
              }),
            },
    });
    const user = userEvent.setup();

    render(<ActionQueue onOpenTask={() => undefined} />);

    const retry = await screen.findByRole("button", { name: "重试" });
    expect(screen.getByRole("alert")).toHaveTextContent("boom");

    fail = false;
    await user.click(retry);
    expect(await screen.findByText("样例任务")).toBeInTheDocument();
  });
});
