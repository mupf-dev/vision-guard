import { useQuery } from "@tanstack/react-query";
import { MessageCircleQuestion } from "lucide-react";
import { api } from "../../lib/api";
import type { Settings, VlmModel } from "../../lib/types";
import { Range, Switch } from "../../components/ui";
import { msgs, useMsgs } from "../../i18n";

const MODELS: VlmModel[] = ["moondream2-int4", "smolvlm-500m", "qwen25vl-3b-nf4"];

function vlmState(s: unknown): { text: string; cls: string } {
  const m = msgs().settings.vlm;
  const v = typeof s === "string" ? s : "off";
  if (v === "ready") return { text: m.stateReady, cls: "ok" };
  if (v === "loading") return { text: m.stateLoading, cls: "info" };
  if (v.startsWith("error")) return { text: v.replace(/^error:\s*/, m.stateError), cls: "bad" };
  return { text: m.stateOff, cls: "outline" };
}

/** Einstellungen → Frag die Kamera (W3, A2). */
export function VlmSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.vlm;
  const sys = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 10000 });
  const st = vlmState(sys.data?.worker.models?.vlm);
  const cloudModel = (f.vlm_cloud_model ?? "").trim();
  const cloudReady = !!cloudModel && f.llm_provider !== "none";
  return (
    <div className="card">
      <div className="card-head"><MessageCircleQuestion size={18} color="var(--accent)" /><h3>{m.title}</h3>
        <span className="spacer" /><span className={`badge ${st.cls}`} title={m.stateTitle}>{st.text}</span>
      </div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.local}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.vlm_enabled} onChange={(v) => patch({ vlm_enabled: v })} label={f.vlm_enabled ? all.common.on : all.common.off} />
          </div>
        </div>
        <div className="field"><label>{m.model}</label>
          <select className="input" value={f.vlm_model} onChange={(e) => patch({ vlm_model: e.target.value as VlmModel })}>
            {MODELS.map((k) => <option key={k} value={k}>{m.models[k]}</option>)}
          </select>
        </div>
        <div className="field"><label>{m.idleUnload}</label>
          <Range value={f.vlm_idle_unload_s} min={30} max={3600} step={30} format={(v) => `${v} s`} onChange={(v) => patch({ vlm_idle_unload_s: v })} />
        </div>
        <div className="field"><label>{m.needMb}</label>
          <Range value={f.vlm_need_mb} min={500} max={6000} step={100} format={(v) => `${v} MB`} onChange={(v) => patch({ vlm_need_mb: v })} />
        </div>
        <div className="field"><label>{m.cloudModel}</label>
          <input className="input" value={f.vlm_cloud_model ?? ""} placeholder={m.cloudPlaceholder}
            onChange={(e) => patch({ vlm_cloud_model: e.target.value })} />
        </div>
        <div className="field"><label>{m.retention}</label>
          <Range value={f.retention_days_ask} min={1} max={90} step={1} format={(v) => all.settings.shared.days(v)} onChange={(v) => patch({ retention_days_ask: v })} />
        </div>
        <div className="field span-2">
          <span className="hint muted" style={{ fontSize: 12 }}>
            {m.hintRuns} <code>vlm</code> ({m.hintStart} <code>docker compose --profile vlm up -d vlm</code>). {m.hintRest}
          </span>
          {cloudModel && !cloudReady && (
            <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>
              {m.cloudNotReady}
            </span>
          )}
          {cloudReady && (
            <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>
              {m.cloudReady}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
