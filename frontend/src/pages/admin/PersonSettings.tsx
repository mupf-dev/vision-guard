import { PersonStanding } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Range } from "../../components/ui";
import { fmtNum, useMsgs } from "../../i18n";

/** Einstellungen → Haltung & Gesten (W2 C1/D1). Schalter je Kamera in der Kameraverwaltung. */
export function PersonSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.person;
  return (
    <div className="card">
      <div className="card-head"><PersonStanding size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.gestureHold}</label>
          <Range value={f.gesture_hold_s} min={0.5} max={5} step={0.1} format={(v) => `${fmtNum(v, 1)} s`} onChange={(v) => patch({ gesture_hold_s: v })} />
        </div>
        <div className="field"><label>{m.fallDwell}</label>
          <Range value={f.fall_default_dwell_s} min={5} max={600} step={5} format={(v) => `${v} s`} onChange={(v) => patch({ fall_default_dwell_s: v })} />
        </div>
        <div className="field"><label>{m.fallGrace}</label>
          <Range value={f.fall_track_grace_s} min={0} max={300} step={5} format={(v) => (v ? `${v} s` : all.common.off)} onChange={(v) => patch({ fall_track_grace_s: v })} />
          <span className="hint muted" style={{ fontSize: 12 }}>{m.fallGraceHint}</span>
        </div>
        <div className="field"><label>{all.settings.shared.hints}</label>
          <span className="hint muted" style={{ fontSize: 12 }}>{m.hint}</span>
        </div>
      </div>
    </div>
  );
}
