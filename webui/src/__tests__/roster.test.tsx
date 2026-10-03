import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Roster from "../pages/Roster";
import type { ActorInfo, AgentDiscoveryInfo } from "../types";
import { AGENT_ONE, ME, mockFetch } from "./helpers";

const operator = { ...ME, role: "admin" as const };
const known: ActorInfo = { ...AGENT_ONE, node: "device-a", model: "model-a", runtime: "runtime-a" };
const discovery: AgentDiscoveryInfo = {
  scanned_at: "2026-08-12T10:00:00Z", scope: "已登记设备", privacy: "仅读取元数据",
  runtimes: [], attention: [], actions: [],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("按设备与模型展示现有成员", () => {
  it("型号占位和别名计入待确认，同步代理独立展示且不计入执行成员", async () => {
    const uncertain = ["", "configured-at-runtime", "unknown", "待确认", "n/a", "opus", "sonnet", "haiku"].map((model, index) => ({
      ...known, id: "agent-pending-" + index, display_name: "Pending " + index, model,
    }));
    const syncAgents = [
      { ...known, id: "runtime-session-sync", display_name: "Runtime Sync", runtime: "multi", model: "" },
      { ...known, id: "sync-model", display_name: "Model Sync", model: "session-index-v2" },
      { ...known, id: "device-session-sync", display_name: "Identity Sync", model: "unknown" },
    ];
    mockFetch({
      "api/actors": () => ({ body: [known, ...uncertain, ...syncAgents] }),
      "api/agent-discovery": () => ({ body: {
        ...discovery,
        attention: syncAgents.map((actor) => ({ actor_id: actor.id, display_name: actor.display_name, runtime: actor.runtime, node: actor.node, missing: ["模型"], online: true })),
      } }),
    });
    render(<Roster me={operator} onNavigate={() => undefined} />);
    expect(await screen.findByRole("heading", { name: "device-a · model-a" })).toBeInTheDocument();
    const overview = screen.getByRole("region", { name: "发现概览" });
    expect(within(overview).getByText("执行成员").closest("article")).toHaveTextContent("9");
    expect(within(overview).getByText("型号待确认").closest("article")).toHaveTextContent("8");
    expect(within(overview).getByText("待补齐绑定").closest("article")).toHaveTextContent("0");
    expect(screen.getAllByText("配置别名 · 精确型号待确认")).toHaveLength(3);
    expect(screen.getByRole("heading", { name: "会话同步代理" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Runtime Sync" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Model Sync" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Identity Sync" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /configured-at-runtime|session-index-v2/ })).not.toBeInTheDocument();
  });

  it("multi 运行端与会话字样不排除真实成员，未知型号仍计入执行成员", async () => {
    mockFetch({
      "api/actors": () => ({ body: [
        { ...known, runtime: "multi", display_name: "会话研究员" },
        { ...known, id: "unknown-worker", node: "device-b", runtime: "multi", model: "unknown" },
      ] }),
      "api/agent-discovery": () => ({ body: discovery }),
    });
    render(<Roster me={operator} onNavigate={() => undefined} />);
    expect(await screen.findByRole("heading", { name: "device-a · model-a" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "device-b · 型号待确认" })).toBeInTheDocument();
    const overview = screen.getByRole("region", { name: "发现概览" });
    expect(within(overview).getByText("执行成员").closest("article")).toHaveTextContent("2");
    expect(within(overview).getByText("型号待确认").closest("article")).toHaveTextContent("1");
    expect(screen.queryByRole("heading", { name: "会话同步代理" })).not.toBeInTheDocument();
  });

  it("完整绑定的成员卡也可编辑，保留原身份并更新登记模型", async () => {
    const { calls } = mockFetch({
      "api/actors/agent-one/update": () => ({ body: known }),
      "api/actors": () => ({ body: [known] }),
      "api/agent-discovery": () => ({ body: discovery }),
    });
    const user = userEvent.setup();
    render(<Roster me={operator} onNavigate={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: "编辑 Agent One 的绑定" }));
    expect(screen.getByRole("heading", { name: "编辑设备与模型绑定" })).toBeInTheDocument();
    expect(screen.getByLabelText("标识")).toBeDisabled();
    expect(screen.getByLabelText("所在设备")).toHaveValue("device-a");
    const model = screen.getByLabelText("登记模型（不确定可留空）");
    await user.clear(model);
    await user.type(model, "model-b");
    await user.click(screen.getByRole("button", { name: "保存绑定" }));
    await waitFor(() => {
      const write = calls.find((call) => call.init?.method === "POST");
      expect(write?.url).toBe("api/actors/agent-one/update");
      expect(JSON.parse(String(write?.init?.body))).toMatchObject({ node: "device-a", runtime: "runtime-a", model: "model-b" });
    });
  });

  it.each([
    { ...operator, readonly: true },
    { ...ME, role: "viewer" as const, readonly: false },
  ])("观察权限没有编辑或登记入口（$role / readonly=$readonly）", async (me) => {
    mockFetch({
      "api/actors": () => ({ body: [known] }),
      "api/agent-discovery": () => ({ body: {
        ...discovery,
        attention: [{ actor_id: known.id, display_name: known.display_name, runtime: known.runtime, node: known.node, missing: ["模型"], online: true }],
      } }),
    });
    render(<Roster me={me} onNavigate={() => undefined} />);
    await screen.findByRole("heading", { name: "device-a · model-a" });
    expect(screen.queryByRole("button", { name: /编辑.*绑定/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "补齐" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "登记" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "智能派单" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "按能力搜索并派单" })).not.toBeInTheDocument();
  });
});
