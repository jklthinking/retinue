import { useI18n, t } from "../i18n";
import { useId } from "react";
import type { Status } from "../types";
import { STATUS_LABEL } from "../types";
import { STATUS_COLOR } from "../lib/statusColors";

interface Props {
  counts: Partial<Record<Status, number>>;
  recentEvents: { from_status: Status | null; to_status: Status | null }[];
  onSelectStatus?: (status: Status) => void;
  selectedStatus?: Status | null;
}

const NODES: { status: Status; x: number; y: number }[] = [
  { status: "queued", x: 100, y: 143 },
  { status: "doing", x: 360, y: 143 },
  { status: "handoff", x: 360, y: 48 },
  { status: "blocked", x: 360, y: 238 },
  { status: "done", x: 660, y: 143 },
];

const EDGES: { from: Status; to: Status; path: string; label?: string; labelX?: number; labelY?: number }[] = [
  { from: "queued", to: "doing", path: "M 160 143 L 297 143" },
  { from: "doing", to: "done", path: "M 420 143 L 597 143" },
  { from: "doing", to: "handoff", path: "M 326 113 C 310 103, 310 88, 326 78", label: "交棒", labelX: 270, labelY: 99 },
  { from: "handoff", to: "doing", path: "M 394 78 C 410 88, 410 103, 394 113", label: "接棒", labelX: 450, labelY: 99 },
  { from: "doing", to: "blocked", path: "M 326 173 C 310 183, 310 198, 326 208", label: "受阻", labelX: 270, labelY: 199 },
  { from: "blocked", to: "doing", path: "M 394 208 C 410 198, 410 183, 394 173", label: "恢复", labelX: 450, labelY: 199 },
];

export default function TaskFlowDiagram({ counts, recentEvents, onSelectStatus, selectedStatus }: Props) {
  useI18n();
  const markerPrefix = useId().replace(/:/g, "");

  return (
    <div className="tf-wrap">
      <svg className="tf-svg" viewBox="0 0 760 286" role="img" aria-label={t("任务状态流转图")}>
        <defs>
          {NODES.map(({ status }) => (
            <marker
              key={status}
              id={markerPrefix + "-" + status}
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
              markerUnits="userSpaceOnUse"
            >
              <path d="M 0 0 L 8 4 L 0 8 Z" fill={STATUS_COLOR[status]} />
            </marker>
          ))}
        </defs>

        {EDGES.map(({ from, to, path, label, labelX, labelY }) => {
          const active = recentEvents.some(
            (event) => event.from_status === from && event.to_status === to
          );
          return (
            <g key={from + "-" + to}>
              <path
                data-edge={from + "-" + to}
                className={"tf-edge" + (active ? " tf-edge--active" : "")}
                d={path}
                stroke={STATUS_COLOR[to]}
                markerEnd={"url(#" + markerPrefix + "-" + to + ")"}
                fill="none"
              >
                <title>{t(STATUS_LABEL[from])} → {t(STATUS_LABEL[to])}</title>
              </path>
              {label && <text x={labelX} y={labelY} textAnchor="middle" className="tf-edge__label">{t(label)}</text>}
            </g>
          );
        })}

        {NODES.map(({ status, x, y }) => (
          <g
            key={status}
            className={"tf-node" + (selectedStatus === status ? " tf-node--selected" : "")}
            data-status={status}
            role={onSelectStatus ? "button" : undefined}
            tabIndex={onSelectStatus ? 0 : undefined}
            aria-label={onSelectStatus ? t("查看{v0}任务：{v1}", { v0: t(STATUS_LABEL[status]), v1: counts[status] ?? 0 }) : undefined}
            aria-pressed={onSelectStatus ? selectedStatus === status : undefined}
            onClick={() => onSelectStatus?.(status)}
            onKeyDown={(event) => {
              if (onSelectStatus && (event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                onSelectStatus(status);
              }
            }}
          >
            <rect x={x - 60} y={y - 29} width="120" height="58" rx="12" stroke={STATUS_COLOR[status]} />
            <rect x={x - 60} y={y - 14} width="4" height="28" rx="2" fill={STATUS_COLOR[status]} />
            <text x={x} y={y - 5} textAnchor="middle" className="tf-node__label">{t(STATUS_LABEL[status])}</text>
            <text x={x} y={y + 17} textAnchor="middle" className="tf-node__count">{counts[status] ?? 0}</text>
          </g>
        ))}
      </svg>
      <div className="tf-legend">
        <span><i className="tf-legend__line" />{t("常规路径")}</span>
        <span><i className="tf-legend__line tf-legend__line--active" />{t("近期流转")}</span>
      </div>
    </div>
  );
}
