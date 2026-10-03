import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { readFlowTask, readPage, usePageNavigation, writeNavigation } from "../lib/navigation";

afterEach(() => window.history.replaceState(null, "", "/"));

describe("工作台导航与分享", () => {
  it("刷新可恢复模块、任务和筛选，同时保留旧任务抽屉链接", () => {
    window.history.replaceState(null, "", "/retinue/?page=taskflow&task=task-20260812-002&q=reader&status=doing#/task/task-20260812-001");
    expect(readPage()).toBe("taskflow");
    expect(readFlowTask()).toBe("task-20260812-002");
    writeNavigation({ page: "board" });
    expect(readPage()).toBe("board");
    expect(readFlowTask()).toBe("task-20260812-002");
    expect(window.location.hash).toBe("#/task/task-20260812-001");
    expect(window.location.pathname).toBe("/retinue/");
    expect(new URLSearchParams(window.location.search).get("q")).toBe("reader");
  });

  it("无效模块与任务编号不会进入未知页面", () => {
    expect(readPage("?page=foreign")).toBe("home");
    expect(readFlowTask("?task=foreign-task")).toBeNull();
    expect(readPage("?page=agenda")).toBe("agenda");
  });

  it("当前窗口导航与浏览器返回都同步页面状态", () => {
    const { result } = renderHook(() => usePageNavigation());
    act(() => result.current[1]("taskflow"));
    expect(result.current[0]).toBe("taskflow");
    act(() => {
      window.history.replaceState(null, "", "/?page=live");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current[0]).toBe("live");
    act(() => result.current[1]("home"));
    expect(result.current[0]).toBe("home");
  });
});
