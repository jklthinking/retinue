import { t, useI18n, getLanguage } from "../i18n";
import React from "react";
import {
  AlertTriangle, ArrowLeft, Check, FileWarning, Pencil, RefreshCw,
  ShieldCheck, Trash2, X,
} from "lucide-react";
import { kingdomApi, type VaultConflictDetail, type VaultConflictItem } from "@/lib/kingdom";
import { useVocab } from "@/theme";
import "./kingdom.css";

const fmtBytes = (value: number) => {
  let size = value; const units = ["B", "KB", "MB"]; let i = 0;
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i += 1; }
  return `${size.toFixed(i ? 1 : 0)} ${units[i]}`;
};
const fmtDate = (value?: string | null) => value
  ? new Date(value).toLocaleString(getLanguage(), { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
  : t("未知");

function diffTone(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "kc-diff__line kc-diff__line--file";
  if (line.startsWith("@@")) return "kc-diff__line kc-diff__line--hunk";
  if (line.startsWith("+")) return "kc-diff__line kc-diff__line--add";
  if (line.startsWith("-")) return "kc-diff__line kc-diff__line--del";
  return "kc-diff__line";
}

export function ConflictsDialog({ open, onClose, onResolved }: {
  open: boolean;
  onClose: () => void;
  onResolved?: () => void;
}) {
  useI18n();
  const vocab = useVocab();
  const [items, setItems] = React.useState<VaultConflictItem[] | null>(null);
  const [error, setError] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<VaultConflictDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const [target, setTarget] = React.useState("");
  const [notice, setNotice] = React.useState("");

  const loadList = React.useCallback(async () => {
    try {
      const response = await kingdomApi.getConflicts();
      setItems(response.items);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("无法加载冲突列表"));
    }
  }, []);

  React.useEffect(() => {
    if (!open) return;
    setSelected(null); setDetail(null); setEditing(false); setNotice("");
    void loadList();
  }, [open, loadList]);

  const openDetail = React.useCallback(async (path: string) => {
    setSelected(path); setDetail(null); setEditing(false); setNotice(""); setDetailLoading(true);
    try {
      const response = await kingdomApi.getConflictDetail(path);
      setDetail(response);
      setTarget(response.original?.path ?? response.candidates[0] ?? "");
      setDraft(response.original?.content ?? response.conflict.content ?? "");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("无法加载冲突详情"));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const resolve = React.useCallback(async (action: "keep_current" | "keep_conflict" | "save_edit") => {
    if (!selected || busy) return;
    const label = action === "keep_current" ? t("保留当前版本并移除该冲突文件（冲突文件会移入回收目录）")
      : action === "keep_conflict" ? t("用冲突版本覆盖「{v0}」（原内容自动备份）", { v0: target })
      : t("将编辑后的内容保存到「{v0}」并归档该冲突文件", { v0: target });
    if (!window.confirm(t("确认{v0}？", { v0: label }))) return;
    setBusy(true);
    try {
      await kingdomApi.resolveConflict({
        path: selected,
        action,
        target: action === "keep_current" ? undefined : target,
        content: action === "save_edit" ? draft : undefined,
        confirm: true,
      });
      setNotice(t("已处理并写入审计记录；计数将在下次快照刷新后更新。"));
      setSelected(null); setDetail(null);
      await loadList();
      onResolved?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("处理失败"));
    } finally {
      setBusy(false);
    }
  }, [selected, busy, target, draft, loadList, onResolved]);

  if (!open) return null;
  return <div className="kc-modal-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <div className="kc-modal kc-modal--wide" role="dialog" aria-label={t("同步冲突处理")}>
      <header>
        <div><span>VAULT SYNC CONFLICTS</span><h2>{selected ? t("冲突对比与处理") : t("同步冲突")}</h2></div>
        <div className="kc-row-actions">
          {selected && <button type="button" title={t("返回列表")} onClick={() => { setSelected(null); setDetail(null); }}><ArrowLeft size={18} /></button>}
          <button type="button" title={t("关闭")} onClick={onClose}><X size={18} /></button>
        </div>
      </header>

      {notice && <div className="kc-notice kc-notice--success"><Check size={16} /><span>{notice}</span><button onClick={() => setNotice("")}><X size={15} /></button></div>}
      {error && <div className="kc-notice kc-notice--error"><AlertTriangle size={16} /><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}

      {!selected && <>
        {items === null && !error && <div className="kc-conflict-loading"><RefreshCw size={16} className="kc-spin" />{t("正在扫描 Vault…")}</div>}
        {items !== null && items.length === 0 && <div className="kc-conflict-loading"><ShieldCheck size={16} />{t("当前没有同步冲突文件 🎉")}</div>}
        {items !== null && items.length > 0 && <div className="kc-conflict-list">
          {items.map((item) => <button type="button" className="kc-conflict-row" key={item.path} onClick={() => void openDetail(item.path)}>
            <FileWarning size={17} />
            <div>
              <strong>{item.original_name}</strong>
              <span>{item.path}</span>
              <span>{t("设备")} {item.device ?? t("未知")} {t("· 冲突于")} {item.conflict_at ?? t("未知")} · {fmtBytes(item.size)} {t("· 修改")} {fmtDate(item.mtime)}</span>
            </div>
            {item.archived ? <em className="kc-conflict-tag">{t("已归档区")}</em> : <em className="kc-conflict-tag kc-conflict-tag--live">{t("现场")}</em>}
          </button>)}
        </div>}
        <p className="kc-muted kc-conflict-hint">{vocab.conflictsHint}</p>
      </>}

      {selected && <>
        {detailLoading && <div className="kc-conflict-loading"><RefreshCw size={16} className="kc-spin" />{t("正在读取双方内容…")}</div>}
        {detail && <div className="kc-conflict-detail">
          <div className="kc-conflict-meta">
            <code>{detail.conflict.path}</code>
            {detail.candidates.length > 1 && <label>{t("对应原文件")} <select value={target} onChange={(event) => setTarget(event.target.value)}>
                {detail.candidates.map((candidate) => <option key={candidate} value={candidate}>{candidate}</option>)}
              </select>
            </label>}
            {detail.candidates.length <= 1 && <span className="kc-muted">{t("原文件：")}{target || t("未找到（只能查看或移除冲突副本）")}</span>}
          </div>

          {!editing && <>
            {detail.diff.length > 0
              ? <pre className="kc-diff">{detail.diff.map((line, index) => <span key={index} className={diffTone(line)}>{line || " "}</span>)}</pre>
              : <pre className="kc-diff">{(detail.conflict.content ?? t("（二进制或过大文件，仅支持整体处理）")).slice(0, 20000)}</pre>}
          </>}
          {editing && <textarea className="kc-conflict-editor" value={draft} onChange={(event) => setDraft(event.target.value)} spellCheck={false} />}

          <footer className="kc-conflict-actions">
            <button type="button" className="kc-button kc-button--ghost" disabled={busy} onClick={() => void resolve("keep_current")}><Trash2 size={15} />{t("保留当前，移除冲突副本")}</button>
            <button type="button" className="kc-button kc-button--soft" disabled={busy || !target} onClick={() => void resolve("keep_conflict")}><Check size={15} />{t("采用冲突版本")}</button>
            {!editing && <button type="button" className="kc-button kc-button--soft" disabled={!detail.conflict.editable} onClick={() => setEditing(true)}><Pencil size={15} />{t("手动合并编辑")}</button>}
            {editing && <>
              <button type="button" className="kc-button kc-button--ghost" onClick={() => setEditing(false)}>{t("放弃编辑")}</button>
              <button type="button" className="kc-button kc-button--primary" disabled={busy || !target} onClick={() => void resolve("save_edit")}><Check size={15} />{t("保存合并结果")}</button>
            </>}
          </footer>
        </div>}
      </>}
    </div>
  </div>;
}

export default ConflictsDialog;
