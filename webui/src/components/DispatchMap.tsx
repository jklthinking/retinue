import { useMemo } from "react";
import type { ActorInfo, Status, TaskSummary } from "../types";
import { STATUS_LABEL } from "../types";
import { hueOf } from "../avatar";
import { useVocab } from "../theme";

const EDGE_COLOR: Record<string, string> = {
  queued: "#a49d8d",
  doing: "#2563eb",
  handoff: "#c9962b",
  blocked: "#d64545",
};

const ACTIVE: Status[] = ["queued", "doing", "handoff", "blocked"];

const WIDTH = 760;
const ROW = 56;
const TOP = 46;
const LEFT_CX = 150;
const RIGHT_CX = 560;
const R = 15;

interface Props {
  tasks: TaskSummary[];
  actors: ActorInfo[];
}

function clip(name: string, max: number): string {
  return name.length > max ? name.slice(0, max - 1) + "…" : name;
}

export default function DispatchMap({ tasks, actors }: Props) {
  const vocab = useVocab();
  const model = useMemo(() => {
    const active = tasks.filter((t) => ACTIVE.includes(t.status));
    const nameOf = (id: string) => actors.find((a) => a.id === id)?.display_name || id;

    const dispatcherIds = [...new Set(active.map((t) => t.created_by))].slice(0, 8);
    const agentIds = [
      ...new Set([
        ...actors.filter((a) => a.kind === "agent" && !a.disabled).map((a) => a.id),
        ...active.map((t) => t.holder),
      ]),
    ].slice(0, 12);

    const leftY = new Map(dispatcherIds.map((id, i) => [id, TOP + i * ROW]));
    const rightY = new Map(agentIds.map((id, i) => [id, TOP + i * ROW]));
    const perAgent = new Map<string, number>();
    for (const t of active) perAgent.set(t.holder, (perAgent.get(t.holder) ?? 0) + 1);

    return {
      active,
      nameOf,
      dispatcherIds,
      agentIds,
      leftY,
      rightY,
      perAgent,
      online: new Set(actors.filter((a) => a.online).map((a) => a.id)),
      height: TOP + Math.max(dispatcherIds.length, agentIds.length, 1) * ROW - 6,
    };
  }, [tasks, actors]);

  if (model.active.length === 0 && model.agentIds.length === 0) {
    return <p className="muted">暂无进行中的派单</p>;
  }

  return (
    <div className="dispatch-wrap">
      <svg
        className="dispatch-svg"
        viewBox={`0 0 ${WIDTH} ${model.height}`}
        role="img"
        aria-label="派单协调图"
      >
        <text x={LEFT_CX} y={18} textAnchor="middle" className="dm-col-label">
          派单方 DISPATCH
        </text>
        <text x={RIGHT_CX} y={18} textAnchor="middle" className="dm-col-label">
          {vocab.membersAgents}
        </text>

        {model.active.map((task) => {
          const y1 = model.leftY.get(task.created_by);
          const y2 = model.rightY.get(task.holder);
          if (y1 === undefined || y2 === undefined) return null;
          const x1 = LEFT_CX + R + 3;
          const x2 = RIGHT_CX - R - 3;
          const mid = (x1 + x2) / 2;
          const width =
            task.priority === "urgent" ? 2.8 : task.priority === "high" ? 2.2 : 1.6;
          return (
            <path
              key={task.id}
              className={`dm-edge ${task.status === "doing" ? "dm-edge-doing" : ""}`}
              d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
              stroke={EDGE_COLOR[task.status] ?? "#a49d8d"}
              strokeWidth={width}
              fill="none"
              opacity={task.status === "queued" ? 0.5 : 0.85}
            >
              <title>
                {task.id} {task.title} · {STATUS_LABEL[task.status]}
              </title>
            </path>
          );
        })}

        {model.dispatcherIds.map((id) => {
          const y = model.leftY.get(id)!;
          const name = model.nameOf(id);
          const hue = hueOf(name);
          return (
            <g key={id}>
              <text x={LEFT_CX - R - 10} y={y + 4} textAnchor="end" className="dm-name">
                {clip(name, 8)}
              </text>
              <rect
                x={LEFT_CX - R}
                y={y - R}
                width={R * 2}
                height={R * 2}
                rx={R * 0.62}
                fill={`hsl(${hue}, 46%, 48%)`}
              />
              <text x={LEFT_CX} y={y + 4.5} textAnchor="middle" className="dm-initial">
                {name.slice(0, 1)}
              </text>
            </g>
          );
        })}

        {model.agentIds.map((id) => {
          const y = model.rightY.get(id)!;
          const name = model.nameOf(id);
          const hue = hueOf(name);
          const count = model.perAgent.get(id) ?? 0;
          return (
            <g key={id} opacity={count === 0 ? 0.42 : 1}>
              <rect
                x={RIGHT_CX - R}
                y={y - R}
                width={R * 2}
                height={R * 2}
                rx={R * 0.62}
                fill={`hsl(${hue}, 46%, 48%)`}
              />
              {model.online.has(id) && (
                <circle cx={RIGHT_CX + R - 2} cy={y - R + 2} r={3.6} className="dm-online" />
              )}
              <text x={RIGHT_CX} y={y + 4.5} textAnchor="middle" className="dm-initial">
                {name.slice(0, 1)}
              </text>
              <text x={RIGHT_CX + R + 10} y={y + 4} className="dm-name">
                {clip(name, 9)}
              </text>
              {count > 0 && (
                <g>
                  <circle cx={RIGHT_CX + R + 128} cy={y} r={9} className="dm-count" />
                  <text
                    x={RIGHT_CX + R + 128}
                    y={y + 3.5}
                    textAnchor="middle"
                    className="dm-count-text"
                  >
                    {count}
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
      <div className="dm-legend">
        {ACTIVE.map((s) => (
          <span key={s}>
            <i style={{ background: EDGE_COLOR[s] }} />
            {STATUS_LABEL[s]}
          </span>
        ))}
      </div>
    </div>
  );
}
