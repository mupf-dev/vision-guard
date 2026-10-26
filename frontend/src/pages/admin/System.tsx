import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { Boxes, Cpu, Radio, Server } from "lucide-react";
import { api } from "../../lib/api";
import { Loading, Empty } from "../../components/ui";
import { StatusBadge } from "../Live";
import { HealthBadge } from "../../components/HealthBadge";
import { useLive } from "../../lib/store";
import { fmtAgo } from "../../lib/format";
import { msgs, useMsgs } from "../../i18n";

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

// R1: Modellnamen und Zustände übersetzt (Rohwerte vom Worker/vlm-Dienst)
const STATE_RE: [RegExp, keyof ReturnType<typeof msgs>["system"]["state"]][] = [
  [/^ready\b/, "ready"], [/^idle\b/, "idle"], [/^off\b/, "off"], [/^loading\b/, "loading"], [/^error\b/, "error"],
  [/^paused\b/, "paused"], [/^disabled\b/, "disabled"],
];
function modelState(v: string): string {
  const st = msgs().system.state;
  for (const [re, k] of STATE_RE) if (re.test(v)) return v.replace(re, st[k]);
  return v;
}

export function SystemPage() {
  const q = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 5000 });
  const live = useLive((s) => s.status);
  const ws = useLive((s) => s.ws);
  const msg = useMsgs();
  const m = msg.system, mc = msg.common;
  if (q.isLoading) return <Loading />;
  if (q.isError || !q.data) return <Empty title={m.unavailable} text={(q.error as Error)?.message} />;
  const { worker, mqtt, cameras, version } = q.data;
  const gpu = worker.gpu ?? {};
  const memFrac = gpu.mem_total ? (gpu.mem_used ?? 0) / gpu.mem_total : null;
  const models = Object.entries(worker.models ?? {});
  return (
    <>
      <div className="page-header">
        <div><h1>{m.title}</h1><div className="sub">{m.sub(version)}</div></div>
      </div>
      <div className="grid-3" style={{ marginBottom: 16 }}>
        <div className="card card-pad row"><Server size={22} color="var(--accent)" /><div className="spacer"><div className="muted" style={{ fontSize: 12 }}>{m.worker}</div><b>{worker.online ? m.connected : m.unreachable}</b></div>
          <span className={`badge ${worker.online ? "ok" : "bad"}`}><span className="dot" />{worker.online ? mc.online : mc.offline}</span></div>
        <div className="card card-pad row"><Radio size={22} color="var(--accent)" /><div className="spacer"><div className="muted" style={{ fontSize: 12 }}>{m.mqtt}</div><b>{mqtt.connected ? m.connected : m.disconnected}</b></div>
          <span className={`badge ${mqtt.connected ? "ok" : "bad"}`}><span className="dot" />{mqtt.connected ? m.ok : mc.error}</span></div>
        <div className="card card-pad row"><Cpu size={22} color="var(--accent)" /><div className="spacer"><div className="muted" style={{ fontSize: 12 }}>{m.liveWs}</div><b>{ws === "open" ? m.connected : ws === "connecting" ? m.connecting : m.disconnected}</b></div>
          <span className={`badge ${ws === "open" ? "ok" : ws === "connecting" ? "warn" : "bad"}`}><span className="dot" />{ws === "open" ? m.ok : ws === "connecting" ? m.wsConnecting : m.wsDisconnected}</span></div>
      </div>

      <div className="section-title">{m.gpu} {gpu.name ? `· ${gpu.name}` : ""}</div>
      <div className="gauges" style={{ marginBottom: 20 }}>
        <Gauge label={m.utilization} value={gpu.util} max={100} display={`${Math.round(gpu.util ?? 0)} %`} />
        <Gauge label={m.memory} value={memFrac} max={1} display={`${Math.round((memFrac ?? 0) * 100)} %`}
          sub={gpu.mem_total ? `${((gpu.mem_used ?? 0) / 1024).toFixed(1)} / ${(gpu.mem_total / 1024).toFixed(1)} GB` : undefined} />
        <Gauge label={m.temperature} value={gpu.temp} max={100} display={`${Math.round(gpu.temp ?? 0)} °C`} warn={0.75} bad={0.85} />
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-head"><Boxes size={18} color="var(--accent)" /><h3>{m.models}</h3></div>
          <div className="card-body">
            {models.length === 0 ? <div className="muted">{m.noModels}</div> : (
              <dl className="kv">{models.map(([k, v]) => <Fragment key={k}><dt>{m.model[k] ?? k}</dt>
                <dd className="mono">{typeof v === "object" ? JSON.stringify(v) : modelState(String(v))}</dd></Fragment>)}</dl>
            )}
          </div>
        </div>
        <div className="card" style={{ overflow: "auto" }}>
          <div className="card-head"><h3>{m.cameras}</h3></div>
          <table className="table">
            <thead><tr><th>{m.colCamera}</th><th>{m.colStatus}</th><th title={m.colImageTitle}>{m.colImage}</th><th>{m.colFps}</th><th>{m.colInference}</th><th>{m.colResolution}</th><th>{m.colLast}</th></tr></thead>
            <tbody>
              {cameras.map((c) => {
                const s = live[c.id] ?? c.status;
                return (
                  <tr key={c.id}>
                    <td><b>{c.name}</b> <span className="muted mono">cam_{c.id}</span></td>
                    <td><StatusBadge status={s} /></td>
                    <td>{s?.state === "disabled" ? "–" : <HealthBadge health={s?.health} full />}</td>
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
