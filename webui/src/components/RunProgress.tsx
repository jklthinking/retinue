import type { CollaborationRun, CompletedClaim } from "../lib/collaboration";
import { collaborationTime, needsProgressUpdate } from "../lib/collaboration";
import { useI18n } from "../i18n";

export function ClaimList({ items }: { items: CompletedClaim[] }) {
  const { t } = useI18n();
  return <ul className="task-collaboration__claims">
    {items.map((item, index) => <li key={index}>
      <span>{item.summary}</span>
      {item.refs.length > 0 && <small>{t("成果引用：")}{item.refs.join(" · ")}</small>}
      {item.revision && <small>{t("版本 / 修订：")}{item.revision}</small>}
    </li>)}
  </ul>;
}

/** An executor's claims are kept separate from task acceptance evidence. */
export default function RunProgress({ run, nameOf }: { run: CollaborationRun; nameOf: (id: string) => string }) {
  const { t } = useI18n();
  const report = run.progress_report;
  const stale = needsProgressUpdate(run);
  return <section className="task-collaboration__progress" aria-label={t("结构化进展")}>
    <header>
      <h5>{t("结构化进展")}</h5>
      <span className="task-collaboration__tag">{t("执行者声明 · 待验收")}</span>
    </header>
    <p className="task-collaboration__hint">{t("进度为执行者上报，验收结果另行确认。")}</p>
    <div className="task-collaboration__progress-time" data-stale={stale}>
      {stale && <strong>{t("进展待更新 · ")}</strong>}
      <span>{t("进展上报于")} {collaborationTime(run.progress_reported_at)}</span>
      {stale && <small>{t("已超过 {count} 分钟没有新进展；心跳不代表工作推进。", { count: Math.round((run.freshness?.stale_after_seconds ?? 0) / 60) })}</small>}
    </div>
    {report ? <>
      <div className="task-collaboration__progress-columns">
        <div><h5>{t("已完成（执行者声明）")}</h5>
          {report.completed.length ? <ClaimList items={report.completed} /> : <p className="task-collaboration__hint">{t("尚未声明完成项。")}</p>}
        </div>
        <div><h5>{t("剩余事项")}</h5>
          {report.remaining.length ? <ul>{report.remaining.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="task-collaboration__hint">{t("未上报剩余事项；不代表已验收。")}</p>}
        </div>
      </div>
      <div className="task-collaboration__next">
        <strong>{t("下一棒：")}{report.next_owner ? nameOf(report.next_owner) : t("责任人待补充")}</strong>
        <p>{report.next_action || t("下一步动作待补充")}</p>
      </div>
    </> : <p className="task-collaboration__hint">{t("结构化进展尚未上报，当前仅能查看已有回执。")}</p>}
  </section>;
}
