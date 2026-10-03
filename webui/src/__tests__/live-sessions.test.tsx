import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import LiveSessions from "../pages/LiveSessions";
import { ME, mockFetch } from "./helpers";

const OBSERVATION = {
  id: 1,
  node_id: "node-d",
  backend: "tmux",
  endpoint_id: "tmux-aaaaaaaaaaaaaaaaaaaaaaaa",
  generation: "b".repeat(32),
  runtime: "codex",
  input_mode: "codex-prompt",
  actor_id: "agent-one",
  task_id: "task-20260902-001",
  explicit_binding: true,
  occupant_verified: true,
  control_eligible: true,
  binding_status: "bound",
  state: "idle",
  command: "codex",
  cwd_hint: "retinue-p0",
  display_location: "retinue:1.0",
  bound_live_session_id: "live-20260902-001",
  observed_at: "2026-09-02T10:00:00Z",
  disappeared_at: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LiveSessions", () => {
  it("queues a tell through the Hub control endpoint", async () => {
    const { calls } = mockFetch({
      "api/live-sessions/live-20260902-001/control": () => ({
        body: {
          id: "ctl-aaaaaaaaaaaaaaaaaaaaaaaa",
          live_session_id: "live-20260902-001",
          node_id: "node-d",
          verb: "tell",
          status: "delivered",
          attempts: 1,
          result: { output: "", detail: "" },
          expires_at: "2026-09-02T10:02:00Z",
          completed_at: "2026-09-02T10:00:01Z",
          created_at: "2026-09-02T10:00:00Z",
        },
      }),
      "api/live-sessions": () => ({ body: [OBSERVATION] }),
    });
    const user = userEvent.setup();
    render(<LiveSessions me={ME} />);

    expect(await screen.findByText("agent-one")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("输入要发送给这个 Agent 的消息…"), "同步进度");
    await user.click(screen.getByRole("button", { name: "发送一次" }));

    await waitFor(() => {
      expect(
        calls.some(
          ({ url, init }) =>
            url === "api/live-sessions/live-20260902-001/control" &&
            init?.method === "POST" &&
            JSON.parse(String(init.body)).message === "同步进度"
        )
      ).toBe(true);
    });
    expect(await screen.findByText("已送达")).toBeInTheDocument();
  });

  it("keeps every control disabled for a read-only viewer", async () => {
    mockFetch({ "api/live-sessions": () => ({ body: [OBSERVATION] }) });
    render(<LiveSessions me={{ ...ME, role: "viewer", readonly: true }} />);

    expect(await screen.findByText("当前账号是只读观察席，控制入口已禁用。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送一次" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "安全查看" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "准备软中断" })).toBeDisabled();
  });

  it("requires a second human click before queuing soft interrupt", async () => {
    const { calls } = mockFetch({
      "api/live-sessions/live-20260902-001/control": () => ({
        body: {
          id: "ctl-bbbbbbbbbbbbbbbbbbbbbbbb",
          live_session_id: "live-20260902-001",
          node_id: "node-d",
          verb: "interrupt",
          status: "queued",
          attempts: 0,
          result: {},
          expires_at: "2026-09-02T10:00:30Z",
          completed_at: null,
          created_at: "2026-09-02T10:00:00Z",
        },
      }),
      "api/live-sessions/control/": () => ({
        body: {
          id: "ctl-bbbbbbbbbbbbbbbbbbbbbbbb",
          live_session_id: "live-20260902-001",
          node_id: "node-d",
          verb: "interrupt",
          status: "delivered",
          attempts: 1,
          result: {},
          expires_at: "2026-09-02T10:00:30Z",
          completed_at: "2026-09-02T10:00:01Z",
          created_at: "2026-09-02T10:00:00Z",
        },
      }),
      "api/live-sessions": () => ({ body: [OBSERVATION] }),
    });
    const user = userEvent.setup();
    render(<LiveSessions me={ME} />);

    const first = await screen.findByRole("button", { name: "准备软中断" });
    await user.click(first);
    expect(calls.filter(({ url }) => url.includes("/control")).length).toBe(0);
    await user.click(screen.getByRole("button", { name: "确认软中断" }));
    await waitFor(() => {
      expect(
        calls.some(
          ({ url, init }) =>
            url === "api/live-sessions/live-20260902-001/control" &&
            JSON.parse(String(init?.body)).verb === "interrupt"
        )
      ).toBe(true);
    });
  });
});
