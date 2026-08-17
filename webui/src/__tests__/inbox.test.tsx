import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import InboxLanes from "../components/InboxLanes";
import { ThemeProvider } from "../theme";
import type { InboxInfo } from "../types";
import { makeTask, mockFetch } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PENDING_APPROVAL = {
  id: 7,
  task_id: "task-20260812-005",
  task_title: "待审批的卡",
  stage_index: 1,
  stage_name: "审批门",
  status: "pending" as const,
  requested_by: "agent-one",
  decided_by: null,
  decision_note: "",
  created_at: "2026-08-12T09:00:00",
  decided_at: null,
};

function makeInbox(overrides: Partial<InboxInfo> = {}): InboxInfo {
  return {
    generated_at: "2026-08-12T10:00:00+00:00",
    today: "2026-08-12",
    lanes: {
      decisions: { count: 0, items: [] },
      reviews: { count: 0, items: [] },
      blocked: { count: 0, items: [] },
      stale: { count: 0, items: [] },
    },
    digest: { date: "2026-08-12", enabled: false, registered: 0, owners: 0, delivered: 0 },
    ...overrides,
  };
}

function renderInbox(theme: "neutral" | "court" = "neutral") {
  const onOpenTask = vi.fn();
  render(
    <ThemeProvider initialTheme={theme}>
      <InboxLanes onOpenTask={onOpenTask} />
    </ThemeProvider>
  );
  return { onOpenTask };
}

describe("InboxLanes 收件箱泳道", () => {
  it("四个泳道渲染数量与条目，点击跳对应卡", async () => {
    const blocked = makeTask({
      id: "task-20260812-003",
      title: "被阻塞的卡",
      status: "blocked",
      blocked_reason: "等上游数据",
    });
    const staleTask = makeTask({
      id: "task-20260812-004",
      title: "超期的在制卡",
      status: "doing",
      holder: "agent-two",
    });
    const { calls } = mockFetch({
      "api/inbox": () => ({
        body: makeInbox({
          lanes: {
            decisions: { count: 1, items: [PENDING_APPROVAL] },
            reviews: {
              count: 1,
              items: [
                {
                  task_id: "task-20260812-006",
                  task_title: "待质检回复的卡",
                  review_id: "review-abc",
                  author: "operator",
                  body: "请复核数据口径",
                  created_at: "2026-08-12T09:30:00",
                },
              ],
            },
            blocked: { count: 1, items: [blocked] },
            stale: {
              count: 1,
              items: [{ task: staleTask, reasons: ["overdue"] }],
            },
          },
        }),
      }),
    });
    const { onOpenTask } = renderInbox();
    const user = userEvent.setup();

    expect(await screen.findByText("待审批的卡")).toBeInTheDocument();
    expect(screen.getByText("待质检回复的卡")).toBeInTheDocument();
    expect(screen.getByText("被阻塞的卡")).toBeInTheDocument();
    expect(screen.getByText("超期的在制卡")).toBeInTheDocument();
    // Lane names come from the neutral theme vocabulary.
    expect(screen.getByText("待拍板")).toBeInTheDocument();
    expect(screen.getByText("待质检回复")).toBeInTheDocument();
    expect(screen.getByText("阻塞待解")).toBeInTheDocument();
    expect(screen.getByText("超期未动")).toBeInTheDocument();
    expect(screen.getByText(/等上游数据/)).toBeInTheDocument();
    expect(screen.getByText(/已逾期/)).toBeInTheDocument();

    expect(calls[0].url).toContain("api/inbox?");

    await user.click(screen.getByText("待审批的卡"));
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-005");

    await user.click(screen.getByText("待质检回复的卡"));
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-006");

    await user.click(screen.getByText("被阻塞的卡"));
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-003");

    await user.click(screen.getByText("超期的在制卡"));
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-004");
  });

  it("宫廷主题下泳道名切换为宫廷词表", async () => {
    mockFetch({ "api/inbox": () => ({ body: makeInbox() }) });

    renderInbox("court");

    expect(await screen.findByText("待圣裁")).toBeInTheDocument();
    expect(screen.getByText("待复质检")).toBeInTheDocument();
    expect(screen.getByText("壅塞待疏")).toBeInTheDocument();
    expect(screen.getByText("逾限未动")).toBeInTheDocument();
  });

  it("收件箱接口失败降级为重试，恢复后照常渲染", async () => {
    let fail = true;
    mockFetch({
      "api/inbox": () =>
        fail
          ? { status: 500, body: { detail: "boom" } }
          : {
              body: makeInbox({
                lanes: {
                  decisions: { count: 1, items: [PENDING_APPROVAL] },
                  reviews: { count: 0, items: [] },
                  blocked: { count: 0, items: [] },
                  stale: { count: 0, items: [] },
                },
              }),
            },
    });
    const user = userEvent.setup();

    renderInbox();

    const retry = await screen.findByRole("button", { name: "重试" });
    expect(screen.getByRole("alert")).toHaveTextContent("boom");

    fail = false;
    await user.click(retry);
    expect(await screen.findByText("待审批的卡")).toBeInTheDocument();
  });
});
