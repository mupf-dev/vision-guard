import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { Boxes, Cpu, Radio, Server } from "lucide-react";
import { api } from "../../lib/api";
import { Loading, Empty } from "../../components/ui";
import { StatusBadge } from "../Live";
import { useLive } from "../../lib/store";
import { fmtAgo } from "../../lib/format";

function Gauge({ value, max, label, display, sub, warn = 0.7, bad = 0.9 }: { value: number | null | undefined; max: number; label: string; display: string; sub?: string; warn?: number; bad?: number }) {
  const r = 52, c = Math.PI * r;
  const frac = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value / max));
  const col = frac >= bad ? "var(--bad)" : frac >= warn ? "var(--warn)" : "var(--accent)";
  return (
    <div className="card gauge">
      <svg viewBox="0 0 130 74">
        <path d="M13 66 A52 52 0 0 1 117 66" fill="none" stroke="var(--bg-4)" strokeWidth={10} strokeLinecap="round" />
        <path d="M13 66 A52 52 0 0 1 117 66" fill="none" stroke={col} strokeWidth={10} strokeLinecap="round"
          strokeDasharray={`${c * frac} ${c}`} style={{ transition: "stroke-dasharray .6s var(--ease), stroke .3s", filter: `drop-shadow(0 0 6px ${col})` }} />
      </svg>
      <div className="gv">{value === null || value === undefined ? "–" : display}</div>
      <div className="gl">{label}</div>
      {sub && <div className="gs">{sub}</div>}
    </div>
  );
}

export function SystemPage() {
  const q = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 5000 });
  const live = useLive((s) => s.status);
  const ws = useLive((s) => s.ws);
  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data) return <Empty title="Systemstatus nicht verfügbar" text={(q.error as Error)?.message} />;
  const { worker, mqtt, cameras, version } = q.data;
  const gpu = worker.gpu ?? {};
  const memFrac = gpu.mem_total ? (gpu.mem_used ?? 0) / gpu.mem_total : null;
  const models = Object.entries(worker.models ?? {});
  return (
    <>
      <div className="page-header">
        <div><h1>System</h1><div className="sub">Worker, GPU, Modelle und Verbindungen{version ? ` · Version ${version}` : ""}</div></div>
      </div>
      <div className="grid-3" style={{ marginBottom: 16 }}>
        <div className="card card-pad row"><Server size={22} color="var(--accent)" /><div className="spacer"><div className="muted" style={{ fontSize: 12 }}>KI-Worker</div><b>{worker.online ? "verbunden" : "nicht erreichbar"}</b></div>
          <span className={`badge ${worker.online ? "ok" : "bad"}`}><span className="dot" />{worker.online ? "Online" : "Offline"}</span></div>
        <div className="card card-pad row"><Radio size={22} color="var(--accent)" /><div className="spacer"><div className="muted" style={{ fontSize: 12 }}>MQTT-Broker</div><b>{mqtt.connected ? "verbunden" : "getrennt"}</b></div>
          <span className={`badge ${mqtt.connected ? "ok" : "bad"}`}><span className="dot" />{mqtt.connected ? "OK" : "Fehler"}</span></div>
        <div className="card card-pad row"><Cpu size={22} color="var(--accent)" /><div className="spacer"><div className="muted" style={{ fontSize: 12 }}>Live-WebSocket</div><b>{ws === "open" ? "verbunden" : ws === "connecting" ? "verbinde …" : "getrennt"}</b></div>
          <span className={`badge ${ws === "open" ? "ok" : ws === "connecting" ? "warn" : "bad"}`}><span className="dot" />{ws === "open" ? "OK" : ws === "connecting" ? "Verbinde" : "Getrennt"}</span></div>
      </div>

      <div className="section-title">GPU {gpu.name ? `· ${gpu.name}` : ""}</div>
      <div className="gauges" style={{ marginBottom: 20 }}>
        <Gauge label="Auslastung" value={gpu.util} max={100} display={`${Math.round(gpu.util ?? 0)} %`} />
        <Gauge label="Speicher" value={memFrac} max={1} display={`${Math.round((memFrac ?? 0) * 100)} %`}
          sub={gpu.mem_total ? `${((gpu.mem_used ?? 0) / 1024).toFixed(1)} / ${(gpu.mem_total / 1024).toFixed(1)} GB` : undefined} />
        <Gauge label="Temperatur" value={gpu.temp} max={100} display={`${Math.round(gpu.temp ?? 0)} °C`} warn={0.75} bad={0.85} />
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-head"><Boxes size={18} color="var(--accent)" /><h3>Modelle</h3></div>
          <div className="card-body">
            {models.length === 0 ? <div className="muted">Keine Modellinformationen.</div> : (
              <dl className="kv">{models.map(([k, v]) => <Fragment key={k}><dt>{{ detector: "Detektor", face: "Gesicht", plate: "Kennzeichen", pet: "Tier" }[k] ?? k}</dt>
                <dd className="mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></Fragment>)}</dl>
            )}
          </div>
        </div>
        <div className="card" style={{ overflow: "auto" }}>
          <div className="card-head"><h3>Kameras</h3></div>
          <table className="table">
            <thead><tr><th>Kamera</th><th>Status</th><th>FPS</th><th>Inferenz</th><th>Auflösung</th><th>Zuletzt</th></tr></thead>
            <tbody>
              {cameras.map((c) => {
                const s = live[c.id] ?? c.status;
                return (
                  <tr key={c.id}>
                    <td><b>{c.name}</b> <span className="muted mono">cam_{c.id}</span></td>
                    <td><StatusBadge status={s} /></td>
                    <td className="mono">{s?.fps != null ? s.fps.toFixed(1) : "–"}</td>
                    <td className="mono">{s?.inference_ms != null ? `${s.inference_ms.toFixed(1)} ms` : "–"}</td>
                    <td className="mono">{s?.width ? `${s.width}×${s.height}` : "–"}</td>
                    <td className="muted">{fmtAgo(s?.last_seen)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
