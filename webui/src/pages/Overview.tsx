import { useEffect, useMemo, useState } from "react";
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
import { api, readErrorMessage } from "../api";
import type { ActorInfo, NodeInfo, SkillInfo, StatusInfo, Status } from "../types";
import { STATUS_LABEL, fmtUptime } from "../types";
import { Ambient, DataState, Metric, PageHeader, Panel } from "../components/ui";

function Bar({ percent }: { percent: number }) {
  const cls = percent > 88 ? "is-red" : percent > 70 ? "is-amber" : "is-green";
  return (
    <div className="rt-progress">
      <span className={cls} style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
  );
}

const STATUS_ORDER: Status[] = ["queued", "doing", "handoff", "blocked", "done"];

function heartbeatStaleAfterMs(nodeId: string) {
  const normalized = nodeId.toLowerCase();
  if (normalized === "windows") return 8 * 60 * 60 * 1000;
  if (normalized === "bridge") return 3 * 60 * 60 * 1000;
  return 2 * 60 * 60 * 1000;
}

export default function Overview() {
  const [status, setStatus] = useState<StatusInfo | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [statusInfo, nodeRows, skillRows, actorRows] = await Promise.all([
          api.get<StatusInfo>("/api/status"),
          api.get<NodeInfo[]>("/api/nodes"),
          api.get<SkillInfo[]>("/api/skills"),
          api.get<ActorInfo[]>("/api/actors"),
        ]);
        setStatus(statusInfo);
        setNodes(nodeRows);
        setSkills(skillRows);
        setActors(actorRows);
        setError(null);
      } catch (reason) {
        setError(readErrorMessage(reason));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const skillCats = useMemo(() => {
    const map = new Map<string, number>();
    for (const skill of skills) {
      const key = skill.category || "未分类";
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [skills]);

  const agents = actors.filter((a) => a.kind === "agent");
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

      {loading && <DataState loading />}
      {error && <DataState error={error} />}

      {!loading && !error && <>

      <div className="rt-metrics">
        <Metric
          icon={<Bot />}
          label="智能体"
          value={
            <>
              {status?.online_actors ?? 0}
              <em className="rt-metric__frac">/ {agents.length}</em>
            </>
          }
          sub="在线 / 总数"
          tone="green"
        />
        <Metric
          icon={<ListChecks />}
          label="任务"
          value={totalTasks}
          sub={`进行中 ${counts["doing"] ?? 0} · 受阻 ${counts["blocked"] ?? 0}`}
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
            const stale =
              node.updated_at !== null &&
              Date.now() - new Date(node.updated_at).getTime() > heartbeatStaleAfterMs(node.id);
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
                  <span className={`rt-badge ${stale ? "rt-badge--warn" : "rt-badge--good"}`}>
                    {stale ? "心跳过期" : "在线"}
                  </span>
                </header>
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
            <p className="muted">尚无节点心跳。在各节点运行 probe 命令或配置组织同步即可接入。</p>
          )}
        </div>
      </Panel>
      </>}
    </div>
  );
}
