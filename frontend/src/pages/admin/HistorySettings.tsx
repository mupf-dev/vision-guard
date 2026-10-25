import { Clock } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Range } from "../../components/ui";

/** Einstellungen → Zeitmaschine (W3, Gerüst K3 – Ausbau B1: Speicheranzeige, „Verlauf löschen“). */
export function HistorySettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  return (
    <div className="card">
      <div className="card-head"><Clock size={18} color="var(--accent)" /><h3>Zeitmaschine</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Aufbewahrung</label>
          <Range value={f.retention_days_history} min={1} max={90} step={1} format={(v) => `${v} Tage`} onChange={(v) => patch({ retention_days_history: v })} />
        </div>
        <div className="field"><label>Änderungsschwelle (neuer Eintrag ab)</label>
          <Range value={f.history_min_change} min={0} max={0.3} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => patch({ history_min_change: v })} />
        </div>
      </div>
    </div>
  );
}
