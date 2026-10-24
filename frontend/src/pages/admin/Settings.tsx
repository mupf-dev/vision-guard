import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Fingerprint, Radio, RotateCcw, Save } from "lucide-react";
import { api } from "../../lib/api";
import type { Settings } from "../../lib/types";
import { Loading, Range, Switch } from "../../components/ui";
import { toast } from "../../lib/store";

export function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [f, setF] = useState<Settings | null>(null);
  useEffect(() => { if (q.data) setF(q.data); }, [q.data]);
  const save = useMutation({
    mutationFn: (s: Settings) => api.saveSettings(s),
    onSuccess: (r) => { toast.success("Einstellungen gespeichert"); qc.setQueryData(["settings"], r ?? f); },
    onError: (e: Error) => toast.error("Speichern fehlgeschlagen", e.message),
  });
  if (q.isLoading || !f) return <Loading />;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setF({ ...f, [k]: v });
  const dirty = JSON.stringify(f) !== JSON.stringify(q.data);

  return (
    <>
      <div className="page-header">
        <div><h1>Einstellungen</h1><div className="sub">Aufbewahrung, Wiedererkennung und MQTT</div></div>
        <div className="row">
          {dirty && <button className="btn btn-ghost" onClick={() => setF(q.data!)}><RotateCcw size={16} />Verwerfen</button>}
          <button className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate(f)}>{save.isPending ? <span className="spinner" /> : <Save size={16} />}Speichern</button>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="card">
          <div className="card-head"><Archive size={18} color="var(--accent)" /><h3>Aufbewahrung</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>Ereignisse aufbewahren</label>
              <div className="row"><input type="number" min={1} className="input" value={f.retention_days_events} onChange={(e) => set("retention_days_events", Number(e.target.value))} /><span className="muted">Tage</span></div>
            </div>
            <div className="field"><label>Medien (Snapshots, Clips) aufbewahren</label>
              <div className="row"><input type="number" min={1} className="input" value={f.retention_days_media} onChange={(e) => set("retention_days_media", Number(e.target.value))} /><span className="muted">Tage</span></div>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><Fingerprint size={18} color="var(--accent)" /><h3>Wiedererkennung</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>Gesichts-Schwelle (Kosinus-Ähnlichkeit)</label>
              <Range value={f.face_match_threshold} min={0.2} max={0.9} step={0.01} onChange={(v) => set("face_match_threshold", v)} format={(v) => v.toFixed(2)} />
              <span className="hint">Höher = strenger, weniger Verwechslungen, aber mehr neue Identitäten.</span>
            </div>
            <div className="field"><label>Tier-Schwelle (Kosinus-Ähnlichkeit)</label>
              <Range value={f.pet_match_threshold} min={0.5} max={0.99} step={0.01} onChange={(v) => set("pet_match_threshold", v)} format={(v) => v.toFixed(2)} />
            </div>
            <div className="field"><label>Kennzeichen: max. Zeichen-Abweichung</label>
              <Range value={f.plate_max_distance} min={0} max={3} step={1} onChange={(v) => set("plate_max_distance", v)} format={(v) => `${v}`} />
              <span className="hint">Levenshtein-Distanz für OCR-Toleranz.</span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><Radio size={18} color="var(--accent)" /><h3>MQTT</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>Topic-Präfix</label><input className="input mono" value={f.mqtt_prefix} onChange={(e) => set("mqtt_prefix", e.target.value)} />
              <span className="hint">z. B. <code>{f.mqtt_prefix || "visionguard"}/events</code>, <code>{f.mqtt_prefix || "visionguard"}/einfahrt/person</code></span>
            </div>
            <div className="field"><label>Alle Ereignisse veröffentlichen</label>
              <div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.publish_all_events} onChange={(v) => set("publish_all_events", v)} label={<code>{f.mqtt_prefix}/events</code>} /></div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
