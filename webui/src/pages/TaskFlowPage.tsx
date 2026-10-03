import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, GitBranch, Search } from "lucide-react";
import type { Me } from "../types";
import { STATUS_LABEL } from "../types";
import { useSummary } from "../lib/summary";
import { NAVIGATION_EVENT, readFlowTask, writeNavigation } from "../lib/navigation";
import { TASKS_CHANGED_EVENT } from "../components/DeepTaskDrawer";
import TaskCollaboration from "../components/TaskCollaboration";
import { Ambient, DataState, PageHeader } from "../components/ui";
import "./task-workspace.css";

export default function TaskFlowPage({ me, onOpenTask }: { me: Me; onOpenTask: (id: string) => void }) {
  const { summary, tasks, loaded, error, reload } = useSummary({ includeArchived: true });
  const [selected, setSelected] = useState<string | null>(() => readFlowTask());
  const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get("q") || "");
  const [status, setStatus] = useState(() => new URLSearchParams(window.location.search).get("status") || "");
  useEffect(() => {
    const sync = () => {
      const params = new URLSearchParams(window.location.search);
      setSelected(readFlowTask());
      setQuery(params.get("q") || "");
      setStatus(params.get("status") || "");
    };
    window.addEventListener("popstate", sync);
    window.addEventListener(NAVIGATION_EVENT, sync);
    window.addEventListener(TASKS_CHANGED_EVENT, reload);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(NAVIGATION_EVENT, sync);
      window.removeEventListener(TASKS_CHANGED_EVENT, reload);
    };
  }, [reload]);
  const visible = useMemo(() => {
    const term = query.trim().toLowerCase();
    return tasks.filter(task => (!status || task.status === status) && (!term || `${task.id} ${task.title} ${task.dept || ""}`.toLowerCase().includes(term)));
  }, [tasks, query, status]);
  const currentId = selected || visible[0]?.id || null;
  const current = tasks.find(task => task.id === currentId);
  const choose = (id: string) => {
    setSelected(id);
    writeNavigation({ task: id });
  };

  return <div className="rt-page task-workspace">
    <Ambient />
    <PageHeader kicker="TASK WORKSPACE · COLLABORATION" title="每个任务的协作流转"
      subtitle="选择任务，查看谁委派谁、各设备与模型的执行时间，以及每个功能模块的工作和成果。"
      tools={currentId && <button type="button" onClick={() => onOpenTask(currentId)}><ArrowUpRight size={15} /> 打开任务卡</button>} />
    {error && <DataState error={error} stale={loaded} onRetry={reload} />}
    <div className="task-workspace__layout">
      <aside className="task-workspace__selector" aria-label="选择协作任务">
        <header><GitBranch size={17} /><strong>任务 {visible.length} / {tasks.length}</strong></header>
        <label className="task-workspace__search"><Search size={15} /><input aria-label="搜索协作任务" placeholder="搜索任务 / 编号 / 条线" value={query} onChange={event => { setQuery(event.target.value); writeNavigation({ q: event.target.value || null }, true); }} /></label>
        <select aria-label="按任务状态筛选" value={status} onChange={event => { setStatus(event.target.value); writeNavigation({ status: event.target.value || null }, true); }}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <div className="task-workspace__tasks">
          {visible.map(task => <button type="button" key={task.id} aria-pressed={task.id === currentId} onClick={() => choose(task.id)}>
            <span><em data-status={task.status}>{STATUS_LABEL[task.status]}</em><small>{task.id.replace("task-", "")}</small></span>
            <strong>{task.title}</strong>
            <small>{task.dept || "条线未登记"}{task.archived ? " · 已归档" : ""}</small>
          </button>)}
          {loaded && visible.length === 0 && <p>没有符合筛选的任务。清除搜索或选择其他状态。</p>}
          {!loaded && !error && <p>正在加载任务…</p>}
        </div>
      </aside>
      <section className="task-workspace__scene" aria-label="所选任务的可视化协作">
        {currentId ? <>
          <div className="task-workspace__selected"><strong>{current?.title || currentId}</strong><span>{current ? STATUS_LABEL[current.status] : "正在取得任务"}</span></div>
          <TaskCollaboration key={currentId} taskId={currentId} actors={summary?.actors || []} me={me} layout="wide" onChanged={reload} onOpenTask={choose} />
        </> : loaded && <div className="task-workspace__empty"><GitBranch size={28} /><h2>选择一项任务查看协作</h2><p>每张任务卡都有自己的流转视图；没有执行回执时仍显示已有任务记录。</p></div>}
      </section>
    </div>
  </div>;
}
