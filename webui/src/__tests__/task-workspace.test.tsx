import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskFlowPage from "../pages/TaskFlowPage";
import { AGENT_ONE, ME, makeSummary, makeTask, mockFetch } from "./helpers";

vi.mock("../components/TaskCollaboration", () => ({ default: ({ taskId, layout }: { taskId: string; layout: string }) => <div aria-label="任务协作证据">{taskId} · {layout}</div> }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

describe("每任务协作工作台", () => {
  const first = makeTask({ title: "阅读器模块", status: "doing" });
  const second = makeTask({ id: "task-20260812-002", title: "词库模块", status: "done", archived: true });
  const seed = () => mockFetch({ "api/summary": () => ({ body: makeSummary({ tasks: [first, second], actors: [AGENT_ONE] }) }) });

  it("归档任务也能选中并显示全宽协作，刷新保持选中的任务", async () => {
    window.history.replaceState(null, "", "/?page=taskflow&task=task-20260812-002");
    seed();
    const open = vi.fn();
    render(<TaskFlowPage me={ME} onOpenTask={open} />);
    await screen.findByRole("button", { name: /词库模块/ });
    expect(screen.getByLabelText("任务协作证据")).toHaveTextContent("task-20260812-002 · wide");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "打开任务卡" }));
    expect(open).toHaveBeenCalledWith(second.id);
    await user.click(screen.getByRole("button", { name: /阅读器模块/ }));
    expect(screen.getByLabelText("任务协作证据")).toHaveTextContent(first.id);
    expect(new URLSearchParams(window.location.search).get("task")).toBe(first.id);
  });

  it("搜索和状态可恢复，未找到时提示筛选原因", async () => {
    window.history.replaceState(null, "", "/?page=taskflow&q=词库&status=done");
    seed();
    render(<TaskFlowPage me={ME} onOpenTask={vi.fn()} />);
    await screen.findByRole("button", { name: /词库模块/ });
    expect(screen.queryByRole("button", { name: /阅读器模块/ })).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("搜索协作任务"));
    await user.type(screen.getByLabelText("搜索协作任务"), "无结果");
    await waitFor(() => expect(screen.getByText(/没有符合筛选的任务/)).toBeInTheDocument());
    expect(new URLSearchParams(window.location.search).get("q")).toBe("无结果");
  });
});
