import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LanguageSwitcher, LocaleProvider } from "../i18n";
import Sessions from "../pages/Sessions";
import { ThemeProvider, neutralVocab } from "../theme";
import type { RuntimeSessionInfo } from "../types";
import { AGENT_ONE, AGENT_TWO, ME, mockFetch } from "./helpers";

function untitledSession(id: number, actor: typeof AGENT_ONE): RuntimeSessionInfo {
  return {
    id, actor_id: actor.id, actor_name: actor.display_name,
    runtime: actor.runtime, node: "synthetic-device", title: "", summary: "任务中心",
    privacy: "summary", cursor: 0, message_count: 0, messages: [],
    task_id: null, task_title: null, resume_capable: false,
    started_at: null, updated_at: null, synced_at: null,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/?lang=zh-CN");
  render(<LocaleProvider>{null}</LocaleProvider>);
  cleanup();
});

it("submits the same canonical untitled-session defaults and raw acceptance in both interface languages", async () => {
  const bodies: unknown[] = [];
  for (const language of ["en", "zh-CN"] as const) {
    window.localStorage.clear();
    window.history.replaceState(null, "", `/?lang=${language}`);
    const first = untitledSession(41, AGENT_ONE), chosen = untitledSession(42, AGENT_TWO);
    const { calls } = mockFetch({
      "api/sessions?": () => ({ body: [first, chosen] }),
      "api/actors": () => ({ body: [AGENT_ONE, AGENT_TWO] }),
      "api/sessions/41/captures": () => ({ body: [] }),
      "api/sessions/42/captures": () => ({ body: [] }),
      "api/sessions/42/create-task": () => ({ status: 500, body: { detail: "synthetic write failure" } }),
      "api/sessions/41": () => ({ body: first }),
      "api/sessions/42": () => ({ body: chosen }),
    });
    const user = userEvent.setup();
    render(<LocaleProvider><ThemeProvider initialTheme="neutral"><LanguageSwitcher /><Sessions me={ME} /></ThemeProvider></LocaleProvider>);
    const extract = language === "en" ? "Extract and dispatch" : "提取并发单";
    const titleLabel = language === "en" ? "Task title" : "任务标题";

    // Both automatic selection and an explicit later selection use the same
    // canonical fallback, independent of the display language.
    await user.click(await screen.findByRole("button", { name: extract }));
    expect(screen.getByLabelText(titleLabel)).toHaveValue("Agent One：会话事项");
    await user.click(screen.getByRole("button", { name: /Agent Two.*任务中心/ }));
    await user.click(screen.getByRole("button", { name: extract }));
    expect(screen.getByLabelText(titleLabel)).toHaveValue("Agent Two：会话事项");
    const acceptanceLabel = language === "en"
      ? "Acceptance criteria (at least one; one per line)" : "验收条件（至少一条，每行一条）";
    await user.type(screen.getByLabelText(acceptanceLabel), "任务中心\n原样验收");

    // Switching locale after drafting must also leave the eventual write intact.
    const nextLanguage = language === "en" ? "zh-CN" : "en";
    await user.selectOptions(screen.getByLabelText("Language / 语言"), nextLanguage);
    expect(screen.getByLabelText(nextLanguage === "en" ? "Task title" : "任务标题"))
      .toHaveValue("Agent Two：会话事项");
    await user.click(screen.getByRole("button", {
      name: nextLanguage === "en" ? "Create and dispatch to receiving member" : "创建并派给接办成员",
    }));
    await screen.findByText("synthetic write failure");
    const writes = calls.filter(call => call.url === "api/sessions/42/create-task" && call.init?.method === "POST");
    expect(writes).toHaveLength(1);
    bodies.push(JSON.parse(String(writes[0].init?.body)));
    cleanup();
    vi.unstubAllGlobals();
  }
  expect(bodies[0]).toEqual({
    title: "Agent Two：会话事项", dept: neutralVocab.centralHub,
    holder: AGENT_TWO.id, priority: "medium", acceptance: ["任务中心", "原样验收"],
  });
  expect(bodies[1]).toEqual(bodies[0]);
});
