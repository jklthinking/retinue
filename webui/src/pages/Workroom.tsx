import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  MessageSquareText,
  Plus,
  Route,
  Search,
  Send,
  Sparkles,
} from "lucide-react";
import { api, ApiError } from "../api";
import type {
  ActorInfo,
  AgentMatchInfo,
  Me,
  PipelineTemplateInfo,
  Priority,
  Task,
  TaskSummary,
} from "../types";
import { PRIORITY_LABEL, STATUS_LABEL } from "../types";
import { Avatar } from "../avatar";
import StageStepper from "../components/StageStepper";
import TaskDrawer from "../components/TaskDrawer";
import { Ambient, PageHeader, Panel } from "../components/ui";
import "./workroom.css";

type DispatchMode = "direct" | "pipeline";
const PRIORITIES: Priority[] = ["urgent", "high", "medium", "low", "none"];
const TERMINAL = new Set(["done", "cancelled"]);
const EXAMPLES = ["准备七年级英语第一课", "制作互动练习和课后作业", "把课堂记录整理成家长反馈"];

function shortTime(value: string): string {
  return value.slice(5, 16).replace("T", " ");
}

export default function Workroom({ me }: { me: Me }) {
  const [intent, setIntent] = useState("");
  const [matches, setMatches] = useState<AgentMatchInfo[]>([]);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [templates, setTemplates] = useState<PipelineTemplateInfo[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [mode, setMode] = useState<DispatchMode>("direct");
  const [priority, setPriority] = useState<Priority>("medium");
  const [acceptance, setAcceptance] = useState("");
  const [message, setMessage] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const bootstrapped = useRef(false);
  const loadSeq = useRef(0);
  const detailSeq = useRef(0);
  const selectedTaskIdRef = useRef<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);

  const loadTaskDetail = useCallback(async (taskId: string | null) => {
    const seq = ++detailSeq.current;
    if (!taskId) {
      setSelectedTask(null);
      return;
    }
    try {
      const task = await api.get<Task>(`/api/tasks/${taskId}`);
      if (seq === detailSeq.current) setSelectedTask(task);
    } catch (err) {
      if (seq === detailSeq.current) {
        setError(err instanceof ApiError ? err.message : "任务详情加载失败");
      }
    }
  }, []);

  const loadCore = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const [actorRows, templateRows, taskRows] = await Promise.all([
        api.get<ActorInfo[]>("/api/actors"),
        api.get<PipelineTemplateInfo[]>("/api/pipeline-templates"),
        api.getAllPages<TaskSummary>("/api/tasks"),
      ]);
      if (seq !== loadSeq.current) return;
      setActors(actorRows);
      setTemplates(templateRows);
      setTasks(taskRows);
      setSelectedTemplate((current) => current || String(templateRows[0]?.id ?? ""));
      if (!bootstrapped.current) {
        const firstTaskId = taskRows[0]?.id ?? null;
        selectedTaskIdRef.current = firstTaskId;
        setSelectedTaskId(firstTaskId);
        bootstrapped.current = true;
      }
      void loadTaskDetail(selectedTaskIdRef.current);
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setError(err instanceof ApiError ? err.message : "协作空间加载失败");
    }
  }, [loadTaskDetail]);

  useEffect(() => {
    void loadCore();
    const timer = setInterval(() => void loadCore(), 8_000);
    return () => clearInterval(timer);
  }, [loadCore]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void api
        .get<AgentMatchInfo[]>(`/api/agent-match?q=${encodeURIComponent(intent.trim())}`)
        .then((rows) => {
          setMatches(rows);
          setSelectedAgent((current) =>
            rows.some((row) => row.id === current) ? current : (rows[0]?.id ?? "")
          );
        })
        .catch((err) =>
          setError(err instanceof ApiError ? err.message : "Agent 匹配失败")
        );
    }, 180);
    return () => clearTimeout(timer);
  }, [intent]);

  useEffect(() => {
    selectedTaskIdRef.current = selectedTaskId;
    void loadTaskDetail(selectedTaskId);
  }, [loadTaskDetail, selectedTaskId]);
  const selectedMatch = matches.find((match) => match.id === selectedAgent);
  const selectedFlow = templates.find((template) => String(template.id) === selectedTemplate);
  const recentTasks = tasks.filter((task) => task.status !== "cancelled").slice(0, 7);

  const nameOf = useCallback(
    (id: string) => actors.find((actor) => actor.id === id)?.display_name || id,
    [actors]
  );

  async function dispatch(event: FormEvent) {
    event.preventDefault();
    if (!intent.trim()) return;
    setBusy(true);
    setError("");
    try {
      const criteria = acceptance
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      const body =
        mode === "pipeline"
          ? {
              title: intent.trim(),
              pipeline: selectedFlow?.stages ?? [],
              priority,
              acceptance: criteria,
              note: "从网页协作空间启动流程",
            }
          : {
              title: intent.trim(),
              holder: selectedAgent,
              priority,
              acceptance: criteria,
              note: "从网页协作空间直接派单",
            };
      const created = await api.post<Task>("/api/tasks", body);
      await loadCore();
      setSelectedTaskId(created.id);
      setMessage("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "发单失败");
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!selectedTask || !message.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/tasks/${selectedTask.id}/update`, {
        note: message.trim(),
      });
      setMessage("");
      await loadCore();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "补充要求发送失败");
    } finally {
      setBusy(false);
    }
  }

  function startDraft() {
    setSelectedTaskId(null);
    setDrawerOpen(false);
    setIntent("");
    setAcceptance("");
    setMessage("");
    setError("");
  }

  return (
    <div className="rt-page workroom-page">
      <Ambient />
      <PageHeader
        kicker="AGENT WORKROOM · SEARCH, DISPATCH, FLOW"
        title="协作空间"
        subtitle="搜索合适的 Agent，在网页里发单、沟通并追踪每一次接棒。"
        tools={
          <button className="rt-button rt-button--primary" onClick={startDraft}>
            <Plus size={14} /> 新发一单
          </button>
        }
      />

      <form className="workroom-compose" onSubmit={dispatch}>
        <div className="workroom-compose__prompt">
          <label htmlFor="workroom-intent">
            <Sparkles size={14} />
            告诉众卿你想完成什么
          </label>
          <textarea
            id="workroom-intent"
            value={intent}
            onChange={(event) => setIntent(event.target.value)}
            placeholder="例如：为基础较弱的七年级学生准备一节 45 分钟英语课，包含教案、课件和分层练习。"
            rows={3}
            maxLength={500}
          />
          <div className="workroom-examples" aria-label="任务示例">
            {EXAMPLES.map((example) => (
              <button key={example} type="button" onClick={() => setIntent(example)}>
                {example}
              </button>
            ))}
          </div>
        </div>
        <div className="workroom-compose__settings">
          <div className="workroom-mode" aria-label="派单方式">
            <button
              type="button"
              className={mode === "direct" ? "is-active" : ""}
              aria-pressed={mode === "direct"}
              onClick={() => setMode("direct")}
            >
              单 Agent
            </button>
            <button
              type="button"
              className={mode === "pipeline" ? "is-active" : ""}
              aria-pressed={mode === "pipeline"}
              disabled={templates.length === 0}
              onClick={() => setMode("pipeline")}
            >
              多 Agent 流程
            </button>
          </div>
          <div className="workroom-settings-row">
            <label>
              优先级
              <select
                value={priority}
                onChange={(event) => setPriority(event.target.value as Priority)}
              >
                {PRIORITIES.map((item) => (
                  <option key={item} value={item}>
                    {PRIORITY_LABEL[item]}
                  </option>
                ))}
              </select>
            </label>
            {mode === "pipeline" && (
              <label>
                流程
                <select
                  value={selectedTemplate}
                  onChange={(event) => setSelectedTemplate(event.target.value)}
                >
                  {templates.map((template) => (
                    <option key={template.id} value={template.id}>
                      {template.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <label className="workroom-acceptance">
            验收要求 <span>每行一条，可选</span>
            <textarea
              value={acceptance}
              onChange={(event) => setAcceptance(event.target.value)}
              rows={2}
              placeholder="可以直接拿来上课&#10;练习附答案"
            />
          </label>
          <button
            className="workroom-dispatch"
            disabled={
              busy ||
              !intent.trim() ||
              (mode === "direct" && !selectedAgent) ||
              (mode === "pipeline" && !selectedFlow)
            }
          >
            <Send size={14} />
            {busy ? "正在发单…" : mode === "pipeline" ? "启动协作流程" : "派给推荐 Agent"}
          </button>
        </div>
      </form>

      {error && <p className="error workroom-error">{error}</p>}

      <div className="workroom-grid">
        <Panel
          icon={<Search size={15} />}
          kicker="AGENT MATCH"
          title="推荐 Agent"
          className="workroom-agents"
          tools={<span className="workroom-count">{matches.length} 位</span>}
        >
          <p className="workroom-panel-note">按职责目标、已分配技能、运行时实测、在线状态、负载与交付记录排序；推荐不会自动派单</p>
          <div className="workroom-agent-list">
            {matches.map((agent, index) => (
              <button
                key={agent.id}
                type="button"
                className={`workroom-agent ${selectedAgent === agent.id ? "is-selected" : ""}`}
                onClick={() => {
                  setSelectedAgent(agent.id);
                  setMode("direct");
                }}
              >
                <Avatar name={agent.display_name || agent.id} size={34} square />
                <span className="workroom-agent__body">
                  <span className="workroom-agent__head">
                    <strong>{agent.display_name || agent.id}</strong>
                    <span className="workroom-score">{agent.score}%</span>
                  </span>
                  <span className="workroom-agent__status">
                    <i className={agent.online ? "is-online" : ""} />
                    {agent.online ? "在线" : "离线"} · {agent.runtime || "未绑定运行时"}{agent.node ? " · " + agent.node : ""} · {agent.active_tasks} 单在手
                  </span>
                  <span className="workroom-agent__skills">
                    {agent.role && <em>{agent.role}</em>}
                    {agent.matched_skills.slice(0, 3).map((skill) => (
                      <em key={skill}>{skill}</em>
                    ))}
                  </span>
                  <span className="workroom-agent__reason">
                    {agent.reasons[0] || "等待能力登记"}
                  </span>
                </span>
                {index === 0 && intent.trim() && <span className="workroom-best">首选</span>}
              </button>
            ))}
            {matches.length === 0 && <p className="muted">没有可用的 Agent</p>}
          </div>
        </Panel>

        <Panel
          icon={<MessageSquareText size={15} />}
          kicker="TASK THREAD"
          title={selectedTask ? "任务沟通" : "等待发单"}
          className="workroom-thread"
          tools={
            selectedTask ? (
              <span className={`chip chip-status-${selectedTask.status}`}>
                {STATUS_LABEL[selectedTask.status]}
              </span>
            ) : undefined
          }
        >
          {selectedTask ? (
            <>
              <header className="workroom-thread__head">
                <div>
                  <strong>{selectedTask.title}</strong>
                  <span>
                    {selectedTask.id} · 当前持棒 {nameOf(selectedTask.holder)}
                  </span>
                </div>
                <button type="button" onClick={() => setDrawerOpen(true)}>
                  查看详情
                </button>
              </header>
              <div className="workroom-timeline" aria-live="polite">
                {selectedTask.chain.map((event, index) => {
                  const mine = event.who === me.actor_id;
                  const moved =
                    event.from_status !== event.to_status ||
                    event.from_holder !== event.to_holder;
                  return (
                    <article
                      key={`${event.at}-${index}`}
                      className={`workroom-event ${mine ? "is-me" : ""}`}
                    >
                      <Avatar name={nameOf(event.who)} size={28} square />
                      <div className="workroom-event__body">
                        <header>
                          <strong>{nameOf(event.who)}</strong>
                          <time>{shortTime(event.at)}</time>
                        </header>
                        <p>{event.did}</p>
                        {moved && event.to_status && (
                          <span className="workroom-event__move">
                            {event.from_status ? STATUS_LABEL[event.from_status] : "创建"}
                            <ArrowRight size={11} />
                            {STATUS_LABEL[event.to_status]}
                            {event.from_holder !== event.to_holder && event.to_holder
                              ? ` · 交给 ${nameOf(event.to_holder)}`
                              : ""}
                          </span>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
              <form className="workroom-message" onSubmit={sendMessage}>
                <textarea
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder={
                    TERMINAL.has(selectedTask.status)
                      ? "任务已经结束，事件链保持只读"
                      : `补充要求给当前持棒人：${nameOf(selectedTask.holder)}`
                  }
                  rows={2}
                  maxLength={1200}
                  disabled={TERMINAL.has(selectedTask.status)}
                />
                <button
                  disabled={busy || !message.trim() || TERMINAL.has(selectedTask.status)}
                  aria-label="发送补充要求"
                >
                  <Send size={15} />
                </button>
                <small>内容将写入不可覆盖的任务事件链</small>
              </form>
            </>
          ) : (
            <div className="workroom-empty">
              <span>
                <MessageSquareText size={22} />
              </span>
              <strong>写下任务，选择派单方式</strong>
              <p>发单后，这里会出现 Agent 的接单、执行、交棒、受阻与交付回执。</p>
            </div>
          )}
        </Panel>

        <div className="workroom-side">
          <Panel icon={<Route size={15} />} kicker="FLOW" title="流转路线">
            {selectedTask ? (
              <div className="workroom-flow">
                <StageStepper task={selectedTask} />
                {selectedTask.pipeline ? (
                  <ol>
                    {selectedTask.pipeline.map((stage, index) => (
                      <li
                        key={`${stage.name}-${index}`}
                        className={
                          index < selectedTask.pipeline_stage
                            ? "is-done"
                            : index === selectedTask.pipeline_stage
                              ? "is-current"
                              : ""
                        }
                      >
                        <span>{index < selectedTask.pipeline_stage ? <CheckCircle2 size={14} /> : index + 1}</span>
                        <div>
                          <strong>{stage.name}</strong>
                          <small>{nameOf(stage.holder)}</small>
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="workroom-direct">
                    <Avatar name={nameOf(selectedTask.holder)} size={34} square />
                    <div>
                      <strong>{nameOf(selectedTask.holder)}</strong>
                      <span>单 Agent 直派 · 当前进度 {selectedTask.progress}%</span>
                    </div>
                  </div>
                )}
              </div>
            ) : mode === "pipeline" && selectedFlow ? (
              <ol className="workroom-draft-flow">
                {selectedFlow.stages.map((stage, index) => (
                  <li key={`${stage.name}-${index}`}>
                    <span>{index + 1}</span>
                    <div>
                      <strong>{stage.name}</strong>
                      <small>{nameOf(stage.holder)}</small>
                    </div>
                  </li>
                ))}
              </ol>
            ) : selectedMatch ? (
              <div className="workroom-direct workroom-direct--draft">
                <Avatar name={selectedMatch.display_name || selectedMatch.id} size={40} square />
                <div>
                  <strong>{selectedMatch.display_name || selectedMatch.id}</strong>
                  <span>{selectedMatch.reasons.join(" · ")}</span>
                </div>
              </div>
            ) : (
              <p className="muted">等待选择执行者</p>
            )}
          </Panel>

          <Panel icon={<Clock3 size={15} />} kicker="RECENT TASKS" title="最近任务">
            <div className="workroom-recent">
              {recentTasks.map((task) => (
                <button
                  key={task.id}
                  type="button"
                  className={selectedTaskId === task.id ? "is-active" : ""}
                  onClick={() => setSelectedTaskId(task.id)}
                >
                  <span>
                    <strong>{task.title}</strong>
                    <small>{nameOf(task.holder)} · {task.id.replace("task-", "")}</small>
                  </span>
                  <em className={`chip chip-status-${task.status}`}>
                    {STATUS_LABEL[task.status]}
                  </em>
                </button>
              ))}
              {recentTasks.length === 0 && <p className="muted">尚无任务</p>}
            </div>
          </Panel>
        </div>
      </div>

      {drawerOpen && selectedTask && (
        <TaskDrawer
          taskId={selectedTask.id}
          me={me}
          actors={actors}
          onClose={() => setDrawerOpen(false)}
          onChanged={() => void loadCore()}
        />
      )}
    </div>
  );
}
