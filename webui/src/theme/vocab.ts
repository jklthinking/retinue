/**
 * Theme vocabulary: every piece of flavour wording in the panel comes from a
 * `ThemeVocab` record instead of a string literal inside a component, so the
 * interface voice is a configuration choice rather than a hard-coded one.
 *
 * The default theme is neutral. The court-style wording that older builds
 * shipped is kept as the `court` preset and stays fully selectable.
 */

export interface ThemeVocab {
  /** Sidebar brand line and login card title, after the "Retinue" name. */
  appTitle: string;
  /** Read-only banner shown to viewer accounts. */
  liveBanner: string;
  /** Hub overview tab and overview page title. */
  overviewLabel: string;
  /** Short name for the coordination hub (default dept value, boundary text). */
  centralHub: string;
  /** Eyebrow line above the overview page title. */
  overviewEyebrow: string;
  /** Display label for the throne node. */
  nodeThrone: string;
  /** Display label for the castle node. */
  nodeCastle: string;
  /** Node filter that spans every node. */
  allNodes: string;
  /** Loading line while the hub connects. */
  connectingNodes: string;
  /** Meta line under the deduplicated-skill metric. */
  skillRegistryMeta: string;
  /** Agents column label on the dispatch map. */
  membersAgents: string;
  /** Roster panel title. */
  membersRoster: string;
  /** Workroom intent prompt. */
  tellMembers: string;
  /** Home metric subtitle for in-flight work. */
  membersExecuting: string;
  /** Sessions privacy note body. */
  membersAuthNote: string;
  /** Data-catalog loading line. */
  dataCatalogLoading: string;
  /** Data-catalog storage panel title. */
  dataLayerTitle: string;
  /** Data-catalog boundary note. */
  dataBoundary: string;
  /** Overview empty-state hint for nodes. */
  nodeSyncHint: string;
  /** Conflicts dialog hint. */
  conflictsHint: string;
  /** Admin onboarding read failure. */
  adminReadError: string;
  /** Admin page subtitle. */
  adminBoundary: string;
  /** Admin onboarding wizard heading. */
  onboardTitle: string;
  /** Admin onboarding wizard description. */
  onboardDesc: string;
  /** Admin onboarding loading line. */
  adminLoading: string;
  /** Admin onboarding context-pack label. */
  contextPackLabel: string;
  /** Session-to-task dept placeholder. */
  deptPlaceholder: string;
}

/** Neutral default: plain operational wording, no flavour terms. */
export const neutralVocab: ThemeVocab = {
  appTitle: "任务台",
  liveBanner: "全局实盘 · 只读观察",
  overviewLabel: "全局总览",
  centralHub: "任务中枢",
  overviewEyebrow: "RETINUE · 任务中枢",
  nodeThrone: "Throne 主节点",
  nodeCastle: "Castle 节点",
  allNodes: "全部节点",
  connectingNodes: "正在连接各节点",
  skillRegistryMeta: "全局能力注册表",
  membersAgents: "成员 AGENTS",
  membersRoster: "成员阵容",
  tellMembers: "告诉成员你想完成什么",
  membersExecuting: "成员正在执行",
  membersAuthNote:
    "成员只接收你授权的层级：默认仅索引；摘要和最近消息需在同步端主动开启。",
  dataCatalogLoading: "正在读取成员数据目录…",
  dataLayerTitle: "全局数据层",
  dataBoundary:
    "任务中枢只负责结构化运行数据；Obsidian 仍保存纪要正文。二者通过 source、refs、双链和更新时间互相指向，不复制全文。",
  nodeSyncHint:
    "尚无节点心跳。在各节点运行 probe 命令或配置多节点同步即可接入。",
  conflictsHint:
    "冲突文件在主节点本地 Vault 处理，Syncthing 会把结果同步到四方节点；所有删除均先移入 ~/.hermes/trash/vault-conflicts。",
  adminReadError: "无法读取全局现状",
  adminBoundary: "登记成员、发放接入凭证，并维护全局的可见边界。",
  onboardTitle: "让新 BOT 先认识任务中枢，再开始工作",
  onboardDesc:
    "向导会登记执行者、账号和一次性令牌，并生成可随时刷新的全局上下文包。上下文包只读、脱敏，不会复制会话正文或私有记忆。",
  adminLoading: "正在读取全局现状…",
  contextPackLabel: "全局上下文包（可复制）",
  deptPlaceholder: "如：任务中枢、研发、教学",
};

/** Court preset: the original flavour wording, kept as an option. */
export const courtVocab: ThemeVocab = {
  appTitle: "众卿任务台",
  liveBanner: "组织实盘 · 只读观察",
  overviewLabel: "组织总览",
  centralHub: "组织中枢",
  overviewEyebrow: "JKL 神思记 · 组织中枢",
  nodeThrone: "王座 Throne",
  nodeCastle: "城堡 Castle",
  allNodes: "全组织",
  connectingNodes: "正在连接组织节点",
  skillRegistryMeta: "全组织能力注册表",
  membersAgents: "众卿 AGENTS",
  membersRoster: "众卿阵容",
  tellMembers: "告诉众卿你想完成什么",
  membersExecuting: "众卿正在执行",
  membersAuthNote:
    "众卿只接收你授权的层级：默认仅索引；摘要和最近消息需在同步端主动开启。",
  dataCatalogLoading: "正在读取众卿数据目录…",
  dataLayerTitle: "组织数据层",
  dataBoundary:
    "组织中枢只负责结构化运行数据；Obsidian 仍保存纪要正文。二者通过 source、refs、双链和更新时间互相指向，不复制全文。",
  nodeSyncHint:
    "尚无节点心跳。在各节点运行 probe 命令或配置组织同步即可接入。",
  conflictsHint:
    "冲突文件在王座本地 Vault 处理，Syncthing 会把结果同步到四方节点；所有删除均先移入 ~/.hermes/trash/vault-conflicts。",
  adminReadError: "无法读取组织现状",
  adminBoundary: "登记成员、发放接入凭证，并维护组织的可见边界。",
  onboardTitle: "让新 BOT 先认识组织，再开始工作",
  onboardDesc:
    "向导会登记执行者、账号和一次性令牌，并生成可随时刷新的组织上下文包。上下文包只读、脱敏，不会复制会话正文或私有记忆。",
  adminLoading: "正在读取组织现状…",
  contextPackLabel: "组织上下文包（可复制）",
  deptPlaceholder: "如：组织中枢、研发、教学",
};

export type ThemeId = "neutral" | "court";

export const THEME_PRESETS: Record<ThemeId, { label: string; vocab: ThemeVocab }> = {
  neutral: { label: "中性（默认）", vocab: neutralVocab },
  court: { label: "宫廷", vocab: courtVocab },
};

export const DEFAULT_THEME: ThemeId = "neutral";

export function isThemeId(value: string | null): value is ThemeId {
  return value !== null && value in THEME_PRESETS;
}
