import { PawPrint } from "lucide-react";
import type { Settings, ZonePurpose } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

const PURPOSE_NAME: Record<ZonePurpose, string> = { food: "Napf", water: "Wasser", litter: "Klo" };

/** Einstellungen → Haustier-Tagebuch (W2, Gerüst K2 – Ausbau C3). Zonen-Zweck im Zonen-Editor. */
export function PetSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  return (
    <div className="card">
      <div className="card-head"><PawPrint size={18} color="var(--accent)" /><h3>Haustier-Tagebuch</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Abweichungen melden</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.pet_diary_notify} onChange={(v) => patch({ pet_diary_notify: v })} label={f.pet_diary_notify ? "an (Hinweis „Tiere“)" : "aus"} />
          </div>
        </div>
        {(Object.keys(PURPOSE_NAME) as ZonePurpose[]).map((k) => (
          <div className="field" key={k}><label>{PURPOSE_NAME[k]}: Besuch zählt ab</label>
            <Range value={f.pet_diary_min_visit_s[k] ?? 0} min={0} max={60} step={1} format={(v) => `${v} s`}
              onChange={(v) => patch({ pet_diary_min_visit_s: { ...f.pet_diary_min_visit_s, [k]: v } })} />
          </div>
        ))}
      </div>
    </div>
  );
}
