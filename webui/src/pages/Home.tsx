import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Bot,
  CircleCheckBig,
  Clock3,
  MessageSquareText,
  Network,
  ScrollText,
  Server,
  ShieldAlert,
  Sparkles,
  SquareKanban,
} from "lucide-react";
import { api, readErrorMessage } from "../api";
import { useSummary } from "../lib/summary";
import type {
  Me,
  RuntimeSessionInfo,
  StatusInfo,
} from "../types";
import { STATUS_LABEL, localTodayISO } from "../types";
import DispatchMap from "../components/DispatchMap";
import ActionQueue from "../components/ActionQueue";
import { Ambient, DataState, Metric, PageHeader, Panel } from "../components/ui";
import { useVocab } from "../theme";
import { Avatar } from "../avatar";

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "夜深了";
  if (hour < 11) return "早上好";
  if (hour < 13) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function runtimeLabel(runtime: string): string {
  return (
    {
      codex: "Codex",
      "claude-code": "Claude Code",
      kimi: "Kimi",
      "kimi-legacy": "Kimi",
      hermes: "Hermes",
    }[runtime] || runtime
  );
}

export default function Home({
  me,
  onNavigate,
  onOpenTask,
  onOpenSession,
}: {
  me: Me;
  onNavigate: (page: string) => void;
  onOpenTask: (taskId: string) => void;
  onOpenSession: (sessionId: number) => void;
}) {
  const vocab = useVocab();
  // First screen is summary-driven: one aggregate fetch up front, then
  // incremental polls (updated_since watermark) merged into the cached task
  // list, so a growing task table never slows the paint. Status counts and
  // recent sessions are small, bounded reads and stay on their own poll.
  const {
    summary,
    tasks,
    error: summaryError,
    loading: summaryLoading,
    loaded: summaryLoaded,
  } = useSummary({ today: localTodayISO() });
  const [status, setStatus] = useState<StatusInfo | null>(null);
  const [sessions, setSessions] = useState<RuntimeSessionInfo[]>([]);
  const [auxLoading, setAuxLoading] = useState(true);
  const [auxLoaded, setAuxLoaded] = useState(false);
  const [auxError, setAuxError] = useState<string | null>(null);

  const loadAux = useCallback(async () => {
    try {
      const [statusInfo, sessionRows] = await Promise.all([
        api.get<StatusInfo>("/api/status"),
        api.get<RuntimeSessionInfo[]>("/api/sessions?limit=24"),
      ]);
      setStatus(statusInfo);
      setSessions(sessionRows);
      setAuxLoaded(true);
      setAuxError(null);
    } catch (reason) {
      setAuxError(readErrorMessage(reason));
    } finally {
      setAuxLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAux();
    const timer = setInterval(() => void loadAux(), 30_000);
    return () => clearInterval(timer);
  }, [loadAux]);

  const actors = summary?.actors ?? [];
  const counts = summary?.task_counts ?? status?.task_counts ?? {};
  const loading = summaryLoading || auxLoading;
  const loaded = summaryLoaded || auxLoaded;
  const error = summaryError ?? auxError;
  const doing = counts.doing ?? 0;
  const blocked = counts.blocked ?? 0;
  const queued = (counts.queued ?? 0) + (counts.handoff ?? 0);
  const agents = actors.filter((actor) => actor.kind === "agent");

  const recent = summary?.recent_events ?? [];

  const nameOf = useCallback(
    (id: string) => actors.find((actor) => actor.id === id)?.display_name || id,
    [actors]
  );

  const sessionPreview = sessions.slice(0, 3);
  const linkedSessions = sessions.filter((session) => session.task_id).length;
  const summarySessions = sessions.filter(
    (session) => session.privacy !== "metadata" && Boolean(session.summary)
  ).length;
  const delivered = recent.filter((event) => event.to_status === "done").length;

  return (
    <div className="rt-page">
      <Ambient />
      <PageHeader
        kicker="RETINUE · COMMAND HOME"
        title={`${greeting()},${me.display_name || me.name}`}
        subtitle={new Date().toLocaleDateString("zh-CN", {
          year: "numeric",
          month: "long",
          day: "numeric",
          weekday: "long",
        })}
      />

      {loading && !loaded && <DataState loading />}
      {error && <DataState error={error} stale={loaded} />}

      <ActionQueue onOpenTask={onOpenTask} />

      {loaded && <>
      <div className="rt-metrics">
        <Metric
          icon={<Clock3 />}
          label="待办 / 移交"
          value={queued}
          sub="等待认领与交接"
          tone="ink"
          onClick={() => onNavigate("board")}
        />
        <Metric
          icon={<Activity />}
          label="进行中"
          value={doing}
          sub={vocab.membersExecuting}
          tone="blue"
          onClick={() => onNavigate("board")}
        />
        <Metric
          icon={<ShieldAlert />}
          label="受阻"
          value={blocked}
          sub={blocked > 0 ? "需要介入处理" : "一切顺畅"}
          tone={blocked > 0 ? "red" : "teal"}
          onClick={() => onNavigate("board")}
        />
        <Metric
          icon={<Bot />}
          label="在线智能体"
          value={
            <>
              {status?.online_actors ?? 0}
              <em className="rt-metric__frac">/ {agents.length}</em>
            </>
          }
          sub="15 分钟活跃推断"
          tone="green"
          onClick={() => onNavigate("agents")}
        />
        <Metric
          icon={<Sparkles />}
          label="技能"
          value={status?.skills ?? 0}
          sub="能力登记总数"
          tone="amber"
          onClick={() => onNavigate("skills")}
        />
        <Metric
          icon={<Server />}
          label="节点"
          value={status?.nodes ?? 0}
          sub="接入健康心跳"
          tone="teal"
          onClick={() => onNavigate("infra")}
        />
        <Metric
          icon={<BookOpen />}
          label="知识源"
          value={status?.knowledge_sources ?? 0}
          sub="Vault / Wiki / 语料"
          tone="amber"
          onClick={() => onNavigate("knowledge")}
        />
      </div>

      <div className="rt-layout rt-layout--hero">
        <Panel
          icon={<Network size={15} />}
          kicker="DISPATCH FLOW"
          title="派单协调"
          tools={
            <button className="rt-button rt-button--soft" onClick={() => onNavigate("board")}>
              <SquareKanban size={14} /> 打开看板
            </button>
          }
        >
          <DispatchMap tasks={tasks} actors={actors} />
        </Panel>

        <Panel
          icon={<MessageSquareText size={15} />}
          kicker="CONVERSATION TO DELIVERY"
          title="会话流转台"
          className="rt-receipts-panel"
          tools={
            <button className="rt-button rt-button--soft" onClick={() => onNavigate("sessions")}>
              会话中心 <ArrowRight size={14} />
            </button>
          }
        >
          <div className="rt-command-workbench">
            <div className="rt-command-flow" aria-label="会话到交付状态">
              <span><MessageSquareText size={13} /><b>{summarySessions}</b><em>可提取摘要</em></span>
              <span><ScrollText size={13} /><b>{linkedSessions}</b><em>已转任务</em></span>
              <span><Activity size={13} /><b>{doing}</b><em>正在执行</em></span>
              <span><CircleCheckBig size={13} /><b>{delivered}</b><em>最近交付</em></span>
            </div>

            <section className="rt-command-sessions" aria-label="最近可提取会话">
              <header>
                <span>最近可提取会话</span>
                <small>摘要 → 发单 → 回执</small>
              </header>
              <div>
                {sessionPreview.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    className="rt-session-pulse"
                    onClick={() => onOpenSession(session.id)}
                  >
                    <Avatar name={session.actor_name} size={28} square />
                    <span>
                      <strong>{session.title || "未命名会话"}</strong>
                      <em>{session.actor_name} · {runtimeLabel(session.runtime)}</em>
                      <small>{session.summary || `${session.message_count} 条原生消息，正文未同步`}</small>
                    </span>
                    <ArrowRight size={14} />
                  </button>
                ))}
                {sessionPreview.length === 0 && (
                  <p className="rt-command-empty">会话同步完成后，这里会出现可提取的摘要。</p>
                )}
              </div>
            </section>

            <section className="rt-command-receipts" aria-label="最近回执">
              <header>
                <span>最近回执</span>
                <small>{recent.length} 条任务事件</small>
              </header>
              <div className="rt-receipt-list" role="list">
                {recent.slice(0, 3).map((event, index) => (
                  <article key={`${event.task_id}-${index}`} className="rt-receipt-row" role="listitem">
                    <Avatar name={nameOf(event.who)} size={28} square />
                    <div className="rt-receipt-row__content">
                      <div className="rt-receipt-row__meta">
                        <strong>{nameOf(event.who)}</strong>
                        <div className="rt-receipt-row__trail">
                          {event.to_status && event.from_status !== event.to_status && (
                            <span className="rt-receipt-status">{STATUS_LABEL[event.to_status]}</span>
                          )}
                          <time dateTime={event.at}>{event.at.slice(5, 16).replace("T", " ")}</time>
                        </div>
                      </div>
                      <p className="rt-receipt-row__action" title={event.did}>{event.did}</p>
                      <p className="rt-receipt-row__task" title={event.task_title}>{event.task_title}</p>
                    </div>
                  </article>
                ))}
                {recent.length === 0 && <p className="rt-receipt-empty">还没有任何回执</p>}
              </div>
            </section>

            <button className="rt-command-cta" type="button" onClick={() => onNavigate("sessions")}>
              查看完整会话工作流 <ArrowRight size={14} />
            </button>
          </div>
        </Panel>
      </div>
      </>}
    </div>
  );
}
