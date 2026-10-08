import { useEffect, useRef, useState } from "react";
import { t, useI18n } from "../i18n";
import { api } from "../api";
import { collaborationTime } from "../lib/collaboration";
import { startVisiblePolling } from "../lib/refresh";
import "./task-conversations.css";

type Identity = { id: string | null; name: string | null; model: string | null; model_source: string };
export interface Conversation {
  id: number; state: string; runtime?: string; node?: string; linked_at: string;
  capture_mode?: string; receiver?: Identity; summary: string | null;
  retained_messages?: number; source_message_count?: number;
  messages: { role: string; text: string; at: string | null; sender: Identity; receiver: Identity; identity_source: string }[];
}
const STATES: Record<string, string> = {
  revoked: "沟通记录已撤回", metadata_only: "来源仅提供元数据，原文不可见。",
  source_changed: "来源身份已变化，原文不可见。", outside_window: "原文已不在同步窗口，或来源已变化。",
};
export default function TaskConversations({ taskId }: { taskId: string }) {
  useI18n();
  const [items, setItems] = useState<Conversation[]>([]);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const seq = useRef(0);
  useEffect(() => {
    setItems([]); setLoaded(false); setError(false);
    const load = async () => {
      const ticket = ++seq.current;
      try {
        const payload = await api.get<{ items: Conversation[] }>(`/api/tasks/${encodeURIComponent(taskId)}/conversations`);
        if (ticket !== seq.current) return;
        setItems(payload.items); setError(false); setLoaded(true);
      } catch {
        if (ticket !== seq.current) return;
        setItems([]); setError(true); setLoaded(true);
      }
    };
    const stop = startVisiblePolling(load, 15_000);
    return () => { seq.current += 1; stop(); };
  }, [taskId]);
  const name = (identity?: Identity) => identity?.name || t("未知");
  return <section className="task-conversations" aria-label={t("沟通记录")}>
    <p className="task-conversations__hint">{t("只展示已关联的可见消息；原文保留 Markdown，不执行其中的代码或链接。")}</p>
    {error ? <p role="alert">{t("无法确认当前访问权限，已隐藏沟通原文。")}</p>
      : !loaded ? <p>{t("正在读取沟通记录…")}</p>
      : !items.length ? <p>{t("暂无可见沟通记录。")}</p> : null}
    {items.map((item) => <article key={item.id} className="task-conversations__source">
      <header><strong>{t(item.capture_mode === "live" ? "同步记录（关联者标注）" : "历史导入")}</strong>
        <span>{item.runtime || t("未知")} · {collaborationTime(item.linked_at)}</span>
      </header>
      {item.receiver && <p className="task-conversations__hint">{name(item.receiver)} · {item.receiver.model || t("模型未知")}
        {" · "}{t(item.receiver.model_source === "registry" ? "登记信息（未验证）" : "来源未知")}</p>}
      {item.state !== "available" ? <p>{t(STATES[item.state] || "原文暂不可见。")}</p> : <>
        {item.summary && <details><summary>{t("来源摘要（同步端提供）")}</summary><pre>{item.summary}</pre></details>}
        {item.source_message_count != null && item.retained_messages != null && item.source_message_count > item.retained_messages &&
          <p className="task-conversations__hint">{t("来源存在未保留的消息，本页仅显示已同步范围。")}</p>}
        <ol className="task-conversations__messages">{item.messages.map((message, index) => <li key={index}>
          <header><strong>{name(message.sender)} → {name(message.receiver)}</strong><time>{collaborationTime(message.at)}</time></header>
          <small>{t(message.identity_source === "operator_annotation" ? "关联者标注（未验证）" : "会话元数据")}</small>
          <pre>{message.text}</pre>
          {message.text.length >= 4000 && <small>{t("同步消息达到长度上限，可能存在未保留的后文。")}</small>}
        </li>)}</ol>
      </>}
    </article>)}
  </section>;
}
