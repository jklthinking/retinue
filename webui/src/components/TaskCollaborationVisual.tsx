import { useId, useMemo } from "react";
import type { ActorInfo, Status } from "../types";
import { STATUS_LABEL } from "../types";
import { collaborationTime, runIdentity, runStatus } from "../lib/collaboration";
import {
  EDGE_LABEL, GRAPH_NODE_HEIGHT, GRAPH_NODE_WIDTH, historicalWorkerLabel, layoutTasks, millis,
  runBounds, visibleModules, visualEdges,
  type CollaborationTask, type HistoryEvent, type VisualSnapshot,
} from "../lib/collaborationVisual";
import "./task-collaboration-visual.css";

interface VisualProps {
  snapshot: VisualSnapshot;
  selectedTaskId?: string;
  selectedRunId?: string;
  actors: ActorInfo[];
  disabled?: boolean;
  onSelectTask: (taskId: string) => void;
  onSelectRun: (runId: string) => void;
  onOpenTask?: (taskId: string) => void;
}

function eventStatus(event: HistoryEvent, nameOf: (id: string) => string): string {
  const pieces = [];
  if (event.from_status || event.to_status) pieces.push(`${event.from_status ? STATUS_LABEL[event.from_status] : "未知"} → ${event.to_status ? STATUS_LABEL[event.to_status] : "未知"}`);
  if (event.from_holder && event.to_holder && event.from_holder !== event.to_holder) pieces.push(`${nameOf(event.from_holder)} → ${nameOf(event.to_holder)}`);
  return pieces.join(" · ");
}

export function TaskStateHistory({ snapshot, taskId, actors }: Pick<VisualProps, "snapshot" | "actors"> & { taskId: string }) {
  const task = snapshot.tasks.find((item) => item.id === taskId);
  const nameOf = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id || "未记录";
  const history = snapshot.events.filter((event) => event.task_id === taskId).slice().sort((a, b) => a.seq - b.seq);
  const lastChange = [...history].reverse().find((event) => event.to_status);
  const stages: Status[] = ["queued", "doing", "handoff", "blocked", "done", "cancelled"];
  return <section className="collab-state" aria-label="任务状态与历史">
    <header><strong>任务状态流</strong><small>状态记录与执行上报分别展示</small></header>
    <div className="collab-state__stages" aria-label="当前任务状态">
      {stages.map((status) => <span key={status} data-status={status} data-current={task?.status === status}>
        {task?.status === status ? "● " : ""}{STATUS_LABEL[status]}
      </span>)}
    </div>
    {lastChange && <p className="collab-state__latest">最近变更：{eventStatus(lastChange, nameOf)} · {collaborationTime(lastChange.at)}</p>}
    <details className="collab-state__history" open={snapshot.runs.every((run) => run.task_id !== taskId)}>
      <summary>任务历史（{history.length}）</summary>
      {history.length ? <ol>{history.map((event) => <li key={event.id}>
        <span className="collab-state__point" aria-hidden="true" />
        <header><strong>#{event.seq} · {nameOf(event.who)}</strong><time>{collaborationTime(event.at)}</time></header>
        {eventStatus(event, nameOf) && <small>{eventStatus(event, nameOf)}</small>}
        <p>{event.did}</p>
      </li>)}</ol> : <p>尚无历史事件；当前状态来自任务卡，不推断过去的委派或执行。</p>}
    </details>
  </section>;
}

export function TaskRelationshipGraph(props: VisualProps) {
  const { snapshot, selectedTaskId, actors, onSelectTask, onOpenTask, disabled } = props;
  const markerId = useId().replace(/:/g, "");
  const nameOf = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id || "未记录";
  const edges = useMemo(() => visualEdges(snapshot, nameOf), [snapshot, actors]);
  const { nodes, width, height } = useMemo(() => layoutTasks(snapshot, edges), [snapshot, edges]);
  const nodeById = new Map(nodes.map((node) => [node.task.id, node]));
  return <section className="collab-graph" aria-label="任务协作关系图">
    <div className="collab-graph__legend" aria-label="关系类型图例">{Object.entries(EDGE_LABEL).map(([kind, label]) => <span key={kind} data-kind={kind}><i />{label}</span>)}</div>
    <p className="collab-graph__hint">点击任务节点查看指令、进展、等待对象和成果。实线委派，虚线依赖；交棒与退回来自明确事件。</p>
    <div className="collab-graph__scroll" tabIndex={0} aria-label="可横向滚动的关系图">
      <div className="collab-graph__canvas" style={{ width, height }}>
        <svg width={width} height={height} aria-label="协作关系连线" role="img">
          <title>已记录的任务委派、依赖、交棒与退回关系</title>
          <defs>{Object.keys(EDGE_LABEL).map((kind) => <marker key={kind} id={`${markerId}-${kind}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" className={`collab-graph__arrow collab-graph__arrow--${kind}`} /></marker>)}</defs>
          {edges.map((edge, index) => {
            const from = nodeById.get(edge.from), to = nodeById.get(edge.to);
            if (!from || !to) return null;
            const self = from.task.id === to.task.id;
            const forward = from.x < to.x;
            const sx = from.x + GRAPH_NODE_WIDTH, sy = from.y + GRAPH_NODE_HEIGHT / 2;
            const tx = self ? from.x + GRAPH_NODE_WIDTH - 38 : to.x + (forward ? 0 : GRAPH_NODE_WIDTH);
            const ty = self ? from.y : to.y + GRAPH_NODE_HEIGHT / 2;
            const bending = 42 + index % 3 * 9;
            const path = self ? `M ${sx - 64} ${from.y} C ${sx - 64} ${from.y - bending}, ${tx} ${from.y - bending}, ${tx} ${ty}`
              : forward ? `M ${sx} ${sy} C ${sx + 36} ${sy}, ${tx - 36} ${ty}, ${tx} ${ty}`
              : `M ${sx} ${sy} C ${sx + bending} ${sy}, ${tx + bending} ${ty}, ${tx} ${ty}`;
            const labelX = self ? sx - 70 : forward ? (sx + tx) / 2 : Math.max(sx, tx) + bending / 2;
            const labelY = self ? from.y - bending + 4 : (sy + ty) / 2 - 7;
            return <g key={edge.id} className="collab-graph__edge" data-kind={edge.kind} data-from={edge.from} data-to={edge.to}>
              <title>{edge.label}：{edge.note || "有明确关系记录"}{edge.at ? ` · ${collaborationTime(edge.at)}` : ""}</title>
              <path d={path} markerEnd={`url(#${markerId}-${edge.kind})`} />
              <text x={labelX} y={labelY} textAnchor="middle">{edge.label}</text>
            </g>;
          })}
        </svg>
        {nodes.map(({ task, external, x, y }) => {
          const runs = snapshot.runs.filter((run) => run.task_id === task.id);
          const latest = runs[runs.length - 1];
          return <button type="button" key={task.id} disabled={disabled || (external && !onOpenTask)}
            className="collab-graph__node" data-status={task.status} data-external={external}
            aria-pressed={!external && selectedTaskId === task.id}
            aria-label={`${external ? "依赖任务" : "协作任务"}：${task.title} · ${STATUS_LABEL[task.status]}`}
            onClick={() => external ? onOpenTask?.(task.id) : onSelectTask(task.id)}
            style={{ left: x, top: y, width: GRAPH_NODE_WIDTH, minHeight: GRAPH_NODE_HEIGHT }}>
            <small>{external ? "外部依赖" : task.id === snapshot.root_task_id ? "主任务" : "任务分支"} · 状态：{STATUS_LABEL[task.status]}</small>
            <strong>{task.title}</strong>
            <span>当前持棒：{nameOf(task.holder)}</span>
            <span>{latest ? `最近执行：${runIdentity(latest)} · ${runStatus(latest)}` : "最近执行：尚无执行上报"}</span>
            {latest?.waiting && <em>等 {nameOf(latest.waiting.owner)} · {latest.waiting.reason}</em>}
          </button>;
        })}
      </div>
    </div>
    {edges.length === 0 && <p className="collab-graph__hint">当前没有已确认的任务间关系；已保留任务节点和状态历史。</p>}
    {edges.some((edge) => edge.kind === "handoff" || edge.kind === "review_return") && <details className="collab-graph__receipts"><summary>交棒与退回记录</summary><ul>{edges.filter((edge) => edge.kind === "handoff" || edge.kind === "review_return").map((edge) => <li key={edge.id}><strong>{edge.label}：</strong>{edge.note} <time>{collaborationTime(edge.at)}</time></li>)}</ul></details>}
    {selectedTaskId && <TaskStateHistory snapshot={snapshot} taskId={selectedTaskId} actors={actors} />}
  </section>;
}

export function WorkerTimeline({ snapshot, actors, selectedTaskId, selectedRunId, disabled, onSelectRun, onSelectTask }: VisualProps) {
  const runs = snapshot.runs.map((run) => ({ run, bounds: runBounds(run, snapshot.generated_at) }));
  const events = snapshot.events.filter((event) => !snapshot.runs.some((run) => run.task_id === event.task_id) && millis(event.at) !== null);
  const unlocated = runs.filter((item) => !item.bounds);
  const times = [...runs.flatMap(({ bounds }) => bounds ? [bounds.start, bounds.end] : []), ...events.map((event) => millis(event.at)).filter((time): time is number => time !== null)];
  const min = Math.min(...times), max = Math.max(...times);
  const range = Math.max(60_000, max - min);
  const fraction = (time: number) => Math.min(100, Math.max(0, (time - min) / range * 100));
  const identities = new Map<string, typeof runs>();
  for (const item of runs.filter((item) => item.bounds)) {
    const key = [item.run.actor_id, item.run.node || "", item.run.model || "", item.run.runtime || ""].join("\u0000");
    identities.set(key, [...(identities.get(key) ?? []), item]);
  }
  const historicalActors = [...new Set(events.map((event) => event.who))];
  const nameOf = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id || "未知";
  return <section className="collab-timeline" aria-label="设备模型时间泳道">
    <p className="collab-graph__hint">横轴为记录时间。执行条显示记录的起止，仍活动的上报延伸至本次同步；准备记录是单点，历史事件不推算工作时长。</p>
    {times.length > 0 && <div className="collab-timeline__axis"><time>{collaborationTime(new Date(min).toISOString())}</time><time>{collaborationTime(new Date(Math.max(max, min + range)).toISOString())}</time></div>}
    <div className="collab-timeline__scroll">
      {[...identities].map(([identity, items]) => <div className="collab-timeline__lane" key={identity}>
        <header><strong>{runIdentity(items[0].run)}</strong><small>{nameOf(items[0].run.actor_id)} · {items[0].run.runtime || "运行端未知"}</small></header>
        <div className="collab-timeline__track" style={{ height: Math.max(58, items.length * 42 + 10) }}>{items.map(({ run, bounds }, index) => <button
          type="button" key={run.id} className="collab-timeline__bar" data-status={run.status} data-prepared={bounds?.prepared ?? true}
          aria-pressed={selectedRunId === run.id} disabled={disabled}
          aria-label={`${runIdentity(run)} · ${run.title} · ${runStatus(run)}`}
          title={`${run.title} · ${runStatus(run)} · ${bounds ? `${collaborationTime(new Date(bounds.start).toISOString())} 至 ${collaborationTime(new Date(bounds.end).toISOString())}` : "时间未记录"}`}
          onClick={() => onSelectRun(run.id)} style={{ top: 8 + index * 42, left: bounds ? `${fraction(bounds.start)}%` : "0%", width: bounds && !bounds.prepared ? `${Math.max(.4, fraction(bounds.end) - fraction(bounds.start))}%` : "10px" }}>
          <span>{bounds?.prepared ? "◇ " : ""}{run.title}</span><small>{runStatus(run)}{run.waiting ? ` · 等${nameOf(run.waiting.owner)}` : ""}</small>
        </button>)}</div>
      </div>)}
      {historicalActors.map((actorId) => {
        const history = events.filter((event) => event.who === actorId);
        return <div key={`history:${actorId}`} className="collab-timeline__lane">
          <header><strong>{historicalWorkerLabel(actorId, actors)}</strong><small>仅任务事件证据</small></header>
          <div className="collab-timeline__track" style={{ height: Math.max(58, history.length * 35 + 10) }}>{history.map((event, index) => <button type="button" key={event.id}
            className="collab-timeline__event" aria-pressed={selectedTaskId === event.task_id} disabled={disabled}
            onClick={() => onSelectTask(event.task_id)} style={{ top: 8 + index * 35, left: `${fraction(millis(event.at) ?? min)}%` }}
            title={`${event.did} · ${collaborationTime(event.at)}`} aria-label={`历史事件 #${event.seq} · ${event.did}`}>
            <span aria-hidden="true">●</span> #{event.seq} {eventStatus(event, nameOf) || "任务事件"}
          </button>)}</div>
        </div>;
      })}
    </div>
    {unlocated.length > 0 && <div className="collab-timeline__unlocated"><p className="collab-graph__hint">{unlocated.length} 条执行缺少可靠时间，未绘制时长：</p>{unlocated.map(({ run }) => <button type="button" key={run.id} onClick={() => onSelectRun(run.id)} disabled={disabled}>{runIdentity(run)} · {run.title} · 时间未记录</button>)}</div>}
    {times.length === 0 && <p className="collab-graph__hint">尚无可定位的执行或历史事件时间；请在关系图查看当前任务。</p>}
    <p className="collab-graph__hint">心跳不表示工作推进；设备和模型保留当次记录，历史缺失字段不会用当前名册补写。</p>
  </section>;
}

export function ModuleContributions({ snapshot, actors, selectedTaskId, onSelectTask, onSelectRun, disabled }: VisualProps) {
  const nameOf = (id: string) => actors.find((actor) => actor.id === id)?.display_name || id || "责任人未记录";
  const modules = visibleModules(snapshot);
  return <section className="collab-modules" aria-label="任务功能模块贡献">
    <p className="collab-graph__hint">模块来自明确登记。每个模块列出任务、实际执行者、已报告工作、等待和成果；执行者声明需要另行验收。</p>
    {modules.map((module) => {
      const tasks = snapshot.tasks.filter((task) => module.task_ids.includes(task.id));
      return <article className="collab-modules__module" key={module.id}>
        <header><h4>{module.source === "unassigned" ? "模块未标注" : module.name}</h4><span>{tasks.length} 个任务 · {module.run_ids.length} 次执行</span></header>
        {module.source === "unassigned" && <p className="collab-graph__hint">这些任务还没有功能模块记录，可在委派时登记模块；不会从任务标题或聊天猜测归属。</p>}
        {tasks.map((task: CollaborationTask) => {
          const runs = snapshot.runs.filter((run) => run.task_id === task.id && module.run_ids.includes(run.id));
          const last = runs[runs.length - 1];
          const completed = module.completed.filter((item) => item.task_id === task.id);
          const claims = completed;
          const events = snapshot.events.filter((event) => event.task_id === task.id).slice().sort((a, b) => a.seq - b.seq);
          return <div className="collab-modules__task" key={task.id} data-selected={selectedTaskId === task.id}>
            <button type="button" disabled={disabled} aria-pressed={selectedTaskId === task.id} onClick={() => last ? onSelectRun(last.id) : onSelectTask(task.id)}><strong>{task.title}</strong><small>状态：{STATUS_LABEL[task.status]}</small></button>
            <dl><div><dt>当前持棒</dt><dd>{nameOf(task.holder)}</dd></div>
              <div><dt>最近执行</dt><dd>{last ? `${runIdentity(last)} · ${nameOf(last.actor_id)} · ${runStatus(last)}` : "尚无执行上报，执行设备/模型未记录"}</dd></div>
              <div><dt>{claims.length ? "做了什么（执行者声明）" : "已完成工作上报"}</dt><dd>{claims.length ? `已声明完成 ${claims.length} 项，尚未验收。` : "尚无已完成工作的上报"}</dd></div>
              <div><dt>任务要求 / 最近记录（非完成证明）</dt><dd>{last?.latest_note || events[events.length - 1]?.did || "尚无任务要求或执行回执记录"}<small>{last?.latest_note ? "执行回执，不代表工作已完成或已验收" : "任务记录，不代表工作已完成"}</small></dd></div>
              <div><dt>当前等待</dt><dd>{last?.waiting ? `${nameOf(last.waiting.owner)} · ${last.waiting.reason}` : task.blocked_reason || "尚未记录等待对象"}</dd></div>
              <div><dt>下一步</dt><dd>{last?.progress_report?.next_action || task.next || "尚未记录下一步"}</dd></div>
            </dl>
            {claims.length > 0 && <ul className="collab-modules__claims">{claims.map((claim, index) => <li key={index}>{claim.summary}<small>{nameOf(claim.actor_id)} · 待验收{claim.revision ? ` · ${claim.revision}` : ""}{claim.refs.length ? ` · ${claim.refs.join(" · ")}` : " · 成果引用未补充"}</small></li>)}</ul>}
            <p className="collab-modules__refs">成果引用：{[...new Set([...(task.refs ?? []), ...runs.flatMap((run) => run.refs), ...claims.flatMap((claim) => claim.refs)])].join(" · ") || "尚未上报"}</p>
          </div>;
        })}
      </article>;
    })}
  </section>;
}
