import { useEffect, useState } from "react";
import { Server } from "lucide-react";
import { api, readErrorMessage } from "../api";
import type { NodeInfo, NodeRuntimeState } from "../types";
import { fmtBytes, fmtUptime } from "../types";
import { Ambient, DataState, PageHeader, Panel } from "../components/ui";

const RUNTIME_STATE_LABEL: Record<NodeRuntimeState, string> = {
  never_probed: "从未探测",
  probed_empty: "已探测 · 未发现运行时",
  probed_found: "已探测 · 发现运行时",
};

function cliText(entry: NodeInfo["runtimes"][number]): string {
  if (!entry.available) return "—";
  const where =
    entry.source === "path"
      ? "PATH"
      : entry.source === "well-known"
        ? "常规安装目录"
        : entry.source;
  return `${entry.command || entry.runtime}（${where}）`;
}

function dataText(entry: NodeInfo["runtimes"][number]): string {
  if (entry.data_state === "present") return entry.path_hint ?? "有";
  if (entry.data_state === "none") return "无";
  return "未知（旧版探针未上报）";
}

export default function Infra() {
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        setNodes(await api.get<NodeInfo[]>("/api/nodes"));
        setError(null);
        setLoaded(true);
      } catch (reason) {
        setError(readErrorMessage(reason));
      } finally {
        setLoading(false);
      }
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="rt-page infra-page">
      <Ambient />
      <PageHeader
        kicker="NODES, SERVICES & HEALTH"
        title="基础设施"
        subtitle={loaded ? `${nodes.length} 台节点在册 · 分层低频心跳（服务器小时级 / 终端 6 小时级）` : "正在读取节点与健康心跳"}
      />

      {loading && !loaded && <DataState loading />}
      {error && <DataState error={error} stale={loaded} />}

      {loaded && nodes.map((node) => {
        const memTotal = node.memory.total ?? 0;
        const memUsed = memTotal - (node.memory.available ?? 0);
        return (
          <Panel
            key={node.id}
            icon={<Server size={15} />}
            kicker={node.hostname || node.id}
            title={node.label || node.id}
          >
            <p className="muted" style={{ margin: "0 0 10px", fontSize: "0.72rem" }}>
              {node.platform}
            </p>
            <div className="infra-stats">
              <span>
                运行 <strong>{fmtUptime(node.uptime_seconds)}</strong>
              </span>
              {node.load.length > 0 && (
                <span>
                  负载 <strong>{node.load.map((v) => v.toFixed(2)).join(" / ")}</strong>
                </span>
              )}
              {memTotal > 0 && (
                <span>
                  内存 <strong>{fmtBytes(memUsed)} / {fmtBytes(memTotal)}</strong>
                </span>
              )}
              {node.disk.total !== undefined && (
                <span>
                  磁盘{" "}
                  <strong>
                    {fmtBytes(node.disk.used ?? 0)} / {fmtBytes(node.disk.total)}(
                    {(node.disk.percent ?? 0).toFixed(1)}%)
                  </strong>
                </span>
              )}
              <span className="muted">
                心跳{" "}
                {node.updated_at
                  ? new Date(node.updated_at).toLocaleString("zh-CN", { hour12: false })
                  : "—"}
              </span>
              <span className="muted">
                运行时探针 {RUNTIME_STATE_LABEL[node.runtime_state]}
                {node.runtimes_probed_at
                  ? ` · ${new Date(node.runtimes_probed_at).toLocaleString("zh-CN", { hour12: false })}`
                  : ""}
              </span>
            </div>
            {node.runtimes.some((rt) => rt.available || rt.path_hint) && (
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>运行时</th>
                    <th>CLI</th>
                    <th>本地历史</th>
                  </tr>
                </thead>
                <tbody>
                  {node.runtimes
                    .filter((rt) => rt.available || rt.path_hint)
                    .map((rt) => (
                      <tr key={rt.runtime}>
                        <td>{rt.runtime}</td>
                        <td>{cliText(rt)}</td>
                        <td>
                          {dataText(rt)}
                          {rt.data_state === "present" && rt.data_changed_at
                            ? ` · ${new Date(rt.data_changed_at).toLocaleString("zh-CN", { hour12: false })}`
                            : ""}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
            {node.services.length > 0 && (
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>服务</th>
                    <th>状态</th>
                    <th>重启次数</th>
                    <th>健康</th>
                  </tr>
                </thead>
                <tbody>
                  {node.services.map((svc) => (
                    <tr key={svc.unit}>
                      <td>{svc.label || svc.unit}</td>
                      <td>
                        {svc.active}
                        {svc.sub ? ` / ${svc.sub}` : ""}
                      </td>
                      <td>{svc.restarts ?? 0}</td>
                      <td>
                        <span className={`chip ${svc.healthy ? "chip-low" : "chip-urgent"}`}>
                          {svc.healthy ? "正常" : "异常"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        );
      })}
      {loaded && nodes.length === 0 && <DataState empty="确实尚无节点接入。" />}
    </div>
  );
}
