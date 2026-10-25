import { PersonStanding } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Range } from "../../components/ui";

/** Einstellungen → Haltung & Gesten (W2, Gerüst K2 – Ausbau C1/D1). Schalter je Kamera in der Kameraverwaltung. */
export function PersonSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  return (
    <div className="card">
      <div className="card-head"><PersonStanding size={18} color="var(--accent)" /><h3>Haltung &amp; Gesten</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Geste mindestens halten</label>
          <Range value={f.gesture_hold_s} min={0.5} max={5} step={0.1} format={(v) => `${v.toFixed(1)} s`} onChange={(v) => patch({ gesture_hold_s: v })} />
        </div>
        <div className="field"><label>„Liegt am Boden“: Vorschlag für die Dauer</label>
          <Range value={f.fall_default_dwell_s} min={5} max={600} step={5} format={(v) => `${v} s`} onChange={(v) => patch({ fall_default_dwell_s: v })} />
        </div>
      </div>
    </div>
  );
}
