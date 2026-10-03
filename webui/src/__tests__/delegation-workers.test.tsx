import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DelegateTaskForm from "../components/DelegateTaskForm";
import DelegationPolicyForm from "../components/DelegationPolicyForm";
import type { ActorInfo } from "../types";
import type { CollaborationSnapshot } from "../lib/collaboration";
import { AGENT_ONE, mockFetch } from "./helpers";

const TASK = "task-20260812-001";
const unknownWorker: ActorInfo = { ...AGENT_ONE, runtime: "multi", model: "unknown", node: "device-a", display_name: "多运行端成员" };
const aliasWorker: ActorInfo = { ...AGENT_ONE, id: "alias-worker", model: "sonnet", node: "device-b", display_name: "会话研究员" };
const knownWorker: ActorInfo = { ...AGENT_ONE, id: "known-worker", runtime: "multi", model: "model-c", node: "device-c", display_name: "已登记成员" };
const syncById: ActorInfo = { ...AGENT_ONE, id: "device-session-sync", display_name: "设备会话索引" };
const syncByModel: ActorInfo = { ...AGENT_ONE, id: "index-agent", model: " SESSION-INDEX-V2 ", display_name: "模型会话索引" };
const actors = [unknownWorker, aliasWorker, knownWorker, syncById, syncByModel];

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("委派使用设备与模型成员", () => {
  it("排除明确同步代理，保留 multi 与未知型号成员并按原 Actor ID 委派", async () => {
    const { calls } = mockFetch({ ["api/tasks/" + TASK + "/delegations"]: () => ({ body: { created: true } }) });
    const user = userEvent.setup();
    render(<DelegateTaskForm taskId={TASK} leaseTerm={1} actors={actors} targets={actors.map((actor) => actor.id)} onCreated={vi.fn()} />);
    await user.click(screen.getByText("委派子任务"));
    expect(screen.queryByRole("option", { name: /设备会话索引|模型会话索引/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "device-a · 型号待确认 · 多运行端成员" })).toHaveValue(unknownWorker.id);
    expect(screen.getByRole("option", { name: /device-b · sonnet · 会话研究员.*配置别名，精确型号待确认/ })).toHaveValue(aliasWorker.id);
    expect(screen.getByRole("option", { name: "device-c · model-c · 已登记成员" })).toHaveValue(knownWorker.id);
    await user.selectOptions(screen.getByLabelText("执行者"), unknownWorker.id);
    await user.type(screen.getByLabelText("子任务标题"), "核查资料");
    await user.type(screen.getByLabelText("委派要求"), "核查现有结论");
    await user.type(screen.getByLabelText("验收标准（每行一条）"), "提供可核查来源");
    await user.click(screen.getByRole("button", { name: "建立委派子卡" }));
    expect(await screen.findByText("已建立委派子卡，等待执行者开工。")).toBeInTheDocument();
    expect(JSON.parse(String(calls[0].init?.body)).delegated_to).toBe(unknownWorker.id);
  });

  it("权限名单不保留隐藏的同步代理，真实成员可显式授权且保存前不写入", async () => {
    const { calls } = mockFetch({ ["api/tasks/" + TASK + "/collaboration/policy"]: () => ({ body: { ok: true } }) });
    const data = {
      task_id: TASK, root_task_id: TASK, can_manage_delegation_policy: true,
      delegation_policy: { allowed_actor_ids: [syncById.id, syncByModel.id, unknownWorker.id], max_depth: 2, max_children: 4 },
    } as CollaborationSnapshot;
    const user = userEvent.setup();
    render(<DelegationPolicyForm snapshot={data} actors={actors} onChanged={vi.fn()} />);
    await user.click(screen.getByText(/Agent 委派权限/));
    expect(screen.queryByRole("checkbox", { name: /设备会话索引|模型会话索引/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "device-a · 型号待确认 · 多运行端成员" })).toBeChecked();
    expect(screen.getByText(/原名单含会话同步代理；保存后将移除/)).toBeInTheDocument();
    const alias = screen.getByRole("checkbox", { name: /device-b · sonnet · 会话研究员.*配置别名，精确型号待确认/ });
    expect(alias).not.toBeChecked();
    await user.click(alias);
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "保存此任务的委派权限" }));
    expect(await screen.findByText(/已保存此任务的委派范围/)).toBeInTheDocument();
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      allowed_actor_ids: [unknownWorker.id, aliasWorker.id].sort(), max_depth: 2, max_children: 4,
    });
  });
});
