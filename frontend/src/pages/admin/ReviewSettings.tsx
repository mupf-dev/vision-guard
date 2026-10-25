import { CircleHelp } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

/** Einstellungen → Rückfragen & Exemplare (W2, Gerüst K2 – Ausbau D2/B4). */
export function ReviewSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  return (
    <div className="card">
      <div className="card-head"><CircleHelp size={18} color="var(--accent)" /><h3>Rückfragen &amp; eigene Gegenstände</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Rückfragen</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.review_enabled} onChange={(v) => patch({ review_enabled: v })} label={f.review_enabled ? "bei unsicheren Erkennungen nachfragen" : "aus"} />
          </div>
        </div>
        <div className="field"><label>Höchstens neue Fragen pro Tag</label>
          <Range value={f.review_max_per_day} min={0} max={50} step={1} format={(v) => `${v}`} onChange={(v) => patch({ review_max_per_day: v })} />
        </div>
        <div className="field"><label>Offene Fragen verfallen nach</label>
          <Range value={f.review_expire_h} min={1} max={168} step={1} format={(v) => `${v} h`} onChange={(v) => patch({ review_expire_h: v })} />
        </div>
        <div className="field"><label>Exemplar-Schwelle (Kosinus-Ähnlichkeit)</label>
          <Range value={f.item_match_threshold} min={0.3} max={0.9} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => patch({ item_match_threshold: v })} />
          <span className="hint">Darunter wird nachgefragt statt zugeordnet. Kleine Dinge (unter {f.item_min_px} px im Bild) werden nicht automatisch zugeordnet.</span>
        </div>
      </div>
    </div>
  );
}
