import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DispatchMap from "../components/DispatchMap";
import TaskFlowDiagram from "../components/TaskFlowDiagram";
import { ThemeProvider } from "../theme";
import { STATUS_LABEL } from "../types";
import { AGENT_ONE, makeTask } from "./helpers";

afterEach(cleanup);

describe("TaskFlowDiagram", () => {
  it("空数据仍显示五个状态及零计数", () => {
    const { container } = render(<TaskFlowDiagram counts={{}} recentEvents={[]} />);

    expect(screen.getByRole("img", { name: "任务状态流转图" })).toBeInTheDocument();
    expect(Array.from(container.querySelectorAll(".tf-node__label"), (node) => node.textContent)).toEqual(
      ["queued", "doing", "handoff", "blocked", "done"].map((status) => STATUS_LABEL[status as keyof typeof STATUS_LABEL])
    );
    expect(container.querySelectorAll(".tf-node__count")).toHaveLength(5);
    expect(Array.from(container.querySelectorAll(".tf-node__count"), (node) => node.textContent)).toEqual(["0", "0", "0", "0", "0"]);
  });

  it("展示每个节点的实时计数", () => {
    const { container } = render(
      <TaskFlowDiagram counts={{ queued: 2, doing: 4, handoff: 1, blocked: 3, done: 8 }} recentEvents={[]} />
    );

    expect(Array.from(container.querySelectorAll(".tf-node__count"), (node) => node.textContent)).toEqual(["2", "4", "1", "3", "8"]);
  });

  it("只高亮近期发生的有向边", () => {
    const { container } = render(
      <TaskFlowDiagram
        counts={{}}
        recentEvents={[{ from_status: "doing", to_status: "handoff" }]}
      />
    );

    expect(container.querySelector('[data-edge="doing-handoff"]')).toHaveClass("tf-edge--active");
    expect(container.querySelector('[data-edge="handoff-doing"]')).not.toHaveClass("tf-edge--active");
  });

  it("supports selecting status nodes with the keyboard", async () => {
    const onSelectStatus = vi.fn();
    render(<TaskFlowDiagram counts={{ doing: 2 }} recentEvents={[]} onSelectStatus={onSelectStatus} />);
    screen.getByRole("button", { name: `查看${STATUS_LABEL.doing}任务：2` }).focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(onSelectStatus).toHaveBeenCalledWith("doing");
  });
});

describe("DispatchMap", () => {
  it("无人无任务时保留图骨架和图例", () => {
    render(
      <ThemeProvider>
        <DispatchMap tasks={[]} actors={[]} />
      </ThemeProvider>
    );

    const diagram = screen.getByRole("img", { name: "派单协调图" });
    expect(diagram).toHaveTextContent("派单方 DISPATCH");
    expect(diagram).toHaveTextContent("暂无进行中的派单");
    expect(screen.getByText(STATUS_LABEL.queued)).toBeInTheDocument();
  });

  it("keeps all actual task holders even when the roster exceeds the idle preview", async () => {
    const actors = Array.from({ length: 20 }, (_, index) => ({ ...AGENT_ONE, id: `worker-${index}`, display_name: `Worker ${index}` }));
    const onOpenTask = vi.fn();
    render(<ThemeProvider><DispatchMap tasks={[makeTask({ title: "末位 worker 的任务", holder: "worker-19" }), makeTask({ id: "archived-task", title: "归档任务", archived: true })]} actors={actors} onOpenTask={onOpenTask} /></ThemeProvider>);
    expect(screen.getByRole("button", { name: "查看派单：末位 worker 的任务" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看派单：归档任务" })).not.toBeInTheDocument();
    const target = screen.getByRole("button", { name: "查看派单：末位 worker 的任务" });
    target.focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(onOpenTask).toHaveBeenCalledWith("task-20260812-001");
  });
});
