import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import TaskConversations, { type Conversation } from "../components/TaskConversations";
import { LanguageSwitcher, LocaleProvider } from "../i18n";
import { requestDataRefresh } from "../lib/refresh";
import { mockFetch } from "./helpers";
const writer = { id: "writer", name: "Codex", model: null, model_source: "unknown" };
const reviewer = { id: "reviewer", name: "Claude", model: "recorded-model", model_source: "registry" };
const raw = "# Request\n<script>alert(1)</script>\n[link](https://example.invalid/)";
function item(): Conversation { return { id: 1, state: "available", capture_mode: "imported", runtime: "demo-runtime", linked_at: "2026-01-01T00:00:00Z", receiver: reviewer, summary: "Source summary", messages: [raw, "Review", "Clarification", "Reply"].map((text, index) => ({ text, role: index % 2 ? "assistant" : "user", at: null, sender: index % 2 ? reviewer : writer, receiver: index % 2 ? writer : reviewer, identity_source: index % 2 ? "session_metadata" : "operator_annotation" })) }; }
beforeEach(() => { window.localStorage.clear(); window.history.replaceState(null, "", "/?lang=en"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });
it("renders two rounds with explicit attribution and escaped literal Markdown; language changes preserve bodies", async () => {
  mockFetch({ "api/tasks/sample/conversations": () => ({ body: { items: [item()] } }) });
  const user = userEvent.setup();
  const { container } = render(<LocaleProvider><LanguageSwitcher /><TaskConversations taskId="sample" /></LocaleProvider>);
  await screen.findByText("Clarification");
  expect([...container.querySelectorAll("li strong")].map(el => el.textContent)).toEqual(["Codex → Claude", "Claude → Codex", "Codex → Claude", "Claude → Codex"]);
  expect(container.querySelector("pre")?.textContent).toBe("Source summary");
  expect(container.querySelector("li pre")?.textContent).toBe(raw);
  expect(container.querySelector("script,a,img")).toBeNull();
  expect(screen.getByText("Historical import")).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Language / 语言"), "zh-CN");
  expect(screen.getByText("历史导入")).toBeInTheDocument();
  expect(container.querySelector("li pre")?.textContent).toBe(raw);
});
it("replaces previously visible text immediately after a withdrawn projection", async () => {
  let withdrawn = false;
  mockFetch({ "api/tasks/sample/conversations": () => ({ body: { items: [withdrawn ? { ...item(), state: "revoked", messages: [], summary: null } : item()] } }) });
  render(<LocaleProvider><TaskConversations taskId="sample" /></LocaleProvider>);
  await screen.findByText("Clarification");
  withdrawn = true; requestDataRefresh();
  await waitFor(() => expect(screen.queryByText("Clarification")).not.toBeInTheDocument());
  expect(screen.queryByText("Source summary")).not.toBeInTheDocument();
});
it("fails closed and discards an earlier body when an access check fails", async () => {
  let denied = false;
  mockFetch({ "api/tasks/sample/conversations": () => denied ? ({ status: 403, body: {} }) : ({ body: { items: [item()] } }) });
  render(<LocaleProvider><TaskConversations taskId="sample" /></LocaleProvider>);
  await screen.findByText("Clarification"); denied = true; requestDataRefresh();
  await screen.findByRole("alert");
  expect(screen.queryByText("Clarification")).not.toBeInTheDocument();
});
it("does not restore an old task's body after navigation", async () => {
  let release: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", (url: string) => url.includes("old") ? new Promise<Response>(resolve => { release = resolve; }) : Promise.resolve(new Response(JSON.stringify({ items: [] }))));
  const view = render(<LocaleProvider><TaskConversations taskId="old" /></LocaleProvider>);
  await waitFor(() => expect(release).toBeDefined());
  view.rerender(<LocaleProvider><TaskConversations taskId="new" /></LocaleProvider>);
  release!(new Response(JSON.stringify({ items: [item()] })));
  await waitFor(() => expect(screen.queryByText("Clarification")).not.toBeInTheDocument());
});
