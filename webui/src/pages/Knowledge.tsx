import { t, useI18n, getLanguage } from "../i18n";
import { useEffect, useState } from "react";
import { BookOpen } from "lucide-react";
import { api, readErrorMessage } from "../api";
import type { KnowledgeInfo } from "../types";
import { fmtBytes } from "../types";
import { Ambient, DataState, PageHeader, Panel } from "../components/ui";

const KIND_LABEL: Record<string, string> = {
  obsidian: "Obsidian",
  wiki: "Wiki",
  corpus: "语料库",
  dataset: "数据集",
  notes: "笔记",
  memory: "记忆",
  archive: "归档",
};

export default function Knowledge() {
  useI18n();
  const [sources, setSources] = useState<KnowledgeInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setSources(await api.get<KnowledgeInfo[]>("/api/knowledge"));
        setError(null);
      } catch (reason) {
        setError(readErrorMessage(reason));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const totalDocs = sources.reduce((sum, s) => sum + s.docs, 0);

  return (
    <div className="rt-page knowledge-page">
      <Ambient />
      <PageHeader
        kicker="KNOWLEDGE SOURCES"
        title={t("知识库")}
        subtitle={loading ? t("正在读取知识源目录") : error ? t("知识源目录读取失败") : t("{v0} 个来源 · 共 {v1} 篇文档", { v0: sources.length, v1: totalDocs.toLocaleString(getLanguage()) })}
      />

      {loading && <DataState loading />}
      {error && <DataState error={error} />}

      {!loading && !error && <Panel icon={<BookOpen size={15} />} kicker="SOURCES" title={t("知识源")}>
      <div className="rt-know-grid">
        {sources.map((source) => (
          <article key={source.id} className="rt-know-card">
            <header>
              <strong>{source.name}</strong>
              <span className="chip chip-dept">{t(KIND_LABEL[source.kind] ?? source.kind)}</span>
            </header>
            <dl>
              <div>
                <dt>{t("文档数")}</dt>
                <dd>{source.docs.toLocaleString(getLanguage())}</dd>
              </div>
              {source.size_bytes > 0 && (
                <div>
                  <dt>{t("体量")}</dt>
                  <dd>{fmtBytes(source.size_bytes)}</dd>
                </div>
              )}
              {source.location && (
                <div>
                  <dt>{t("位置")}</dt>
                  <dd>{source.location}</dd>
                </div>
              )}
              <div>
                <dt>{t("更新")}</dt>
                <dd>
                  {source.updated_at
                    ? new Date(source.updated_at).toLocaleDateString(getLanguage())
                    : "—"}
                </dd>
              </div>
            </dl>
            {source.notes && <p className="muted" style={{ marginBottom: 0 }}>{source.notes}</p>}
          </article>
        ))}
        {sources.length === 0 && <DataState empty={t("确实尚无知识源。")} />}
      </div>
      </Panel>
      }
    </div>
  );
}
