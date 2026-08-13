import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Bot,
  Clock3,
  Cloud,
  Link2,
  ListChecks,
  LockKeyhole,
  MessageCircle,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Smartphone,
  TerminalSquare,
} from "lucide-react";
import { api, ApiError } from "../api";
import { Avatar } from "../avatar";
import { Ambient, PageHeader, Panel } from "../components/ui";
import TaskDrawer from "../components/TaskDrawer";
import type {
  ActorInfo,
  Me,
  Priority,
  RuntimeSessionInfo,
  SessionPrivacy,
  Task,
} from "../types";
import { PRIORITY_LABEL, STATUS_LABEL } from "../types";
import { useVocab } from "../theme";
import "./sessions.css";

const PRIVACY_LABEL: Record<SessionPrivacy, string> = {
  metadata: "仅索引",
  summary: "摘要",
  full: "最近消息",
};

const RUNTIME_LABEL: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  kimi: "Kimi",
  "kimi-legacy": "Kimi",
  hermes: "Hermes",
};

const PRIORITIES: Priority[] = ["urgent", "high", "medium", "low", "none"];

function runtimeLabel(value: string): string {
  return RUNTIME_LABEL[value] || value;
}

function timeLabel(value: string | null): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const diff = Math.max(0, Date.now() - date.getTime());
  const minute = 60_000;
  if (diff < minute) return "刚刚";
  if (diff < 60 * minute) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < 24 * 60 * minute) return `${Math.floor(diff / (60 * minute))} 小时前`;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function exactTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

interface SessionCaptureInfo {
  id: number;
  session_id: number;
  actor_id: string;
  kind: string;
  title: string;
  markdown: string;
  status: "queued" | "exported";
  target_path: string;
  created_at: string | null;
  exported_at: string | null;
}

export default function Sessions({
  me,
  focusSessionId,
}: {
  me: Me;
  focusSessionId?: number | null;
}) {
  const vocab = useVocab();
  const [sessions, setSessions] = useState<RuntimeSessionInfo[]>([]);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<RuntimeSessionInfo | null>(null);
  const [linkedTask, setLinkedTask] = useState<Task | null>(null);
  const [taskDrawerId, setTaskDrawerId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [runtime, setRuntime] = useState("all");
  const [privacy, setPrivacy] = useState<SessionPrivacy | "all">("all");
  const [mobileDetail, setMobileDetail] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [captures, setCaptures] = useState<SessionCaptureInfo[]>([]);
  const [captureBusy, setCaptureBusy] = useState(false);
  const [taskOpen, setTaskOpen] = useState(false);
  const [taskBusy, setTaskBusy] = useState(false);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDept, setTaskDept] = useState(vocab.centralHub);
  const [taskHolder, setTaskHolder] = useState("");
  const [taskPriority, setTaskPriority] = useState<Priority>("medium");
  const [taskAcceptance, setTaskAcceptance] = useState("");
  const [actionNotice, setActionNotice] = useState("");

  const enabledActors = useMemo(
    () => actors.filter((actor) => !actor.disabled),
    [actors]
  );

  const load = useCallback(async () => {
    try {
      const [rows, actorRows] = await Promise.all([
        api.get<RuntimeSessionInfo[]>("/api/sessions?limit=200"),
        api.get<ActorInfo[]>("/api/actors"),
      ]);
      setSessions(rows);
      setActors(actorRows);
      setSelectedId((current) => {
        if (focusSessionId && rows.some((item) => item.id === focusSessionId)) {
          return focusSessionId;
        }
        return current && rows.some((item) => item.id === current)
          ? current
          : (rows[0]?.id ?? null);
      });
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "会话列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [focusSessionId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!focusSessionId) return;
    setSelectedId(focusSessionId);
    setMobileDetail(true);
  }, [focusSessionId]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setCaptures([]);
      return;
    }
    void api
      .get<RuntimeSessionInfo>(`/api/sessions/${selectedId}`)
      .then((row) => {
        setDetail(row);
        setTaskTitle(`${row.actor_name}：${row.title || "会话事项"}`);
        setTaskHolder((current) => current || row.actor_id);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "会话详情加载失败")
      );
    void api
      .get<SessionCaptureInfo[]>(`/api/sessions/${selectedId}/captures`)
      .then(setCaptures)
      .catch(() => setCaptures([]));
  }, [selectedId]);

  useEffect(() => {
    if (!detail?.task_id) {
      setLinkedTask(null);
      return;
    }
    void api
      .get<Task>(`/api/tasks/${detail.task_id}`)
      .then(setLinkedTask)
      .catch(() => setLinkedTask(null));
  }, [detail?.task_id]);

  const runtimes = useMemo(
    () => Array.from(new Set(sessions.map((item) => item.runtime))).sort(),
    [sessions]
  );

  const visible = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return sessions.filter((item) => {
      if (runtime !== "all" && item.runtime !== runtime) return false;
      if (privacy !== "all" && item.privacy !== privacy) return false;
      if (!term) return true;
      return [item.title, item.summary, item.actor_name, item.runtime, item.node]
        .join(" ")
        .toLocaleLowerCase()
        .includes(term);
    });
  }, [privacy, query, runtime, sessions]);

  const agentCount = new Set(sessions.map((item) => item.actor_id)).size;
  const textCount = sessions.filter((item) => item.privacy !== "metadata").length;
  const readonly = Boolean(me.readonly) || me.role === "viewer";

  const holderName = useCallback(
    (actorId: string) => actors.find((actor) => actor.id === actorId)?.display_name || actorId,
    [actors]
  );

  function choose(item: RuntimeSessionInfo) {
    setSelectedId(item.id);
    setDetail(item);
    setTaskTitle(`${item.actor_name}：${item.title || "会话事项"}`);
    setTaskHolder(item.actor_id);
    setTaskDept(vocab.centralHub);
    setTaskPriority("medium");
    setTaskOpen(false);
    setActionNotice("");
    setMobileDetail(true);
  }

  async function queueObsidianCapture() {
    if (!detail) return;
    setCaptureBusy(true);
    try {
      const capture = await api.post<SessionCaptureInfo>(
        `/api/sessions/${detail.id}/capture-obsidian`,
        {}
      );
      setCaptures((current) => [capture, ...current.filter((item) => item.id !== capture.id)]);
      setActionNotice("已建立 OB 来源卡；同步完成后会写入本地 Vault。");
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "无法创建 OB 提取队列");
    } finally {
      setCaptureBusy(false);
    }
  }

  async function createTaskFromSession() {
    if (!detail || !taskTitle.trim() || !taskDept.trim()) {
      setError("请填写任务标题和业务域。");
      return;
    }
    const acceptance = taskAcceptance
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    if (acceptance.length === 0) {
      setError("请至少填写一条可验证的验收条件，再发单。");
      return;
    }
    setTaskBusy(true);
    try {
      const created = await api.post<Task>(
        `/api/sessions/${detail.id}/create-task`,
        {
          title: taskTitle.trim(),
          dept: taskDept.trim(),
          holder: taskHolder || undefined,
          priority: taskPriority,
          acceptance,
        }
      );
      const task = await api.get<Task>(`/api/tasks/${created.id}`);
      setLinkedTask(task);
      setActionNotice(`任务卡 ${task.id} 已发给 ${holderName(task.holder)}，并与本会话互相引用。`);
      setTaskOpen(false);
      setTaskDrawerId(task.id);
      setDetail(await api.get<RuntimeSessionInfo>(`/api/sessions/${detail.id}`));
      await load();
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "无法从会话创建任务");
    } finally {
      setTaskBusy(false);
    }
  }

  async function refreshLinkedTask() {
    if (!linkedTask) return;
    try {
      setLinkedTask(await api.get<Task>(`/api/tasks/${linkedTask.id}`));
      if (detail) setDetail(await api.get<RuntimeSessionInfo>(`/api/sessions/${detail.id}`));
      await load();
    } catch {
      // The drawer surfaces task errors. This refresh is intentionally quiet.
    }
  }

  const archiveCaptures = captures.filter(
    (capture) => capture.kind === "recap" || capture.kind === "obsidian"
  );

  return (
    <div className={`rt-page sessions-page ${mobileDetail ? "is-mobile-detail" : ""}`}>
      <Ambient />
      <PageHeader
        kicker="MOBILE SESSION INBOX · READ-ONLY SYNC"
        title="会话中心"
        subtitle="把分散在终端的已授权会话，转成可验收、可交接、可归档的真实任务链。"
        tools={
          <span className="sessions-live">
            <RefreshCw size={13} />
            每日同步 · 30 秒刷新视图
          </span>
        }
      />

      <section className="sessions-privacy-note">
        <span className="sessions-privacy-note__icon"><ShieldCheck size={18} /></span>
        <div>
          <strong>原会话仍保存在 Agent 所在机器</strong>
          <p>{vocab.membersAuthNote}</p>
        </div>
        <div className="sessions-mini-stats" aria-label="同步概览">
          <span><b>{sessions.length}</b> 会话</span>
          <span><b>{agentCount}</b> Agent</span>
          <span><b>{textCount}</b> 含正文</span>
        </div>
      </section>

      <div className="sessions-toolbar">
        <label className="sessions-search">
          <Search size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索会话、Agent 或摘要"
            aria-label="搜索会话"
          />
        </label>
        <label>
          <span>运行终端</span>
          <select value={runtime} onChange={(event) => setRuntime(event.target.value)}>
            <option value="all">全部</option>
            {runtimes.map((item) => <option key={item} value={item}>{runtimeLabel(item)}</option>)}
          </select>
        </label>
        <label>
          <span>同步内容</span>
          <select value={privacy} onChange={(event) => setPrivacy(event.target.value as SessionPrivacy | "all")}>
            <option value="all">全部层级</option>
            <option value="metadata">仅索引</option>
            <option value="summary">摘要</option>
            <option value="full">最近消息</option>
          </select>
        </label>
      </div>

      {error && <p className="error sessions-error">{error}</p>}

      <div className="sessions-layout">
        <Panel
          icon={<Cloud size={15} />}
          kicker="SYNCED SESSIONS"
          title="最近会话"
          className="sessions-list-panel"
          tools={<span className="sessions-count">{visible.length} 条</span>}
        >
          <div className="sessions-list" aria-live="polite">
            {visible.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`session-row ${selectedId === item.id ? "is-active" : ""}`}
                onClick={() => choose(item)}
              >
                <Avatar name={item.actor_name} size={36} square />
                <span className="session-row__body">
                  <span className="session-row__title">{item.title || "未命名会话"}</span>
                  <span className="session-row__meta">{item.actor_name} · {runtimeLabel(item.runtime)}</span>
                  <span className="session-row__summary">{item.summary || `${item.message_count} 条原生消息，正文未同步`}</span>
                  <span className="session-row__foot">
                    <em className={`privacy-chip privacy-chip--${item.privacy}`}>{PRIVACY_LABEL[item.privacy]}</em>
                    <time>{timeLabel(item.updated_at || item.synced_at)}</time>
                  </span>
                </span>
              </button>
            ))}
            {!loading && visible.length === 0 && (
              <div className="sessions-empty">
                <MessageCircle size={22} />
                <strong>还没有匹配的会话</strong>
                <p>调整筛选条件，或先在 Agent 所在机器运行会话同步。</p>
              </div>
            )}
            {loading && <p className="muted sessions-loading">正在读取会话索引…</p>}
          </div>
        </Panel>

        <Panel
          icon={<TerminalSquare size={15} />}
          kicker="SESSION TO DELIVERY"
          title={detail?.title || "会话详情"}
          className="sessions-detail-panel"
          tools={
            <button type="button" className="sessions-back" onClick={() => setMobileDetail(false)}>
              <ArrowLeft size={14} /> 返回
            </button>
          }
        >
          {detail ? (
            <div className="session-detail">
              <header className="session-detail__head">
                <Avatar name={detail.actor_name} size={42} square />
                <div>
                  <strong>{detail.actor_name}</strong>
                  <span><Bot size={12} /> {runtimeLabel(detail.runtime)}{detail.node && <> · {detail.node}</>}</span>
                </div>
                <em className={`privacy-chip privacy-chip--${detail.privacy}`}><LockKeyhole size={11} />{PRIVACY_LABEL[detail.privacy]}</em>
              </header>

              <div className="session-detail__facts">
                <span><Clock3 size={12} />更新 {exactTime(detail.updated_at)}</span>
                <span><Cloud size={12} />同步 {timeLabel(detail.synced_at)}</span>
                <span>{detail.message_count} 条原生消息</span>
                {detail.task_id && <span className="session-task-link"><Link2 size={12} />{detail.task_title || detail.task_id}</span>}
              </div>

              {detail.summary && <section className="session-summary"><span>会话摘要</span><p>{detail.summary}</p></section>}

              {detail.privacy === "full" && detail.messages.length > 0 ? (
                <div className="session-messages">
                  {detail.messages.map((message, index) => (
                    <article key={`${message.at || "message"}-${index}`} className={`session-message session-message--${message.role}`}>
                      <header><strong>{message.role === "assistant" ? detail.actor_name : "你"}</strong><time>{message.at ? timeLabel(message.at) : ""}</time></header>
                      <p>{message.text}</p>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="session-locked">
                  {detail.privacy === "metadata" ? <LockKeyhole size={22} /> : <MessageCircle size={22} />}
                  <strong>{detail.privacy === "metadata" ? "这条会话只同步了索引" : "这条会话只同步了摘要"}</strong>
                  <p>{detail.privacy === "metadata" ? "提示词和回复仍只存在于原终端。" : "逐条消息仍只存在于原终端。"}</p>
                </div>
              )}

              {linkedTask && (
                <section className="session-task-bridge" aria-label="关联任务进度">
                  <header>
                    <div><span>TASK IN MOTION</span><strong>任务已进入执行链</strong></div>
                    <em className={`session-task-status session-task-status--${linkedTask.status}`}>{STATUS_LABEL[linkedTask.status]}</em>
                  </header>
                  <div className="session-task-bridge__facts">
                    <span><ListChecks size={13} />{holderName(linkedTask.holder)}</span>
                    <span>优先级 {PRIORITY_LABEL[linkedTask.priority]}</span>
                    <span>{linkedTask.acceptance.length} 条验收条件</span>
                    <span>{linkedTask.progress}% 进度</span>
                  </div>
                  <div className="session-task-timeline">
                    {linkedTask.chain.slice(-3).reverse().map((event, index) => (
                      <p key={`${event.at}-${index}`}><time>{timeLabel(event.at)}</time><strong>{holderName(event.who)}</strong><span>{event.did}</span></p>
                    ))}
                    {linkedTask.chain.length === 0 && <p className="muted">任务刚创建，等待执行者接棒。</p>}
                  </div>
                  <button type="button" className="session-action session-action--primary" onClick={() => setTaskDrawerId(linkedTask.id)}>
                    <Send size={14} />打开任务详情，推进执行与回执
                  </button>
                </section>
              )}

              <section className="session-workflow" aria-label="会话提取流程">
                <header>
                  <div><span>CONVERSATION ROUTE</span><strong>查看摘要 · 提取任务 · 发单 · 回执归档</strong></div>
                  <em>{detail.task_id ? "任务已关联" : detail.privacy === "metadata" ? "先建来源卡" : "可发单"}</em>
                </header>
                <div className="session-route session-route--full">
                  <span>授权会话</span><b>→</b><span>摘要 / 来源卡</span><b>→</b><span>任务与验收</span><b>→</b><span>执行 / 交接</span><b>→</b><span>回执归档</span>
                </div>
                {detail.privacy === "metadata" && <p className="session-route-note">当前是“仅索引”模式：可以建立来源卡和任务关联，但不会凭空理解正文。若要沉淀内容，请在原设备主动把这条会话升级为“脱敏摘要”后再次同步。</p>}

                {!readonly && !detail.task_id && (
                  <div className="session-actions">
                    <button type="button" className="session-action session-action--soft" disabled={captureBusy} onClick={() => void queueObsidianCapture()}>
                      <Link2 size={14} />{captureBusy ? "正在建卡…" : "建立 OB 来源卡"}
                    </button>
                    <button type="button" className="session-action session-action--primary" onClick={() => setTaskOpen((value) => !value)}>
                      <Send size={14} />提取并发单
                    </button>
                  </div>
                )}
                {detail.task_id && <p className="session-route-success">已关联任务：{detail.task_title || detail.task_id}。执行、交接、审核与交付均写入同一条任务链。</p>}
                {archiveCaptures.length > 0 && (
                  <div className="session-archive-list">
                    {archiveCaptures.slice(0, 2).map((capture) => (
                      <p key={capture.id} className={capture.status === "exported" ? "session-route-success" : "session-route-pending"}>
                        {capture.kind === "recap" ? "自动 recap" : "OB 来源卡"} · {capture.status === "exported" ? `已归档：${capture.target_path || "已完成"}` : "已排队，等待本机同步写入。"}
                      </p>
                    ))}
                  </div>
                )}
                {actionNotice && <p className="session-route-success">{actionNotice}</p>}

                {taskOpen && (
                  <form className="session-task-form" onSubmit={(event) => { event.preventDefault(); void createTaskFromSession(); }}>
                    <div className="session-form-grid">
                      <label>任务标题<input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} required /></label>
                      <label>业务域<input value={taskDept} onChange={(event) => setTaskDept(event.target.value)} placeholder={vocab.deptPlaceholder} required /></label>
                      <label>接办成员<select value={taskHolder} onChange={(event) => setTaskHolder(event.target.value)} required>{enabledActors.map((actor) => <option key={actor.id} value={actor.id}>{actor.display_name || actor.id}{actor.kind === "agent" ? "（智能体）" : ""}</option>)}</select></label>
                      <label>优先级<select value={taskPriority} onChange={(event) => setTaskPriority(event.target.value as Priority)}>{PRIORITIES.map((priorityValue) => <option key={priorityValue} value={priorityValue}>{PRIORITY_LABEL[priorityValue]}</option>)}</select></label>
                    </div>
                    <label>验收条件（至少一条，每行一条）<textarea value={taskAcceptance} onChange={(event) => setTaskAcceptance(event.target.value)} placeholder="例如：交付物路径已登记&#10;审核人可按验收条件复核" required /></label>
                    <p className="session-form-note">发单后自动绑定本会话为来源证据；任务详情里可推进执行、移交和交付回执。</p>
                    <button className="session-action session-action--primary" disabled={taskBusy || enabledActors.length === 0} type="submit">{taskBusy ? "正在发单…" : "创建并派给接办成员"}</button>
                  </form>
                )}
              </section>

              <footer className="session-relay-note">
                <span><Smartphone size={16} /></span>
                <div><strong>移动端用于查看与发单，原生续聊仍留在原设备</strong><p>这样手机上看到的是可靠的任务与证据链，而不是另一份会丢上下文的假会话。</p></div>
              </footer>
            </div>
          ) : (
            <div className="sessions-empty sessions-empty--detail"><TerminalSquare size={24} /><strong>选择一条会话</strong><p>右侧会显示授权范围内的摘要、任务链与归档状态。</p></div>
          )}
        </Panel>
      </div>

      {taskDrawerId && (
        <TaskDrawer
          taskId={taskDrawerId}
          me={me}
          actors={actors}
          onClose={() => setTaskDrawerId(null)}
          onChanged={() => void refreshLinkedTask()}
        />
      )}
    </div>
  );
}