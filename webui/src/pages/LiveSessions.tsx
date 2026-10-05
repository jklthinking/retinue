import { t, useI18n, getLanguage } from "../i18n";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CircleStop,
  Copy,
  Eye,
  RadioTower,
  RefreshCw,
  Send,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import { api, ApiError } from "../api";
import { Ambient, DataState, PageHeader, Panel } from "../components/ui";
import type { Me } from "../types";
import "./live-sessions.css";

type ControlVerb = "tell" | "peek" | "interrupt";
type ControlState = "queued" | "leased" | "delivered" | "failed" | "expired";

interface LiveObservation {
  id: number;
  node_id: string;
  backend: string;
  endpoint_id: string;
  generation: string;
  runtime: string;
  input_mode: string;
  actor_id: string | null;
  task_id: string | null;
  explicit_binding: boolean;
  occupant_verified: boolean;
  control_eligible: boolean;
  binding_status: "bound" | "unbound" | "invalid" | "stale";
  state: string;
  command: string;
  cwd_hint: string;
  display_location: string;
  bound_live_session_id: string | null;
  observed_at: string;
  disappeared_at: string | null;
}

interface ControlEnvelope {
  id: string;
  live_session_id: string;
  node_id: string;
  verb: ControlVerb;
  status: ControlState;
  attempts: number;
  result: { output?: string; detail?: string };
  expires_at: string;
  completed_at: string | null;
  created_at: string;
}

const STATUS_LABEL: Record<ControlState, string> = {
  queued: "等待节点领取",
  leased: "节点执行中",
  delivered: "已送达",
  failed: "执行失败",
  expired: "已过期",
};

const BINDING_LABEL: Record<LiveObservation["binding_status"], string> = {
  bound: "已验证绑定",
  unbound: "未绑定",
  invalid: "绑定无效",
  stale: "已离线",
};

function makeIdempotencyKey(): string {
  const value = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  return `web-${value.replace(/[^A-Za-z0-9._:-]/g, "")}`;
}

function timeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return t("时间未知");
  return new Intl.DateTimeFormat(getLanguage(), {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export default function LiveSessions({ me }: { me: Me }) {
  useI18n();
  const [rows, setRows] = useState<LiveObservation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [peekLines, setPeekLines] = useState(20);
  const [control, setControl] = useState<ControlEnvelope | null>(null);
  const [interruptArmed, setInterruptArmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<ControlVerb | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selected = useMemo(
    () => rows.find((row) => row.id === selectedId) || rows[0] || null,
    [rows, selectedId]
  );
  const readonly = Boolean(me.readonly) || me.role === "viewer";
  const canControl = Boolean(
    selected?.control_eligible && selected.bound_live_session_id && !readonly
  );
  const canTell = canControl && selected?.input_mode === "codex-prompt";

  const load = useCallback(async () => {
    try {
      const next = await api.get<LiveObservation[]>("/api/live-sessions");
      setRows(next);
      setSelectedId((current) =>
        current && next.some((row) => row.id === current)
          ? current
          : (next[0]?.id ?? null)
      );
      setError("");
    } catch (err) {
      setError(errorMessage(err, t("实时会话加载失败")));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!control || !["queued", "leased"].includes(control.status)) return;
    const timer = window.setTimeout(() => {
      void api
        .get<ControlEnvelope>(`/api/live-sessions/control/${control.id}`)
        .then(setControl)
        .catch((err) => setError(errorMessage(err, t("控制状态刷新失败"))));
    }, 1_200);
    return () => clearTimeout(timer);
  }, [control]);

  async function submitControl(verb: ControlVerb) {
    if (!selected?.bound_live_session_id || !canControl) return;
    if (verb === "tell" && !message.trim()) {
      setError(t("请输入要发送的消息。"));
      return;
    }
    setBusy(verb);
    setNotice("");
    try {
      const body: Record<string, unknown> = {
        verb,
        idempotency_key: makeIdempotencyKey(),
      };
      if (verb === "tell") body.message = message.trim();
      if (verb === "peek") body.lines = peekLines;
      const envelope = await api.post<ControlEnvelope>(
        `/api/live-sessions/${selected.bound_live_session_id}/control`,
        body
      );
      setControl(envelope);
      if (verb === "tell") setMessage("");
      setNotice(t("{v0}已进入安全队列。", { v0: verb === "tell" ? "消息" : verb === "peek" ? "查看请求" : "软中断" }));
      setError("");
    } catch (err) {
      setError(errorMessage(err, t("无法创建控制信封")));
    } finally {
      setBusy(null);
    }
  }

  async function copyLocation() {
    if (!selected) return;
    const value = `${selected.node_id}:${selected.display_location}`;
    try {
      await navigator.clipboard.writeText(value);
      setNotice(t("已复制定位信息：{v0}", { v0: value }));
    } catch {
      setNotice(t("定位信息：{v0}", { v0: value }));
    }
  }

  const boundCount = rows.filter((row) => row.binding_status === "bound").length;
  const controllableCount = rows.filter((row) => row.control_eligible).length;

  return (
    <div className="rt-page live-page">
      <Ambient />
      <PageHeader
        kicker="LIVE SESSIONS · VERIFIED ENDPOINTS"
        title={t("实时会话")}
        subtitle={t("查看各节点上的 Agent，并通过 Hub 安全地传话、查看尾部输出或软中断。")}
        tools={
          <button className="rt-button rt-button--soft" onClick={() => void load()}>
            <RefreshCw size={15} />{t("刷新")} </button>
        }
      />

      <div className="live-safety-note">
        <ShieldCheck size={18} />
        <div>
          <strong>{t("节点观察不等于控制权限")}</strong>
          <span>{t("只有显式绑定、进程占用者和端点代际全部验证通过后，控制入口才会启用。")}</span>
        </div>
        <div className="live-counts">
          <span><b>{rows.length}</b> {t("个端点")}</span>
          <span><b>{boundCount}</b> {t("个绑定")}</span>
          <span><b>{controllableCount}</b> {t("个可控")}</span>
        </div>
      </div>

      {error && <div className="error live-error" role="alert">{error}</div>}
      {notice && <div className="success live-notice">{notice}</div>}

      <div className="live-layout">
        <Panel
          icon={<RadioTower size={17} />}
          kicker="DISCOVERY"
          title={t("节点端点")}
          tools={<span className="live-count-chip">{t("30 秒刷新")}</span>}
          className="live-list-panel"
        >
          {loading && rows.length === 0 ? (
            <DataState loading />
          ) : rows.length === 0 ? (
            <DataState empty={t("尚未收到节点会话探测。")} />
          ) : (
            <div className="live-list">
              {rows.map((row) => (
                <button
                  type="button"
                  key={row.id}
                  className={`live-row ${selected?.id === row.id ? "is-active" : ""}`}
                  onClick={() => {
                    setSelectedId(row.id);
                    setControl(null);
                    setInterruptArmed(false);
                    setNotice("");
                  }}
                >
                  <span className={`live-dot live-dot--${row.state}`} />
                  <span className="live-row__body">
                    <strong>{row.actor_id || t("未绑定 Agent")}</strong>
                    <span>{row.runtime || row.command || t("未知运行时")} · {row.node_id}</span>
                    <em className={`live-binding live-binding--${row.binding_status}`}>
                      {t(BINDING_LABEL[row.binding_status])}
                    </em>
                  </span>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          icon={<TerminalSquare size={17} />}
          kicker="CONTROL ENVELOPE"
          title={selected?.bound_live_session_id || t("选择一个端点")}
          tools={selected && (
            <button className="rt-button rt-button--soft" onClick={() => void copyLocation()}>
              <Copy size={14} />{t("复制定位")} </button>
          )}
          className="live-control-panel"
        >
          {!selected ? (
            <DataState empty={t("选择左侧端点后查看状态。")} />
          ) : (
            <div className="live-control">
              <dl className="live-meta">
                <div><dt>{t("节点 / 位置")}</dt><dd>{selected.node_id}:{selected.display_location}</dd></div>
                <div><dt>{t("运行时")}</dt><dd>{selected.runtime || "—"}</dd></div>
                <div><dt>{t("任务")}</dt><dd>{selected.task_id || t("未关联")}</dd></div>
                <div><dt>{t("状态")}</dt><dd>{selected.state}</dd></div>
                <div><dt>{t("端点代际")}</dt><dd>{selected.generation.slice(0, 12)}</dd></div>
                <div><dt>{t("最近观察")}</dt><dd>{timeLabel(selected.observed_at)}</dd></div>
              </dl>

              <section className="live-action-block">
                <div className="live-action-head">
                  <div>
                    <strong>{t("直接传话")}</strong>
                    <span>{t("适合短消息；需要交付与验收的工作请建立任务卡。")}</span>
                  </div>
                  <Send size={17} />
                </div>
                <textarea
                  value={message}
                  maxLength={4000}
                  rows={4}
                  placeholder={canTell ? t("输入要发送给这个 Agent 的消息…") : t("该端点没有可验证的提示词输入契约")}
                  disabled={!canTell || busy !== null}
                  onChange={(event) => setMessage(event.target.value)}
                />
                <div className="live-action-foot">
                  <span>{message.length}/4000</span>
                  <button
                    className="rt-button rt-button--primary"
                    disabled={!canTell || !message.trim() || busy !== null}
                    onClick={() => void submitControl("tell")}
                  >
                    <Send size={14} />{busy === "tell" ? t("排队中…") : t("发送一次")}
                  </button>
                </div>
              </section>

              <div className="live-secondary-actions">
                <section className="live-action-block">
                  <div className="live-action-head">
                    <div><strong>{t("查看尾部输出")}</strong><span>{t("节点侧裁剪并脱敏，最多 100 行。")}</span></div>
                    <Eye size={17} />
                  </div>
                  <label className="live-lines"> {t("行数")} <input
                      type="number"
                      min={1}
                      max={100}
                      value={peekLines}
                      disabled={!canControl || busy !== null}
                      onChange={(event) => setPeekLines(Math.min(100, Math.max(1, Number(event.target.value) || 1)))}
                    />
                  </label>
                  <button
                    className="rt-button rt-button--soft"
                    disabled={!canControl || busy !== null}
                    onClick={() => void submitControl("peek")}
                  >
                    <Eye size={14} />{busy === "peek" ? t("排队中…") : t("安全查看")}
                  </button>
                </section>

                <section className="live-action-block live-action-block--danger">
                  <div className="live-action-head">
                    <div><strong>{t("软中断")}</strong><span>{t("仅发送一次 Ctrl-C，不终止进程或窗格。")}</span></div>
                    <CircleStop size={17} />
                  </div>
                  <button
                    className={`rt-button live-interrupt ${interruptArmed ? "is-armed" : ""}`}
                    disabled={!canControl || busy !== null}
                    onClick={() => {
                      if (!interruptArmed) {
                        setInterruptArmed(true);
                        setNotice(t("软中断已准备；请再次点击确认。不会终止进程或窗格。"));
                        return;
                      }
                      setInterruptArmed(false);
                      void submitControl("interrupt");
                    }}
                  >
                    <CircleStop size={14} />
                    {busy === "interrupt" ? t("排队中…") : interruptArmed ? t("确认软中断") : t("准备软中断")}
                  </button>
                </section>
              </div>

              {readonly && <p className="live-policy">{t("当前账号是只读观察席，控制入口已禁用。")}</p>}
              {!readonly && !selected.control_eligible && (
                <p className="live-policy">{t("该端点尚未通过显式绑定、占用者或代际验证。")}</p>
              )}

              {control && (
                <section className={`live-envelope live-envelope--${control.status}`}>
                  <header>
                    <div>
                      <span>{t("控制信封")} {control.id}</span>
                      <strong>{t(STATUS_LABEL[control.status])}</strong>
                    </div>
                    <em>{control.verb} {t("· 尝试")} {control.attempts} {t("次")}</em>
                  </header>
                  {control.result?.detail && <p>{control.result.detail}</p>}
                  {control.result?.output && <pre>{control.result.output}</pre>}
                </section>
              )}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
