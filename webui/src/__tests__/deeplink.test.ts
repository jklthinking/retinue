import { describe, expect, it } from "vitest";
import { parseTaskHash, taskDeepLink } from "../deeplink";

describe("parseTaskHash", () => {
  it("parses a task deep link", () => {
    expect(parseTaskHash("#/task/task-20260812-009")).toBe("task-20260812-009");
  });

  it("rejects empty and foreign hashes", () => {
    expect(parseTaskHash("")).toBeNull();
    expect(parseTaskHash("#/board")).toBeNull();
    expect(parseTaskHash("#/task/not-a-task")).toBeNull();
    expect(parseTaskHash("#/task/task-20260812-009/extra")).toBeNull();
  });
});

describe("taskDeepLink", () => {
  it("builds a shareable hash URL on the current mount path", () => {
    const link = taskDeepLink("task-20260812-009");
    expect(link).toContain("#/task/task-20260812-009");
    expect(link.startsWith("http")).toBe(true);
  });
});
