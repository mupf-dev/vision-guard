import { MessageCircleQuestion } from "lucide-react";
import type { Settings, VlmModel } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

const MODELS: Record<VlmModel, string> = {
  "moondream2-int4": "Moondream 2 (INT4, empfohlen)",
  "smolvlm-500m": "SmolVLM 500M (klein, ohne GPU-Lease)",
  "qwen25vl-3b-nf4": "Qwen2.5-VL 3B (Deutsch, nur nicht-kommerziell)",
};

/** Einstellungen → Frag die Kamera (W3, Gerüst K3 – Ausbau A2). */
export function VlmSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  return (
    <div className="card">
      <div className="card-head"><MessageCircleQuestion size={18} color="var(--accent)" /><h3>Frag die Kamera</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Lokales Bildmodell</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.vlm_enabled} onChange={(v) => patch({ vlm_enabled: v })} label={f.vlm_enabled ? "an" : "aus"} />
          </div>
        </div>
        <div className="field"><label>Modell</label>
          <select className="input" value={f.vlm_model} onChange={(e) => patch({ vlm_model: e.target.value as VlmModel })}>
            {(Object.keys(MODELS) as VlmModel[]).map((k) => <option key={k} value={k}>{MODELS[k]}</option>)}
          </select>
        </div>
        <div className="field"><label>Entladen nach Leerlauf</label>
          <Range value={f.vlm_idle_unload_s} min={30} max={3600} step={30} format={(v) => `${v} s`} onChange={(v) => patch({ vlm_idle_unload_s: v })} />
        </div>
        <div className="field"><label>Cloud-Modell (leer = Cloud aus)</label>
          <input className="input" value={f.vlm_cloud_model ?? ""} placeholder="z. B. Modell-ID des KI-Anbieters"
            onChange={(e) => patch({ vlm_cloud_model: e.target.value })} />
        </div>
        <div className="field"><label>Fragen aufbewahren</label>
          <Range value={f.retention_days_ask} min={1} max={90} step={1} format={(v) => `${v} Tage`} onChange={(v) => patch({ retention_days_ask: v })} />
        </div>
      </div>
    </div>
  );
}
