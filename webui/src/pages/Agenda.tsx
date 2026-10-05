import { useI18n, t, getLanguage } from "../i18n";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, CalendarDays, Inbox, Plus } from "lucide-react";
import { readErrorMessage } from "../api";
import {
  completeTodoItem,
  createTodoItem,
  fetchTodoHome,
  fetchTodos,
  updateTodoItem,
} from "../lib/todos";
import type { Me, TodoHome, TodoItem } from "../types";
import { localTodayISO } from "../types";
import { Ambient, DataState } from "../components/ui";
import { useVocab } from "../theme";
import { BOARD_REFRESH_MS, DATA_REFRESH_EVENT } from "../lib/refresh";

type LaneTab = "today" | "tomorrow" | "anytime";

const PROGRESS_PRESETS = [0, 50, 100] as const;

type ChildDraft = {
  title: string;
  due_at: string;
  event_on: string;
};

function emptyChildDraft(): ChildDraft {
  return { title: "", due_at: localTodayISO(), event_on: "" };
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return t("夜深了");
  if (hour < 11) return t("早上好");
  if (hour < 13) return t("中午好");
  if (hour < 18) return t("下午好");
  return t("晚上好");
}

function shiftLocalISO(days: number): string {
  const now = new Date();
  now.setDate(now.getDate() + days);
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function isActive(item: TodoItem): boolean {
  return item.status === "open" || item.status === "snoozed";
}

function emptyHome(): TodoHome {
  return {
    pending_proposals: [],
    due_today: [],
    overdue: [],
    waiting_on_others: [],
    events_tomorrow: [],
    anytime: [],
  };
}

function formatDay(iso: string | null, today: string, tomorrow: string): string | null {
  if (!iso) return null;
  if (iso === today) return t("今天");
  if (iso === tomorrow) return t("明天");
  const parts = iso.split("-");
  if (parts.length !== 3) return iso;
  return t("{v0}月{v1}日", { v0: Number(parts[1]), v1: Number(parts[2]) });
}

function dateMeta(item: TodoItem, today: string, tomorrow: string): string {
  const bits: string[] = [];
  if (item.due_at) {
    const due = formatDay(item.due_at, today, tomorrow);
    bits.push(item.due_at < today ? t("逾期 {v0}", { v0: due }) : t("{v0}截止", { v0: due }));
  }
  if (item.event_on) {
    bits.push(t("{v0}有这场", { v0: formatDay(item.event_on, today, tomorrow) }));
  }
  if (item.status === "snoozed") bits.push(t("已延期过"));
  return bits.join(" · ");
}

function progressTone(percent: number): "is-green" | "is-amber" | "is-red" | "" {
  if (percent >= 100) return "is-green";
  if (percent >= 50) return "is-amber";
  if (percent > 0) return "";
  return "";
}

function parentReadyToClose(item: TodoItem): boolean {
  if (item.parent_id) return false;
  if (item.status !== "open" && item.status !== "snoozed") return false;
  if (item.ready_to_close) return true;
  const live = (item.children ?? []).filter((child) => child.status !== "cancelled");
  return live.length > 0 && live.every((child) => child.status === "done");
}

function AgendaRow({
  item,
  today,
  tomorrow,
  parentTitle,
  nested,
  pending,
  onComplete,
  onProgress,
}: {
  item: TodoItem;
  today: string;
  tomorrow: string;
  parentTitle?: string;
  nested?: boolean;
  pending: boolean;
  onComplete: (id: string) => void;
  onProgress: (id: string, percent: number) => void;
}) {
  useI18n();
  const done = item.status === "done";
  const meta = dateMeta(item, today, tomorrow);
  const readyToClose = parentReadyToClose(item);
  return (
    <article className={`rt-agenda-row ${nested ? "is-nested" : ""} ${done ? "is-done" : ""}`}>
      <label className="rt-agenda-check">
        <input
          type="checkbox"
          checked={done}
          disabled={pending || done}
          aria-label={t("完成：{v0}", { v0: item.title })}
          onChange={(event) => {
            if (event.target.checked && !done) onComplete(item.id);
          }}
        />
      </label>
      <div className="rt-agenda-row__body">
        <div className="rt-agenda-row__top">
          <strong>{item.title}</strong>
          <div className="rt-agenda-presets" role="group" aria-label={t("{v0} 进度", { v0: item.title })}>
            {PROGRESS_PRESETS.map((value) => (
              <button
                key={value}
                type="button"
                className={item.progress === value ? "is-active" : ""}
                disabled={pending || done}
                aria-label={t("进度 {v0}%：{v1}", { v0: value, v1: item.title })}
                onClick={() => onProgress(item.id, value)}
              >
                {value}
              </button>
            ))}
          </div>
        </div>
        <p className="rt-agenda-row__meta">
          {parentTitle ? `${parentTitle} · ` : ""}
          {meta || t("随时")}
          {` · ${item.progress}%`}
        </p>
        <div className="rt-progress rt-agenda-bar" aria-hidden="true">
          <span className={progressTone(item.progress)} style={{ width: `${Math.min(100, item.progress)}%` }} />
        </div>
        {readyToClose && (
          <div className="rt-agenda-close-hint" role="status">
            <span>{t("子项都做完了，这场是否也结束了？")}</span>
            <button
              type="button"
              className="rt-button rt-button--gold"
              disabled={pending}
              aria-label={t("结束这场：{v0}", { v0: item.title })}
              onClick={() => onComplete(item.id)}
            >
              {t("结束这场")} </button>
          </div>
        )}
      </div>
    </article>
  );
}

function CaptureDialog({
  roots,
  onClose,
  onCreated,
}: {
  roots: TodoItem[];
  onClose: () => void;
  onCreated: () => void;
}) {
  useI18n();
  const [title, setTitle] = useState("");
  const [eventOn, setEventOn] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [parentId, setParentId] = useState("");
  const [childDrafts, setChildDrafts] = useState<ChildDraft[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const attaching = Boolean(parentId);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function patchChild(index: number, patch: Partial<ChildDraft>) {
    setChildDrafts((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const cleaned = title.trim();
    if (!cleaned) {
      setError(t("标题必填"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const parent = await createTodoItem({
        title: cleaned,
        event_on: eventOn || null,
        due_at: dueAt || null,
        parent_id: parentId || null,
      });
      if (!attaching) {
        for (const row of childDrafts) {
          const childTitle = row.title.trim();
          if (!childTitle) continue;
          await createTodoItem({
            title: childTitle,
            parent_id: parent.id,
            due_at: row.due_at || null,
            event_on: row.event_on || null,
          });
        }
      }
      onCreated();
    } catch (reason) {
      setError(readErrorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="drawer-mask" onClick={onClose}>
      <form className="dialog rt-agenda-dialog" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>{t("记下一条")}</h2>
        <label>
          {t("标题")} <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            autoFocus
            maxLength={256}
            aria-label={t("标题")}
          />
        </label>
        <div className="dialog-row">
          <label>
            {t("哪天有这场")} <input type="date" value={eventOn} onChange={(e) => setEventOn(e.target.value)} aria-label={t("哪天有这场")} />
          </label>
          <label>
            {t("必须哪天结束")} <input type="date" value={dueAt} onChange={(e) => setDueAt(e.target.value)} aria-label={t("必须哪天结束")} />
          </label>
        </div>
        <label>
          {t("挂到已有的场")} <select
            value={parentId}
            aria-label={t("挂到已有的场")}
            onChange={(e) => {
              setParentId(e.target.value);
              if (e.target.value) setChildDrafts([]);
            }}
          >
            <option value="">{t("无（新的一条）")}</option>
            {roots.map((root) => (
              <option key={root.id} value={root.id}>
                {root.title}
              </option>
            ))}
          </select>
        </label>
        {!attaching && (
          <div className="rt-agenda-children">
            <span>{t("子项（可选）")}</span>
            {childDrafts.map((row, index) => (
              <div key={index} className="rt-agenda-child-row">
                <input
                  value={row.title}
                  maxLength={256}
                  aria-label={t("子项 {v0}", { v0: index + 1 })}
                  placeholder={t("为这场要做完的活")}
                  onChange={(e) => patchChild(index, { title: e.target.value })}
                />
                <div className="dialog-row">
                  <label>
                    {t("必须哪天结束")} <input
                      type="date"
                      value={row.due_at}
                      aria-label={t("子项 {v0} 必须哪天结束", { v0: index + 1 })}
                      onChange={(e) => patchChild(index, { due_at: e.target.value })}
                    />
                  </label>
                  <label>
                    {t("哪天有这场")} <input
                      type="date"
                      value={row.event_on}
                      aria-label={t("子项 {v0} 哪天有这场", { v0: index + 1 })}
                      onChange={(e) => patchChild(index, { event_on: e.target.value })}
                    />
                  </label>
                </div>
              </div>
            ))}
            {childDrafts.length < 8 && (
              <button
                type="button"
                className="rt-button rt-button--soft"
                onClick={() => setChildDrafts((rows) => [...rows, emptyChildDraft()])}
              >
                {t("添加子项")} </button>
            )}
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="rt-button rt-button--soft" onClick={onClose} disabled={busy}>
            {t("取消")} </button>
          <button type="submit" className="rt-button rt-button--gold" disabled={busy || !title.trim()}>
            {busy ? t("记下…") : t("记下")}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function Agenda({
  me,
  onNavigate,
}: {
  me: Me;
  onNavigate: (page: string) => void;
  onOpenTask: (taskId: string) => void;
  onOpenSession: (sessionId: number) => void;
}) {
  useI18n();
  const vocab = useVocab();
  const viewer = me.role === "viewer";
  const [home, setHome] = useState<TodoHome>(emptyHome);
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [loading, setLoading] = useState(!viewer);
  const [loaded, setLoaded] = useState(viewer);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<LaneTab>("today");
  const [capture, setCapture] = useState(false);
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const seq = useRef(0);
  const today = localTodayISO();
  const tomorrow = shiftLocalISO(1);

  const load = useCallback(() => {
    if (viewer) return;
    const ticket = ++seq.current;
    void Promise.all([fetchTodoHome(), fetchTodos()])
      .then(([nextHome, nextTodos]) => {
        if (ticket !== seq.current) return;
        setHome({
          pending_proposals: nextHome.pending_proposals ?? [],
          due_today: nextHome.due_today ?? [],
          overdue: nextHome.overdue ?? [],
          waiting_on_others: nextHome.waiting_on_others ?? [],
          events_tomorrow: nextHome.events_tomorrow ?? [],
          anytime: nextHome.anytime ?? [],
        });
        setTodos(nextTodos);
        setError(null);
        setLoaded(true);
      })
      .catch((reason) => {
        if (ticket !== seq.current) return;
        setError(readErrorMessage(reason));
      })
      .finally(() => {
        if (ticket === seq.current) setLoading(false);
      });
  }, [viewer]);

  useEffect(() => {
    load();
    const timer = setInterval(load, BOARD_REFRESH_MS);
    const onManual = () => load();
    window.addEventListener(DATA_REFRESH_EVENT, onManual);
    return () => {
      clearInterval(timer);
      window.removeEventListener(DATA_REFRESH_EVENT, onManual);
    };
  }, [load]);

  const byId = useMemo(() => {
    const map = new Map<string, TodoItem>();
    const add = (item: TodoItem) => {
      map.set(item.id, item);
      for (const child of item.children ?? []) add(child);
    };
    for (const item of todos) add(item);
    for (const lane of [home.due_today, home.overdue, home.events_tomorrow, home.anytime]) {
      for (const item of lane) add(item);
    }
    return map;
  }, [home, todos]);

  const todayEvents = useMemo(() => {
    const taken = new Set([...home.due_today, ...home.overdue].map((item) => item.id));
    return todos.filter(
      (item) => isActive(item) && item.event_on === today && !taken.has(item.id) && !item.parent_id
    );
  }, [home.due_today, home.overdue, todos, today]);

  const dueTomorrow = useMemo(() => {
    const nested = new Set(
      home.events_tomorrow.flatMap((item) => [item.id, ...(item.children ?? []).map((child) => child.id)])
    );
    return todos.filter((item) => isActive(item) && item.due_at === tomorrow && !nested.has(item.id));
  }, [home.events_tomorrow, todos, tomorrow]);

  const roots = useMemo(
    () => todos.filter((item) => isActive(item) && !item.parent_id),
    [todos]
  );

  const run = useCallback(
    (key: string, action: () => Promise<unknown>) => {
      setPendingKey(key);
      void action()
        .then(() => load())
        .catch((reason) => setError(readErrorMessage(reason)))
        .finally(() => setPendingKey(null));
    },
    [load]
  );

  const dateLabel = new Date().toLocaleDateString(getLanguage(), {
    month: "long",
    day: "numeric",
    weekday: "long",
  });
  const mustFinish = home.due_today.length;
  const overdueCount = home.overdue.length;
  const proposalCount = home.pending_proposals.length;
  const tomorrowCount = home.events_tomorrow.length;
  const pending = pendingKey !== null;
  const who = me.display_name || me.name;
  const row = (item: TodoItem, extra: { nested?: boolean; parentTitle?: string } = {}) => (
    <AgendaRow
      key={item.id}
      item={item}
      today={today}
      tomorrow={tomorrow}
      parentTitle={extra.parentTitle}
      nested={extra.nested}
      pending={pending}
      onComplete={(id) => run(`complete:${id}`, () => completeTodoItem(id))}
      onProgress={(id, percent) =>
        run(`progress:${id}:${percent}`, () => updateTodoItem(id, { progress: percent }))
      }
    />
  );

  return (
    <div className="rt-page rt-agenda">
      <Ambient />
      <header className="rt-agenda-hero">
        <h1>
          {t("{greeting}，{who}", { greeting: greeting(), who })}
        </h1>
        <p>
          {dateLabel}
          {!viewer && (
            <>
              {" · "}
              {t("今天")} {mustFinish} {t("件必须结束")} {overdueCount > 0 ? t(" · 逾期 {v0}", { v0: overdueCount }) : ""}
              {t(" · 明天 {v0} 场", { v0: tomorrowCount })}
            </>
          )}
        </p>
        <div className="rt-agenda-hero__bar" aria-hidden="true" />
      </header>

      {viewer && (
        <p className="rt-agenda-private">{t("观察席看不到私人日程。舰队数字在系统总览。")}</p>
      )}

      {loading && !loaded && <DataState loading />}
      {error && <DataState error={error} stale={loaded} onRetry={load} />}

      {!viewer && loaded && (
        <div className="rt-agenda-body">
          <div className="rt-agenda-main">
            <div className="rt-agenda-toolbar">
              <div className="rt-segmented" role="tablist" aria-label={t("日程分区")}>
                {(
                  [
                    ["today", t("今天")],
                    ["tomorrow", t("明天")],
                    ["anytime", t("随时")],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={tab === key}
                    className={tab === key ? "is-active" : ""}
                    onClick={() => setTab(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="rt-button rt-button--gold rt-agenda-cta"
                onClick={() => setCapture(true)}
              >
                <Plus size={14} /> {t("记下一条")} </button>
            </div>

            <div role="tabpanel" className="rt-agenda-panel">
              {tab === "today" && (
                <>
                  {overdueCount > 0 && (
                    <section className="rt-agenda-card rt-agenda-card--overdue" aria-label={t("逾期")}>
                      <header>
                        <h2>{t("逾期，先结掉")}</h2>
                        <em>{overdueCount}</em>
                      </header>
                      {home.overdue.map((item) =>
                        row(item, { parentTitle: item.parent_id ? byId.get(item.parent_id)?.title : undefined })
                      )}
                    </section>
                  )}
                  <section className="rt-agenda-card" aria-label={t("必须今天结束")}>
                    <header>
                      <h2>{t("必须今天结束")}</h2>
                      <em>{mustFinish}</em>
                    </header>
                    {mustFinish === 0 ? (
                      <p className="rt-agenda-empty">{t("今天没有必须结束的事项。")}</p>
                    ) : (
                      home.due_today.map((item) =>
                        row(item, { parentTitle: item.parent_id ? byId.get(item.parent_id)?.title : undefined })
                      )
                    )}
                  </section>
                  {todayEvents.length > 0 && (
                    <section className="rt-agenda-card rt-agenda-card--event" aria-label={t("今天有这场")}>
                      <header>
                        <h2>{t("今天有这场")}</h2>
                        <em>{todayEvents.length}</em>
                      </header>
                      {todayEvents.map((item) => row(item))}
                    </section>
                  )}
                </>
              )}
              {tab === "tomorrow" && (
                <>
                  <section className="rt-agenda-card rt-agenda-card--event" aria-label={t("明天有这场")}>
                    <header>
                      <h2>{t("明天有这场")}</h2>
                      <em>{tomorrowCount}</em>
                    </header>
                    {tomorrowCount === 0 ? (
                      <p className="rt-agenda-empty">{t("明天还没有记下的场。")}</p>
                    ) : (
                      home.events_tomorrow.map((item) => (
                        <div key={item.id} className="rt-agenda-stack">
                          {row(item)}
                          {(item.children ?? [])
                            .filter((child) => child.status !== "cancelled")
                            .map((child) => row(child, { nested: true }))}
                        </div>
                      ))
                    )}
                  </section>
                  {dueTomorrow.length > 0 && (
                    <section className="rt-agenda-card" aria-label={t("明天截止")}>
                      <header>
                        <h2>{t("明天截止")}</h2>
                        <em>{dueTomorrow.length}</em>
                      </header>
                      {dueTomorrow.map((item) =>
                        row(item, { parentTitle: item.parent_id ? byId.get(item.parent_id)?.title : undefined })
                      )}
                    </section>
                  )}
                </>
              )}
              {tab === "anytime" && (
                <section className="rt-agenda-card" aria-label={t("随时")}>
                  <header>
                    <h2>{t("随时 / 不赶")}</h2>
                    <em>{home.anytime.length}</em>
                  </header>
                  {home.anytime.length === 0 ? (
                    <p className="rt-agenda-empty">{t("没有不赶的事项。记下一条，不必填日期。")}</p>
                  ) : (
                    home.anytime.map((item) =>
                      row(item, { parentTitle: item.parent_id ? byId.get(item.parent_id)?.title : undefined })
                    )
                  )}
                </section>
              )}
            </div>
          </div>

          <aside className="rt-agenda-rail" aria-label={t("日程摘要")}>
            <div className="rt-agenda-stat rt-agenda-stat--gold">
              <span>{t("今日必须结束")}</span>
              <strong>{mustFinish}</strong>
            </div>
            <div className="rt-agenda-stat rt-agenda-stat--red">
              <span>{t("逾期")}</span>
              <strong>{overdueCount}</strong>
            </div>
            <div className="rt-agenda-stat rt-agenda-stat--ink">
              <span>{t("待确认提案")}</span>
              <strong>{proposalCount}</strong>
            </div>
            <button type="button" className="rt-agenda-affairs" onClick={() => onNavigate("affairs")}>
              <Inbox size={14} />
              {vocab.affairsLabel}
              <ArrowRight size={14} />
            </button>
            <p className="rt-agenda-rail-note">
              <CalendarDays size={13} /> {t("提案、逾期处理仍在")}{vocab.affairsLabel}{t("。舰队数字在系统总览。")} </p>
          </aside>
        </div>
      )}

      {capture && (
        <CaptureDialog
          roots={roots}
          onClose={() => setCapture(false)}
          onCreated={() => {
            setCapture(false);
            load();
          }}
        />
      )}
    </div>
  );
}
