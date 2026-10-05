import { t, useI18n } from "../i18n";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import { api, ApiError } from "../api";
import type { ActorInfo, Me, SkillInfo } from "../types";
import { Avatar } from "../avatar";
import { Ambient, PageHeader, Panel } from "../components/ui";

export default function Skills({ me }: { me: Me }) {
  useI18n();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [actors, setActors] = useState<ActorInfo[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  const load = (bypass = false) => {
    void api
      .getCached<SkillInfo[]>("/api/skills", bypass)
      .then((rows) => {
        setSkills(rows);
        setError("");
      })
      .catch((reason) => {
        if (reason instanceof ApiError) setError(reason.message);
        else setError(t("技能列表暂时拉不下来，已保留上次数据（如有）"));
      });
    void api.getCached<ActorInfo[]>("/api/actors", bypass).then(setActors).catch(() => {});
  };

  useEffect(() => {
    load(false);
    const onManual = () => load(true);
    window.addEventListener("retinue:data-refresh", onManual);
    return () => window.removeEventListener("retinue:data-refresh", onManual);
  }, []);

  const categories = useMemo(() => {
    const map = new Map<string, number>();
    for (const skill of skills) {
      const key = skill.category || "";
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [skills]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return skills.filter(
      (s) =>
        (category === null || (s.category || "") === category) &&
        (!q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q))
    );
  }, [skills, category, query]);

  const nameOf = (id: string) => actors.find((a) => a.id === id)?.display_name || id;

  function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    void api
      .post("/api/skills", {
        name: form.get("name"),
        category: form.get("category") || "",
        description: form.get("description") || "",
      })
      .then(() => load(true))
      .catch((err) => setError(err instanceof ApiError ? err.message : t("创建失败")));
    event.currentTarget.reset();
  }

  return (
    <div className="rt-page skills-page">
      <Ambient />
      <PageHeader
        kicker="CAPABILITY REGISTRY"
        title={t("技能中心")}
        subtitle={t("{v0} 项能力 · {v1} 个分类,按归属与来源管理", { v0: skills.length, v1: categories.length })}
      />

      <Panel icon={<Sparkles size={15} />} kicker="REGISTRY" title={t("能力登记表")}>
      <div className="skill-cats">
        <button className={category === null ? "is-active" : ""} onClick={() => setCategory(null)}> {t("全部")} {skills.length}
        </button>
        {categories.map(([cat, n]) => (
          <button
            key={cat}
            className={category === cat ? "is-active" : ""}
            onClick={() => setCategory(cat)}
          >
            {cat || t("未分类")} {n}
          </button>
        ))}
      </div>

      <div className="tc-filters">
        <input
          placeholder={t("搜索技能名称或描述…")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="muted">{filtered.length} {t("项")}</span>
      </div>

      {me.role === "admin" && (
        <form className="admin-form" onSubmit={onCreate}>
          <input name="name" placeholder={t("新技能名称")} required />
          <input name="category" placeholder={t("分类")} />
          <input name="description" placeholder={t("描述")} />
          <button className="primary">{t("登记技能")}</button>
        </form>
      )}
      {error && <p className="error">{error}</p>}

      <div className="skill-grid">
        {filtered.map((skill) => (
          <article key={skill.id} className={`skill-card ${skill.enabled ? "" : "is-disabled"}`}>
            <header>
              <strong>{skill.name}</strong>
              <span className="chip chip-dept">{skill.category || t("未分类")}</span>
              {!skill.enabled && <span className="chip chip-high">{t("停用")}</span>}
            </header>
            <p>{skill.description || "—"}</p>
            {skill.owners.length > 0 && (
              <footer>
                {skill.owners.slice(0, 5).map((owner) => (
                  <span key={owner} className="holder-line" title={nameOf(owner)}>
                    <Avatar name={nameOf(owner)} size={18} square />
                  </span>
                ))}
                <span className="muted skill-owner-names">
                  {skill.owners.slice(0, 3).map(nameOf).join("、")}
                  {skill.owners.length > 3 ? t(" 等 {v0} 位", { v0: skill.owners.length }) : ""}
                </span>
              </footer>
            )}
          </article>
        ))}
        {filtered.length === 0 && <p className="muted">{t("没有匹配的技能")}</p>}
      </div>
      </Panel>
    </div>
  );
}
