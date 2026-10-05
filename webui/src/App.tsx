import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  BookOpen,
  Bot,
  Castle,
  DatabaseZap,
  Gauge,
  GitBranch,
  Handshake,
  History,
  Home as HomeIcon,
  ListChecks,
  ListTodo,
  MessageSquareText,
  RadioTower,
  LogOut,
  RefreshCw,
  Server,
  Settings,
  Sparkles,
  SquareKanban,
} from "lucide-react";
import { api, ApiError } from "./api";
import type { Me } from "./types";
import { useTaskDeepLink } from "./deeplink";
import DeepTaskDrawer from "./components/DeepTaskDrawer";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Agenda from "./pages/Agenda";
import TaskFlowPage from "./pages/TaskFlowPage";
import Affairs from "./pages/Affairs";
import Overview from "./pages/Overview";
import Operations from "./pages/Operations";
import Board from "./pages/Board";
import Roster from "./pages/Roster";
import TaskCenter from "./pages/TaskCenter";
import Skills from "./pages/Skills";
import Knowledge from "./pages/Knowledge";
import DataCatalog from "./pages/DataCatalog";
import Infra from "./pages/Infra";
import Admin from "./pages/Admin";
import KingdomHub from "./pages/KingdomHub";
import Collab from "./pages/Collab";
import Workroom from "./pages/Workroom";
import Sessions from "./pages/Sessions";
import LiveSessions from "./pages/LiveSessions";
import { ThemeSwitcher, useVocab } from "./theme";
import { LanguageSwitcher, useI18n } from "./i18n";
import { requestDataRefresh } from "./lib/refresh";
import { demoMode } from "./demo";
import { navigationGroup, usePageNavigation, writeNavigation, type Page } from "./lib/navigation";
import "./pages/task-workspace.css";

type NavSection = "工作台" | "协作" | "系统";

type NavItem = {
  key: Page;
  label: string;
  icon: React.ReactNode;
  section: NavSection;
  adminOnly?: boolean;
  kingdomOnly?: boolean;
  teacherVisible?: boolean;
};

const NAV: NavItem[] = [
  { key: "home", label: "首页", icon: <HomeIcon size={16} />, section: "工作台", teacherVisible: true },
  { key: "affairs", label: "我的事务", icon: <ListTodo size={16} />, section: "工作台", teacherVisible: true },
  { key: "taskflow", label: "任务工作台", icon: <GitBranch size={16} />, section: "协作", teacherVisible: true },
  { key: "sessions", label: "会话中心", icon: <History size={16} />, section: "协作", teacherVisible: true },
  { key: "overview", label: "系统总览", icon: <Gauge size={16} />, section: "系统" },
  { key: "agents", label: "智能体", icon: <Bot size={16} />, section: "系统", teacherVisible: true },
  { key: "skills", label: "技能中心", icon: <Sparkles size={16} />, section: "系统" },
  { key: "catalog", label: "数据整理", icon: <DatabaseZap size={16} />, section: "系统" },
  { key: "knowledge", label: "知识库", icon: <BookOpen size={16} />, section: "系统" },
  { key: "infra", label: "基础设施", icon: <Server size={16} />, section: "系统" },
  { key: "kingdom", label: "中枢", icon: <Castle size={16} />, section: "系统", kingdomOnly: true },
  { key: "admin", label: "管理", icon: <Settings size={16} />, section: "系统", adminOnly: true },
];

const VIEWER_NAV = new Set<Page>([
  "home",
  "taskflow",
  "overview",
  "ops",
  "collab",
  "board",
  "agents",
  "live",
  "skills",
  "knowledge",
  "catalog",
  "infra",
]);

const PAGE_PURPOSE: Record<Page, string> = {
  home: "首页展示任务状态流转、派单分布与会话流转，帮助找到正在推进和需要处理的工作。",
  affairs: "我的事务管理个人待办、提案与需要处理的事项；日程视图保留快捷记录、父子事务和进度设置。",
  agenda: "日程按今日、待办和等待安排个人事务，支持快捷记录、父子事务及进度设置。",
  taskflow: "协作图展示所选任务的委派、依赖、接棒和退回；时间泳道与模块贡献联动具体执行证据。",
  board: "任务看板按状态排列任务，保留拖拽流转、新建任务和现在可做筛选。",
  taskcenter: "任务列表检索全部任务及归档记录，按状态和负责成员筛选，打开完整历史。",
  workroom: "协作空间保留任务派发、推荐成员、对话与成果回执，可进入同一任务的协作现场。",
  collab: "进度概览展示全局待接单、执行中、受阻和审批事项；按模型分组查看在手工作。",
  sessions: "历史会话检索已记录会话、摘要和关联任务，保留转为任务的入口。",
  live: "实时会话观察真实运行端点，并在既有权限内进行受控操作。",
  overview: "系统健康查看设备、模型、技能和服务状态；效率视图保留吞吐与用量统计。",
  ops: "运营效率汇总任务吞吐、模型产出与用量，用于复盘；完成数不等同成果验收数。",
  agents: "智能体登记设备与模型身份、能力和在线情况；会话同步服务单列展示。",
  skills: "技能中心查看技能来源、适用范围和可调用能力。",
  catalog: "数据整理查看与处理数据目录、来源和整理状态。",
  knowledge: "知识库查看可供任务引用的知识与来源。",
  infra: "基础设施查看设备与运行环境，定位可达性和资源问题。",
  kingdom: "中枢保留当前站点控制台入口；运营效率快捷入口统一前往系统总览。",
  admin: "管理维护账号、权限与系统配置。",
};

const MODE_GROUPS: { pages: Page[]; tabs: { key: Page; label: string; icon: React.ReactNode }[] }[] = [
  { pages: ["taskflow", "board", "taskcenter", "workroom", "collab"], tabs: [
    { key: "taskflow", label: "协作图", icon: <GitBranch size={15} /> },
    { key: "board", label: "任务看板", icon: <SquareKanban size={15} /> },
    { key: "taskcenter", label: "任务列表", icon: <ListChecks size={15} /> },
    { key: "workroom", label: "协作空间", icon: <MessageSquareText size={15} /> },
    { key: "collab", label: "进度概览", icon: <Handshake size={15} /> },
  ] },
  { pages: ["affairs", "agenda"], tabs: [
    { key: "affairs", label: "我的事务", icon: <ListTodo size={15} /> },
    { key: "agenda", label: "日程", icon: <HomeIcon size={15} /> },
  ] },
  { pages: ["sessions", "live"], tabs: [
    { key: "sessions", label: "历史会话", icon: <History size={15} /> },
    { key: "live", label: "实时会话", icon: <RadioTower size={15} /> },
  ] },
  { pages: ["overview", "ops"], tabs: [
    { key: "overview", label: "系统健康", icon: <Gauge size={15} /> },
    { key: "ops", label: "运营效率", icon: <BarChart3 size={15} /> },
  ] },
];

function groupNav(items: NavItem[]): { label: NavSection; items: NavItem[] }[] {
  const groups: { label: NavSection; items: NavItem[] }[] = [];
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.label === item.section) last.items.push(item);
    else groups.push({ label: item.section, items: [item] });
  }
  return groups;
}

export default function App() {
  const vocab = useVocab();
  const { t } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);
  const [page, setPage] = usePageNavigation();
  const [sessionFocus, setSessionFocus] = useState<number | null>(null);
  const [deepTaskId, setDeepTaskId] = useTaskDeepLink();
  const navigate = (next: Page) => {
    if (deepTaskId) setDeepTaskId(null);
    setPage(next);
  };

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api.get<Me>("/api/auth/me"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setMe(null);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void refreshMe();
  }, [refreshMe]);

  if (checking) return <div className="boot">{t("加载中…")}</div>;
  if (!me && !demoMode) return <Login onLogin={() => void refreshMe()} />;
  if (!me) return <div className="boot">{t("演示数据加载失败，请刷新页面。")}</div>;

  // The 中枢 hub embeds the site-specific console (admin-only API).
  const kingdomOn = Boolean(me.site_console) && me.role === "admin";
  const teacherMode = me.mode === "teacher";
  const viewerMode = demoMode || me.role === "viewer";
  const allowedPage = (key: Page) => {
    if (teacherMode && !NAV.find(item => item.key === navigationGroup(key))?.teacherVisible) return false;
    if (key === "admin") return me.role === "admin" && !demoMode;
    if (key === "kingdom") return kingdomOn && !demoMode;
    if (demoMode && key === "live") return false;
    return !viewerMode || VIEWER_NAV.has(key);
  };
  const visiblePage = allowedPage(page) ? page : page === "sessions" && allowedPage("live") ? "live" : "home";
  const currentModes = MODE_GROUPS.find(group => group.pages.includes(visiblePage));
  const navGroups = groupNav(
    NAV.filter(
      (item) =>
        (!item.adminOnly || me.role === "admin") &&
        (!item.kingdomOnly || kingdomOn) &&
        (!demoMode || item.key !== "live") &&
        (!teacherMode || item.teacherVisible) &&
        (allowedPage(item.key) || MODE_GROUPS.find(group => group.pages.includes(item.key))?.pages.some(allowedPage))
    )
  );

  return (
    <div className={`shell ${viewerMode ? "shell--viewer" : ""}`}>
      <aside className="sidebar">
        <div className="side-brand">
          <img src="./retinue-mark-v2.png" alt="Retinue" />
          <div>
            <strong>Retinue</strong>
            <small>{me.site_label || vocab.appTitle}</small>
          </div>
        </div>
        <nav className="side-nav">
          {navGroups.map((group) => (
            <div key={group.label} className="side-nav__section">
              <p className="side-nav__label">{t(group.label)}</p>
              {group.items.map((item) => {
                const label =
                  item.key === "affairs"
                    ? vocab.affairsLabel
                    : teacherMode && item.key === "agents"
                      ? t("AI 助理")
                      : t(item.label);
                return (
                  <button
                    key={item.key}
                    className={navigationGroup(visiblePage) === item.key ? "is-active" : ""}
                    aria-label={label}
                    onClick={() => navigate(allowedPage(item.key) ? item.key : MODE_GROUPS.find(group => group.pages.includes(item.key))?.pages.find(allowedPage) || "home")}
                  >
                    {item.icon}
                    <span>{label}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="side-user">
          <div className="side-user__name">
            <strong>{me.display_name || me.name}</strong>
            <small>{t(teacherMode ? "老师账号" : demoMode ? "演示观察席" : viewerMode ? "实盘观察席" : me.role === "admin" ? "管理员" : "成员")}</small>
          </div>
          <button
            title={t("刷新数据（协作总览每 15 秒更新，任务协作每 5 秒更新）")}
            aria-label={t("刷新数据")}
            onClick={() => {
              void refreshMe();
              requestDataRefresh();
            }}
          >
            <RefreshCw size={15} />
          </button>
          {!demoMode && (
          <button
            title={t("退出登录")}
            aria-label={t("退出登录")}
            onClick={() => {
              void api.post("/api/auth/logout").then(() => setMe(null));
            }}
          >
            <LogOut size={15} />
          </button>
          )}
        </div>
        <div className="side-theme">
          <LanguageSwitcher />
          <ThemeSwitcher />
        </div>
      </aside>
      <main className="main">
        {currentModes && <div className="workspace-modes" role="group" aria-label={t("工作台视图")}>
          {currentModes.tabs.filter(tab => allowedPage(tab.key)).map(tab => <button type="button" key={tab.key} aria-pressed={visiblePage === tab.key} onClick={() => navigate(tab.key)}>{tab.icon}{t(tab.label)}</button>)}
        </div>}
        <p className="workspace-purpose" aria-label={t("当前页面用途")}>{t(PAGE_PURPOSE[visiblePage])}</p>
        {demoMode && (
          <div className="real-data-banner">
            <strong>{t("公开演示 · 只读样本")}</strong>
            <span>{t("数据来自演示模板快照，无法改卡或登录写操作。")}</span>
          </div>
        )}
        {viewerMode && !demoMode && (
          <div className="real-data-banner">
            <strong>{vocab.liveBanner}</strong>
            <span>{t("这里展示的成员、任务、节点与流转均来自真实运行数据；观察席不能修改内容。")}</span>
          </div>
        )}
        {visiblePage === "home" && (
          <Home
            me={me}
            onNavigate={(p) => navigate(p as Page)}
            onOpenTask={(id) => setDeepTaskId(id)}
            onOpenSession={(id) => {
              setSessionFocus(id);
              navigate("sessions");
            }}
          />
        )}
        {visiblePage === "affairs" && !viewerMode && (
          <Affairs onOpenTask={(id) => setDeepTaskId(id)} />
        )}
        {visiblePage === "agenda" && !viewerMode && <Agenda me={me} onNavigate={p => navigate(p as Page)} onOpenTask={setDeepTaskId} onOpenSession={id => { setSessionFocus(id); navigate("sessions"); }} />}
        {visiblePage === "taskflow" && <TaskFlowPage me={me} onOpenTask={setDeepTaskId} />}
        {visiblePage === "workroom" && <Workroom me={me} />}
        {visiblePage === "sessions" && <Sessions me={me} focusSessionId={sessionFocus} />}
        {visiblePage === "live" && <LiveSessions me={me} />}
        {visiblePage === "overview" && <Overview />}
        {visiblePage === "ops" && <Operations kingdomOn={kingdomOn} />}
        {visiblePage === "collab" && <Collab me={me} />}
        {visiblePage === "board" && <Board me={me} onOpenTask={(id) => setDeepTaskId(id)} />}
        {visiblePage === "agents" && <Roster me={me} onNavigate={(target) => navigate(target)} />}
        {visiblePage === "taskcenter" && (
          <TaskCenter me={me} onOpenTask={(id) => setDeepTaskId(id)} />
        )}
        {visiblePage === "skills" && <Skills me={me} />}
        {visiblePage === "knowledge" && <Knowledge />}
        {visiblePage === "catalog" && <DataCatalog />}
        {visiblePage === "infra" && <Infra />}
        {visiblePage === "kingdom" && kingdomOn && <KingdomHub onOpenOperations={() => navigate("ops")} />}
        {visiblePage === "admin" && me.role === "admin" && <Admin />}
      </main>
      {deepTaskId && (
        <DeepTaskDrawer
          taskId={deepTaskId}
          me={me}
          onClose={() => setDeepTaskId(null)}
          onOpenTask={(id) => setDeepTaskId(id)}
          onOpenFlow={(id) => { setDeepTaskId(null); setPage("taskflow"); writeNavigation({ task: id }); }}
        />
      )}
    </div>
  );
}
