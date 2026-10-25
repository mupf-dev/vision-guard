import { useQuery } from "@tanstack/react-query";
import { MessageCircleQuestion } from "lucide-react";
import { api } from "../../lib/api";
import type { Settings, VlmModel } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

const MODELS: Record<VlmModel, string> = {
  "moondream2-int4": "Moondream 2 (INT4, empfohlen)",
  "smolvlm-500m": "SmolVLM 500M (klein, weniger Grafikspeicher)",
  "qwen25vl-3b-nf4": "Qwen2.5-VL 3B (versteht Deutsch, nur nicht-kommerziell, lädt ≈ 10 s)",
};

function vlmState(s: unknown): { text: string; cls: string } {
  const v = typeof s === "string" ? s : "off";
  if (v === "ready") return { text: "geladen", cls: "ok" };
  if (v === "loading") return { text: "lädt …", cls: "info" };
  if (v.startsWith("error")) return { text: v.replace(/^error:\s*/, "Fehler: "), cls: "bad" };
  return { text: "nicht geladen", cls: "outline" };
}

/** Einstellungen → Frag die Kamera (W3, A2). */
export function VlmSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const sys = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 10000 });
  const st = vlmState(sys.data?.worker.models?.vlm);
  const cloudModel = (f.vlm_cloud_model ?? "").trim();
  const cloudReady = !!cloudModel && f.llm_provider !== "none";
  return (
    <div className="card">
      <div className="card-head"><MessageCircleQuestion size={18} color="var(--accent)" /><h3>Frag die Kamera</h3>
        <span className="spacer" /><span className={`badge ${st.cls}`} title="Zustand des lokalen Bildmodells (Dienst vlm)">{st.text}</span>
      </div>
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
        <div className="field"><label>Grafikspeicher-Bedarf</label>
          <Range value={f.vlm_need_mb} min={500} max={6000} step={100} format={(v) => `${v} MB`} onChange={(v) => patch({ vlm_need_mb: v })} />
        </div>
        <div className="field"><label>Cloud-Modell (leer = Cloud aus)</label>
          <input className="input" value={f.vlm_cloud_model ?? ""} placeholder="z. B. Modell-ID des KI-Anbieters"
            onChange={(e) => patch({ vlm_cloud_model: e.target.value })} />
        </div>
        <div className="field"><label>Fragen aufbewahren</label>
          <Range value={f.retention_days_ask} min={1} max={90} step={1} format={(v) => `${v} Tage`} onChange={(v) => patch({ retention_days_ask: v })} />
        </div>
        <div className="field span-2">
          <span className="hint muted" style={{ fontSize: 12 }}>
            Das lokale Modell läuft im Dienst <code>vlm</code> (Start: <code>docker compose --profile vlm up -d vlm</code>). Es wird erst bei
            der ersten Frage geladen; solange es geladen ist, pausiert die Gegenstandserkennung (gemeinsamer Grafikspeicher).
            Fragen am besten auf Englisch stellen.
          </span>
          {cloudModel && !cloudReady && (
            <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>
              Cloud-Modell eingetragen, aber kein KI-Anbieter eingerichtet (Einstellungen → KI-Sprachmodell) – Cloud bleibt aus.
            </span>
          )}
          {cloudReady && (
            <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>
              Cloud-Fallback aktiv für Kameras mit Freigabe (Administration → Kameras): deren Standbilder können das Haus verlassen.
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
