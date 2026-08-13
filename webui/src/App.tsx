import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  BookOpen,
  Bot,
  DatabaseZap,
  Gauge,
  Handshake,
  History,
  Home as HomeIcon,
  ListChecks,
  MessageSquareText,
  LogOut,
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
import Collab from "./pages/Collab";
import Workroom from "./pages/Workroom";
import Sessions from "./pages/Sessions";
import { ThemeSwitcher, useVocab } from "./theme";

type Page =
  | "home"
  | "workroom"
  | "sessions"
  | "overview"
  | "ops"
  | "collab"
  | "board"
  | "agents"
  | "taskcenter"
  | "skills"
  | "knowledge"
  | "catalog"
  | "infra"
  | "admin";

const NAV: {
  key: Page;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
  teacherVisible?: boolean;
}[] = [
  { key: "home", label: "首页", icon: <HomeIcon size={16} />, teacherVisible: true },
  { key: "workroom", label: "协作空间", icon: <MessageSquareText size={16} />, teacherVisible: true },
  { key: "sessions", label: "会话", icon: <History size={16} />, teacherVisible: true },
  { key: "overview", label: "系统总览", icon: <Gauge size={16} /> },
  { key: "ops", label: "运营看板", icon: <BarChart3 size={16} /> },
  { key: "collab", label: "协作进度", icon: <Handshake size={16} />, teacherVisible: true },
  { key: "board", label: "任务看板", icon: <SquareKanban size={16} />, teacherVisible: true },
  { key: "agents", label: "智能体", icon: <Bot size={16} />, teacherVisible: true },
  { key: "taskcenter", label: "任务中心", icon: <ListChecks size={16} />, teacherVisible: true },
  { key: "skills", label: "技能中心", icon: <Sparkles size={16} /> },
  { key: "catalog", label: "数据整理", icon: <DatabaseZap size={16} /> },
  { key: "knowledge", label: "知识库", icon: <BookOpen size={16} /> },
  { key: "infra", label: "基础设施", icon: <Server size={16} /> },
  { key: "admin", label: "管理", icon: <Settings size={16} />, adminOnly: true },
];

const VIEWER_NAV = new Set<Page>([
  "home",
  "overview",
  "ops",
  "collab",
  "board",
  "agents",
  "skills",
  "knowledge",
  "catalog",
  "infra",
]);

export default function App() {
  const vocab = useVocab();
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);
  const [page, setPage] = useState<Page>("home");
  const [sessionFocus, setSessionFocus] = useState<number | null>(null);
  const [deepTaskId, setDeepTaskId] = useTaskDeepLink();

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

  if (checking) return <div className="boot">加载中…</div>;
  if (!me) return <Login onLogin={() => void refreshMe()} />;

  // The 中枢 hub embeds the site-specific console (admin-only API).
  const teacherMode = me.mode === "teacher";
  const viewerMode = me.role === "viewer";

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
          {NAV.filter(
            (item) =>
              (!item.adminOnly || me.role === "admin") &&
              (!teacherMode || item.teacherVisible) &&
              (!viewerMode || VIEWER_NAV.has(item.key))
          ).map((item) => (
            <button
              key={item.key}
              className={page === item.key ? "is-active" : ""}
              aria-label={teacherMode && item.key === "agents" ? "AI 助理" : item.label}
              onClick={() => setPage(item.key)}
            >
              {item.icon}
              <span>
                {teacherMode && item.key === "agents" ? "AI 助理" : item.label}
              </span>
            </button>
          ))}
        </nav>
        <div className="side-user">
          <div className="side-user__name">
            <strong>{me.display_name || me.name}</strong>
            <small>{teacherMode ? "老师账号" : viewerMode ? "实盘观察席" : me.role === "admin" ? "管理员" : "成员"}</small>
          </div>
          <button
            title="退出登录"
            aria-label="退出登录"
            onClick={() => {
              void api.post("/api/auth/logout").then(() => setMe(null));
            }}
          >
            <LogOut size={15} />
          </button>
        </div>
        <div className="side-theme">
          <ThemeSwitcher />
        </div>
      </aside>
      <main className="main">
        {viewerMode && (
          <div className="real-data-banner">
            <strong>{vocab.liveBanner}</strong>
            <span>这里展示的成员、任务、节点与流转均来自真实运行数据；观察席不能修改内容。</span>
          </div>
        )}
        {page === "home" && (
          <Home
            me={me}
            onNavigate={(p) => setPage(p as Page)}
            onOpenTask={(id) => setDeepTaskId(id)}
            onOpenSession={(id) => {
              setSessionFocus(id);
              setPage("sessions");
            }}
          />
        )}
        {page === "workroom" && <Workroom me={me} />}
        {page === "sessions" && <Sessions me={me} focusSessionId={sessionFocus} />}
        {page === "overview" && <Overview />}
        {page === "ops" && <Operations />}
        {page === "collab" && <Collab me={me} />}
        {page === "board" && <Board me={me} onOpenTask={(id) => setDeepTaskId(id)} />}
        {page === "agents" && <Roster me={me} onNavigate={(target) => setPage(target)} />}
        {page === "taskcenter" && (
          <TaskCenter me={me} onOpenTask={(id) => setDeepTaskId(id)} />
        )}
        {page === "skills" && <Skills me={me} />}
        {page === "knowledge" && <Knowledge />}
        {page === "catalog" && <DataCatalog />}
        {page === "infra" && <Infra />}
        {page === "admin" && me.role === "admin" && <Admin />}
      </main>
      {deepTaskId && (
        <DeepTaskDrawer
          taskId={deepTaskId}
          me={me}
          onClose={() => setDeepTaskId(null)}
          onOpenTask={(id) => setDeepTaskId(id)}
        />
      )}
    </div>
  );
}
