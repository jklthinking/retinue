import { useI18n, t } from "../i18n";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ActorInfo, PipelineStage, PipelineTemplateInfo, Priority } from "../types";
import { GATE_LABEL, PRIORITY_LABEL } from "../types";

interface Props {
  actors: ActorInfo[];
  onClose: () => void;
  onCreated: () => void;
}

const PRIORITIES: Priority[] = ["urgent", "high", "medium", "low", "none"];
type Mode = "assign" | "open" | "pipeline";

export default function NewTaskDialog({ actors, onClose, onCreated }: Props) {
  useI18n();
  const enabled = actors.filter((a) => !a.disabled);
  const [title, setTitle] = useState("");
  const [holder, setHolder] = useState(enabled[0]?.id ?? "");
  const [mode, setMode] = useState<Mode>("assign");
  const [templates, setTemplates] = useState<PipelineTemplateInfo[]>([]);
  const [stages, setStages] = useState<PipelineStage[]>([]);
  const [dept, setDept] = useState("");
  const [priority, setPriority] = useState<Priority>("medium");
  const [dueAt, setDueAt] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api
      .get<PipelineTemplateInfo[]>("/api/pipeline-templates")
      .then((rows) => {
        setTemplates(rows);
        if (rows.length > 0) setStages(rows[0].stages.map((s) => ({ ...s })));
      })
      .catch(() => setTemplates([]));
  }, []);

  // Keyboard: Escape closes the dialog without submitting.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post("/api/tasks", {
        title,
        holder: mode === "assign" ? holder : null,
        open_dispatch: mode === "open",
        pipeline: mode === "pipeline" ? stages : null,
        dept: dept.trim() || null,
        priority,
        due_at: dueAt || null,
        acceptance: acceptance
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("创建失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="drawer-mask" onClick={onClose}>
      <form className="dialog" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>{t("新建任务卡")}</h2>
        <label>
          {t("标题")} <input value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus />
        </label>
        <div className="dispatch-mode dispatch-mode--three">
          <button
            type="button"
            className={mode === "assign" ? "is-active" : ""}
            onClick={() => setMode("assign")}
          >
            {t("指派执行者")} </button>
          <button
            type="button"
            className={mode === "open" ? "is-active" : ""}
            onClick={() => setMode("open")}
          >
            {t("挂单大厅")} </button>
          <button
            type="button"
            className={mode === "pipeline" ? "is-active" : ""}
            onClick={() => setMode("pipeline")}
            disabled={templates.length === 0}
            title={templates.length === 0 ? t("尚无流程模板(管理员可经 API 登记)") : ""}
          >
            {t("流程模板")} </button>
        </div>
        {mode === "assign" && (
          <label>
            {t("持棒人(执行者)")} <select value={holder} onChange={(e) => setHolder(e.target.value)} required>
              {enabled.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name || a.id}
                  {a.kind === "agent" ? t("(智能体)") : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {mode === "pipeline" && (
          <>
            <label>
              {t("流程模板")} <select
                onChange={(e) => {
                  const found = templates.find((t) => String(t.id) === e.target.value);
                  if (found) setStages(found.stages.map((s) => ({ ...s })));
                }}
              >
                {templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}({template.stages.length} {t("节点)")} </option>
                ))}
              </select>
            </label>
            <div className="stage-editor">
              {stages.map((stage, index) => (
                <div key={index} className="stage-editor__row">
                  <span className="stage-editor__name">
                    {index + 1}. {stage.name}
                    <em>{t(GATE_LABEL[stage.gate])}</em>
                  </span>
                  <select
                    value={stage.holder}
                    onChange={(e) =>
                      setStages((prev) =>
                        prev.map((s, i) => (i === index ? { ...s, holder: e.target.value } : s))
                      )
                    }
                  >
                    {enabled.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.display_name || a.id}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </>
        )}
        <div className="dialog-row">
          <label>
            {t("部门/条线")} <input value={dept} onChange={(e) => setDept(e.target.value)} placeholder={t("可选")} />
          </label>
          <label>
            {t("优先级")} <select value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {t(PRIORITY_LABEL[p])}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("截止日")} <input
              type="date"
              value={dueAt}
              onChange={(e) => setDueAt(e.target.value)}
            />
          </label>
        </div>
        <label>
          {t("验收标准(每行一条)")} <textarea value={acceptance} onChange={(e) => setAcceptance(e.target.value)} rows={3} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            {t("取消")} </button>
          <button
            type="submit"
            className="primary"
            disabled={busy || (mode === "assign" && !holder) || (mode === "pipeline" && stages.length < 2)}
          >
            {busy ? t("创建中…") : mode === "open" ? t("发布到大厅") : mode === "pipeline" ? t("启动流程") : t("创建")}
          </button>
        </div>
      </form>
    </div>
  );
}
