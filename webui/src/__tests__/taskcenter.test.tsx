import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskCenter from "../pages/TaskCenter";
import { TASKS_CHANGED_EVENT } from "../components/DeepTaskDrawer";
import { AGENT_ONE, ME, makeSummary, makeTask, mockFetch } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TaskCenter 筛选", () => {
  it("按关键词过滤任务行，点击行打开深链接", async () => {
    const alpha = makeTask({ id: "task-20260812-001", title: "整理语料" });
    const beta = makeTask({ id: "task-20260812-002", title: "修复看板" });
    mockFetch({
      "api/summary": () => ({
        body: makeSummary({ tasks: [alpha, beta], actors: [AGENT_ONE] }),
      }),
    });
    const onOpenTask = vi.fn();
    const user = userEvent.setup();

    render(<TaskCenter me={ME} onOpenTask={onOpenTask} />);

    await screen.findByText("整理语料");
    expect(screen.getByText("修复看板")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText(/搜索标题/), "看板");
    await waitFor(() => {
      expect(screen.queryByText("整理语料")).not.toBeInTheDocument();
    });
    expect(screen.getByText("修复看板")).toBeInTheDocument();

    await user.click(screen.getByText("修复看板"));
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-002");
  });

  it("接口失败显示错误与重试，重试成功后恢复", async () => {
    const alpha = makeTask({ id: "task-20260812-001", title: "整理语料" });
    let fail = true;
    mockFetch({
      "api/summary": () =>
        fail
          ? { status: 500, body: { detail: "boom" } }
          : { body: makeSummary({ tasks: [alpha], actors: [AGENT_ONE] }) },
    });
    const user = userEvent.setup();

    render(<TaskCenter me={ME} onOpenTask={() => undefined} />);

    const retry = await screen.findByRole("button", { name: "重试" });
    expect(screen.getByRole("alert")).toHaveTextContent("boom");

    fail = false;
    await user.click(retry);
    expect(await screen.findByText("整理语料")).toBeInTheDocument();
  });

  it("任务变更后按 updated_since 增量合并，保留已有行", async () => {
    const alpha = makeTask({
      id: "task-20260812-001",
      title: "整理语料",
      created_at: "2026-08-12T08:00:00",
    });
    const beta = makeTask({
      id: "task-20260812-002",
      title: "修复看板",
      created_at: "2026-08-12T09:00:00",
    });
    const betaRenamed = { ...beta, title: "修复看板 v2" };
    const { calls } = mockFetch({
      "api/summary": (init, url) =>
        (url ?? "").includes("updated_since=")
          ? {
              body: makeSummary({
                partial: true,
                generated_at: "2026-08-12T11:00:00+00:00",
                tasks: [betaRenamed],
                actors: [AGENT_ONE],
              }),
            }
          : {
              body: makeSummary({ tasks: [alpha, beta], actors: [AGENT_ONE] }),
            },
    });

    render(<TaskCenter me={ME} onOpenTask={() => undefined} />);
    await screen.findByText("整理语料");
    expect(calls.every(({ url }) => !url.includes("updated_since="))).toBe(true);

    await act(async () => {
      window.dispatchEvent(new Event(TASKS_CHANGED_EVENT));
    });

    await waitFor(() => {
      expect(calls.some(({ url }) => url.includes("updated_since="))).toBe(true);
    });
    // The changed row replaced its cached copy; untouched rows survive.
    expect(await screen.findByText("修复看板 v2")).toBeInTheDocument();
    expect(screen.getByText("整理语料")).toBeInTheDocument();
    expect(screen.queryByText("修复看板", { exact: true })).not.toBeInTheDocument();
  });
});
