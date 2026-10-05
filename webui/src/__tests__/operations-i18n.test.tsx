import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageSwitcher, LocaleProvider } from "../i18n";
import { formatTokens, operationsSystemText } from "../lib/operations";
import Reports from "../pages/Reports";
import Skills from "../pages/Skills";
import { AGENT_ONE, ME, mockFetch } from "./helpers";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/?lang=en");
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/?lang=zh-CN");
  // Pure label helpers share the provider's locale; reset it for other tests.
  render(<LocaleProvider>{null}</LocaleProvider>);
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("operational labels and source evidence across locales", () => {
  it("keeps missing usage unknown and actor-provided names verbatim in English", async () => {
    const actor = { ...AGENT_ONE, display_name: "任务卡", model: "自定义模型" };
    mockFetch({ "api/metrics/summary": () => ({ body: {
      start: "2026-08-31", end: "2026-08-31", days: 1,
      actors: [{ actor_id: actor.id, input: null, output: null, usage_available: false, records: 0, days: {}, runtimes: [] }],
      coverage: { reported_actors: 0, expected_actors: 1, missing_actors: [actor.id], complete: false },
    } }) });
    render(<LocaleProvider><Reports days={1} actors={[actor]} /></LocaleProvider>);
    await screen.findByText("任务卡");
    const row = screen.getByText("任务卡").closest(".usage-row");
    if (!(row instanceof HTMLElement)) throw new Error("Missing usage row");
    expect(within(row).getByText("Unreported")).toBeInTheDocument();
    expect(row).toHaveTextContent("自定义模型");
    expect(screen.getByText(/Unknown usage cannot be recorded as zero/)).toBeInTheDocument();
    expect(screen.getByText(/provider bills/)).toBeInTheDocument();
    expect(formatTokens(null)).toBe("Unknown");
    expect(formatTokens(0)).toBe("0");
    expect(operationsSystemText("为 12 张任务卡补充可验证的 acceptance。")).toBe("Add verifiable acceptance criteria to 12 task cards.");
    expect(operationsSystemText("未经登记的说明内容")).toBe("未经登记的说明内容");
  });

  it("preserves an uncategorized skill filter when switching languages", async () => {
    const user = userEvent.setup();
    mockFetch({
      "api/skills": () => ({ body: [
        { id: 1, name: "原始技能名称", category: "", description: "请保留技能描述", owners: [], enabled: true },
        { id: 2, name: "Other capability", category: "Engineering", description: "Example capability", owners: [], enabled: true },
      ] }),
      "api/actors": () => ({ body: [] }),
    });
    render(<LocaleProvider><LanguageSwitcher /><Skills me={{ ...ME, readonly: true }} /></LocaleProvider>);
    await screen.findByText("原始技能名称");
    await user.click(screen.getByRole("button", { name: "Uncategorized 1" }));
    expect(screen.queryByText("Other capability")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
    expect(screen.getByRole("button", { name: "未分类 1" })).toHaveClass("is-active");
    expect(screen.getByText("原始技能名称")).toBeInTheDocument();
    expect(screen.getByText("请保留技能描述")).toBeInTheDocument();
    expect(screen.queryByText("Other capability")).not.toBeInTheDocument();
  });
});
