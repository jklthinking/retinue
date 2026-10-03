import { useCallback, useMemo } from "react";
import {
  Activity,
  BookOpen,
  Bot,
  HardDrive,
  ListChecks,
  MemoryStick,
  Server,
  Sparkles,
} from "lucide-react";
import { api } from "../api";
import { useVocab } from "../theme";
import type { ActorInfo, NodeInfo, SkillInfo, StatusInfo, Status } from "../types";
import { STATUS_LABEL, fmtUptime } from "../types";
import { Ambient, DataState, Metric, PageHeader, Panel } from "../components/ui";
import { isSessionSync } from "../lib/rosterIdentity";
import { sourceTime, useOperationsRead } from "../lib/operations";
import { observationState } from "../lib/observation";
import "./operations.css";

function Bar({ percent }: { percent: number }) {
  const cls = percent > 88 ? "is-red" : percent > 70 ? "is-amber" : "is-green";
  return (
    <div className="rt-progress">
      <span className={cls} style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
  );
}

const STATUS_ORDER: Status[] = ["queued", "doing", "handoff", "blocked", "done"];

export default function Overview() {
  const vocab = useVocab();
  const reader = useCallback(async () => {
        const [status, nodes, skills, actors] = await Promise.all([
          api.get<StatusInfo>("/api/status"),
          api.get<NodeInfo[]>("/api/nodes"),
          api.get<SkillInfo[]>("/api/skills"),
          api.get<ActorInfo[]>("/api/actors"),
        ]);
        return { status, nodes, skills, actors };
  }, []);
  const { data, refreshing: loading, error, fetchedAt } = useOperationsRead("system-overview", reader);
  const status = data?.status;
  const nodes = data?.nodes ?? [];
  const skills = data?.skills ?? [];
  const actors = data?.actors ?? [];

  const skillCats = useMemo(() => {
    const map = new Map<string, number>();
    for (const skill of skills) {
      const key = skill.category || "未分类";
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [skills]);

  const agents = actors.filter((a) => a.kind === "agent" && !a.disabled && !isSessionSync(a));
  const counts = status?.task_counts ?? {};
  const totalTasks = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <div className="rt-page">
      <Ambient />
      <PageHeader
        kicker="SYSTEM OVERVIEW"
        title="系统总览"
        subtitle="一个界面掌握节点、智能体、任务与知识流的全局状态。"
      />

      {loading && !data && <DataState loading />}
      {error && <DataState error={error} stale={!!data} />}
      {data && <p className="ops-source-note">页面读取 {sourceTime(fetchedAt)} · 节点新鲜度按最后入库遥测时间及 30 分钟阈值判断，不代表 worker 在线或离线。</p>}

      {data && <>

      <div className="rt-metrics">
        <Metric
          icon={<Bot />}
          label="启用的模型 Worker"
          value={
            <>
              {agents.filter((actor) => actor.online).length}
              <em className="rt-metric__frac">/ {agents.length}</em>
            </>
          }
          sub="近期认证上报 / 启用总数 · 不含同步代理"
          tone="green"
        />
        {/* Fleet counts that used to live on Home: queued/doing/blocked stay here with agents/skills/nodes/knowledge. */}
        <Metric
          icon={<ListChecks />}
          label="任务"
          value={totalTasks}
          sub={`待办/移交 ${(counts["queued"] ?? 0) + (counts["handoff"] ?? 0)} · 进行中 ${counts["doing"] ?? 0} · 受阻 ${counts["blocked"] ?? 0}`}
          tone="blue"
        />
        <Metric
          icon={<Sparkles />}
          label="技能"
          value={status?.skills ?? 0}
          sub={`${skillCats.length} 个分类`}
          tone="amber"
        />
        <Metric icon={<Server />} label="节点" value={status?.nodes ?? 0} sub="健康心跳接入" tone="teal" />
        <Metric
          icon={<BookOpen />}
          label="知识源"
          value={status?.knowledge_sources ?? 0}
          sub="Vault / Wiki / 语料"
          tone="amber"
        />
      </div>

      <div className="rt-strip">
        {STATUS_ORDER.map((s) => (
          <span key={s} className={`chip chip-status-${s}`}>
            {STATUS_LABEL[s]} {counts[s] ?? 0}
          </span>
        ))}
        <span className="rt-strip__right">
          {skillCats.slice(0, 6).map(([cat, n]) => (
            <span key={cat} className="chip">
              {cat} {n}
            </span>
          ))}
        </span>
      </div>

      <Panel icon={<Server size={15} />} kicker="NODES & DEVICES" title="节点与设备">
        <div className="rt-node-grid">
          {nodes.map((node) => {
            const memTotal = node.memory.total ?? 0;
            const memUsedPct = memTotal
              ? ((memTotal - (node.memory.available ?? 0)) / memTotal) * 100
              : 0;
            const observation = observationState(node.updated_at);
            return (
              <article key={node.id} className="rt-node-card">
                <header>
                  <div>
                    <strong>{node.label || node.id}</strong>
                    <p>
                      {node.hostname} · 运行 {fmtUptime(node.uptime_seconds)}
                      {node.load.length > 0 && ` · 负载 ${node.load[0].toFixed(2)}`}
                    </p>
                  </div>
                  <span className={`rt-badge ${observation === "fresh" ? "rt-badge--good" : "rt-badge--warn"}`}>
                    {({ fresh: "近期遥测", stale: "遥测已旧", unknown: "遥测时间未知", clock_skew: "时钟待核对" })[observation]}
                  </span>
                </header>
                <p className="ops-source-note">来源入库遥测 {sourceTime(node.updated_at)} · 时效阈值 30 分钟</p>
                <div className="rt-resource">
                  <span>
                    <MemoryStick size={12} /> 内存 {memUsedPct.toFixed(0)}%
                  </span>
                  <Bar percent={memUsedPct} />
                </div>
                <div className="rt-resource">
                  <span>
                    <HardDrive size={12} /> 磁盘 {(node.disk.percent ?? 0).toFixed(0)}%
                  </span>
                  <Bar percent={node.disk.percent ?? 0} />
                </div>
                {node.services.length > 0 && (
                  <footer>
                    {node.services.slice(0, 5).map((svc) => (
                      <span
                        key={svc.unit}
                        className={`rt-svc ${svc.healthy ? "is-ok" : "is-bad"}`}
                        title={`${svc.unit} · ${svc.active}/${svc.sub ?? ""}`}
                      >
                        <Activity size={11} />
                        {(svc.label || svc.unit || "").replace(".service", "")}
                      </span>
                    ))}
                  </footer>
                )}
              </article>
            );
          })}
          {nodes.length === 0 && (
            <p className="muted">{vocab.nodeSyncHint}</p>
          )}
        </div>
      </Panel>
      </>}
    </div>
  );
}
