import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageSwitcher, LocaleProvider, getLanguage, readLanguage, t, useI18n } from "../i18n";
import { ThemeProvider, ThemeSwitcher, useCanonicalVocab, useVocab } from "../theme";
import { readFlowTask, readPage } from "../lib/navigation";
import { fmtUptime } from "../types";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
  window.localStorage.clear();
});

function Probe() {
  const { t: label } = useI18n();
  const vocab = useVocab();
  const canonical = useCanonicalVocab();
  // A task title can happen to match an interface key. It remains data.
  const task = { title: "任务看板", body: "请保留任务正文。" };
  return <>
    <LanguageSwitcher />
    <ThemeSwitcher />
    <span data-testid="label">{label("任务看板")}</span>
    <span data-testid="pure-helper">{t("刷新数据")}</span>
    <span data-testid="count">{label("已超过 {count} 分钟没有新进展；心跳不代表工作推进。", { count: 17 })}</span>
    <span data-testid="unknown">{label("unlisted label {count}", { count: 3 })}</span>
    <span data-testid="theme">{vocab.appTitle}</span>
    <span data-testid="canonical-dept">{canonical.centralHub}</span>
    <span data-testid="task-title">{task.title}</span>
    <span data-testid="task-body">{task.body}</span>
    <span data-testid="uptime">{[86_400, 3_600, 60, 59].map(fmtUptime).join(" · ")}</span>
  </>;
}

function renderProbe() {
  return render(<LocaleProvider><ThemeProvider><Probe /></ThemeProvider></LocaleProvider>);
}

describe("interface language and canonical task data", () => {
  it("takes an explicit English link before a saved Chinese preference", () => {
    window.localStorage.setItem("retinue.language", "zh-CN");
    window.history.replaceState(null, "", "/retinue/?lang=en&page=taskflow&task=task-20260831-009");
    renderProbe();
    expect(screen.getByTestId("label")).toHaveTextContent("Task board");
    expect(screen.getByTestId("pure-helper")).toHaveTextContent("Refresh data");
    expect(document.documentElement.lang).toBe("en");
    expect(document.title).toBe("Retinue · Task workspace");
    expect(getLanguage()).toBe("en");
    expect(window.localStorage.getItem("retinue.language")).toBe("en");
  });

  it("switches without losing selected task, filters, page, hash or task text", async () => {
    const user = userEvent.setup();
    window.history.replaceState({ selected: true }, "", "/retinue/?page=taskflow&task=task-20260831-009&q=reader&status=doing#/task/task-20260831-010");
    renderProbe();
    await user.selectOptions(screen.getByLabelText("Language / 语言"), "en");
    expect(readPage()).toBe("taskflow");
    expect(readFlowTask()).toBe("task-20260831-009");
    expect(new URLSearchParams(window.location.search).get("q")).toBe("reader");
    expect(new URLSearchParams(window.location.search).get("status")).toBe("doing");
    expect(window.location.hash).toBe("#/task/task-20260831-010");
    expect(window.history.state).toEqual({ selected: true });
    expect(screen.getByTestId("label")).toHaveTextContent("Task board");
    expect(screen.getByTestId("task-title")).toHaveTextContent("任务看板");
    expect(screen.getByTestId("task-body")).toHaveTextContent("请保留任务正文。");
    expect(screen.getByTestId("canonical-dept")).toHaveTextContent("任务中枢");

    await user.selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
    expect(screen.getByTestId("label")).toHaveTextContent("任务看板");
    expect(window.localStorage.getItem("retinue.language")).toBe("zh-CN");
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(readFlowTask()).toBe("task-20260831-009");
  });

  it("retains English after a remount and localizes both themes without changing their IDs", async () => {
    const user = userEvent.setup();
    const view = renderProbe();
    await user.selectOptions(screen.getByLabelText("Language / 语言"), "en");
    expect(screen.getByTestId("theme")).toHaveTextContent("Task desk");
    await user.selectOptions(screen.getByLabelText("Interface theme"), "court");
    expect(screen.getByTestId("theme")).toHaveTextContent("Retinue task desk");
    expect(screen.getByTestId("canonical-dept")).toHaveTextContent("王国中枢");
    expect(window.localStorage.getItem("retinue.theme")).toBe("court");
    view.unmount();
    window.history.replaceState(null, "", "/retinue/?page=home");
    renderProbe();
    expect(screen.getByTestId("label")).toHaveTextContent("Task board");
    expect(screen.getByTestId("theme")).toHaveTextContent("Retinue task desk");
  });

  it("substitutes named values in both locales and preserves unknown keys", async () => {
    const user = userEvent.setup();
    renderProbe();
    expect(screen.getByTestId("count")).toHaveTextContent("已超过 17 分钟");
    await user.selectOptions(screen.getByLabelText("Language / 语言"), "en");
    expect(screen.getByTestId("count")).toHaveTextContent("more than 17 minutes");
    expect(screen.getByTestId("unknown")).toHaveTextContent("unlisted label 3");
    expect(t("unlisted {missing}")).toBe("unlisted {missing}");
    expect(t("unlisted {value}", { value: null })).toBe("unlisted ");
    expect(t("toString")).toBe("toString");
    expect(t("__proto__")).toBe("__proto__");
    expect(t("协作空间")).toBe("Workroom");
    expect(t("完成")).toBe("Done");
  });

  it("ignores unsupported query and stored languages", () => {
    window.history.replaceState(null, "", "/?lang=unknown");
    window.localStorage.setItem("retinue.language", "unsupported");
    expect(readLanguage()).toBe("zh-CN");
    window.localStorage.setItem("retinue.language", "en");
    expect(readLanguage()).toBe("en");
    window.history.replaceState(null, "", "/?lang=zh-CN");
    expect(readLanguage()).toBe("zh-CN");
  });

  it("follows the language on browser history navigation", () => {
    renderProbe();
    act(() => {
      window.history.replaceState(null, "", "/retinue/?page=taskflow&task=task-20260831-010&lang=en");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("label")).toHaveTextContent("Task board");
    expect(readFlowTask()).toBe("task-20260831-010");
    act(() => {
      window.history.replaceState(null, "", "/retinue/?page=board&lang=zh-CN");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("label")).toHaveTextContent("任务看板");
    expect(readPage()).toBe("board");
  });

  it("switches uptime units without changing duration counts or rounding", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/?lang=en");
    renderProbe();
    expect(screen.getByTestId("uptime")).toHaveTextContent("1 d · 1 h · 1 min · 0 min");
    await user.selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
    expect(screen.getByTestId("uptime")).toHaveTextContent("1 天 · 1 小时 · 1 分钟 · 0 分钟");
  });

  it("keeps explicit links and switching usable when browser preference storage is blocked", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    window.history.replaceState(null, "", "/retinue/?lang=en&task=task-20260831-009");
    renderProbe();
    expect(screen.getByTestId("label")).toHaveTextContent("Task board");
    await user.selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
    expect(screen.getByTestId("label")).toHaveTextContent("任务看板");
    expect(readFlowTask()).toBe("task-20260831-009");
  });
});
