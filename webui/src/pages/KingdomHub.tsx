import { useState } from "react";
import { KingdomPage } from "./KingdomPage";
import KingdomKnowledgePage from "./KingdomKnowledgePage";
import type { KingdomView } from "../lib/kingdom";
import { useVocab, type ThemeVocab } from "../theme";

type HubTab = KingdomView | "ops";

const TABS: { key: HubTab; label: (vocab: ThemeVocab) => string }[] = [
  { key: "overview", label: (vocab) => vocab.overviewLabel },
  { key: "ops", label: () => "运营效率 ↗" },
  { key: "agents", label: () => "智能体" },
  { key: "tasks", label: () => "任务中心" },
  { key: "skills", label: () => "技能中心" },
  { key: "knowledge", label: () => "知识库" },
  { key: "infrastructure", label: () => "基础设施" },
];

export default function KingdomHub({ onOpenOperations }: { onOpenOperations: () => void }) {
  const vocab = useVocab();
  const [tab, setTab] = useState<KingdomView>("overview");

  return (
    <div className="kingdom-hub">
      <div className="kingdom-hub-tabs">
        {TABS.map((item) => (
          <button
            key={item.key}
            className={tab === item.key ? "is-active" : ""}
            aria-label={item.key === "ops" ? "运营效率快捷入口" : undefined}
            title={item.key === "ops" ? "前往系统总览 → 运营效率" : undefined}
            onClick={() => item.key === "ops" ? onOpenOperations() : setTab(item.key)}
          >
            {item.label(vocab)}
          </button>
        ))}
      </div>
      {tab === "knowledge" ? (
        <KingdomKnowledgePage />
      ) : (
        <KingdomPage view={tab} />
      )}
    </div>
  );
}
