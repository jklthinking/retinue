import { describe, expect, it } from "vitest";
import { mergeTasks, summaryPath } from "../lib/summary";
import { makeTask } from "./helpers";

describe("summaryPath", () => {
  it("编码水位线并组装增量参数", () => {
    const path = summaryPath({
      since: "2026-08-12T10:00:00+00:00",
      today: "2026-08-12",
      includeArchived: true,
    });
    expect(path.startsWith("/api/summary?")).toBe(true);
    expect(path).toContain("updated_since=2026-08-12T10%3A00%3A00%2B00%3A00");
    expect(path).toContain("today=2026-08-12");
    expect(path).toContain("include_archived=true");
    expect(path).not.toContain("include_tasks");
  });

  it("默认全量快照不带任何参数", () => {
    expect(summaryPath()).toBe("/api/summary");
    expect(summaryPath({ includeTasks: false })).toBe(
      "/api/summary?include_tasks=false"
    );
  });
});

describe("mergeTasks", () => {
  it("按 id 覆盖已有行，新行按创建时间倒序并入", () => {
    const older = makeTask({
      id: "task-20260812-001",
      title: "旧卡",
      created_at: "2026-08-12T08:00:00",
    });
    const newer = makeTask({
      id: "task-20260812-002",
      title: "新卡",
      created_at: "2026-08-12T09:00:00",
    });
    const changedOlder = { ...older, status: "doing" as const, progress: 40 };
    const arrival = makeTask({
      id: "task-20260812-003",
      title: "刚到",
      created_at: "2026-08-12T10:00:00",
    });

    const merged = mergeTasks([newer, older], [changedOlder, arrival]);

    expect(merged.map((task) => task.id)).toEqual([
      "task-20260812-003",
      "task-20260812-002",
      "task-20260812-001",
    ]);
    expect(merged[2].status).toBe("doing");
    expect(merged[2].progress).toBe(40);
  });

  it("空增量原样返回缓存", () => {
    const cached = [makeTask({})];
    expect(mergeTasks(cached, [])).toBe(cached);
  });
});
