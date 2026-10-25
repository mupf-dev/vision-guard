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
          <span className="hint">So lange gelten auch die Antwort-Links in Push-Nachrichten.</span>
        </div>
        <div className="field"><label>Höchstens offene Fragen</label>
          <Range value={f.review_max_open} min={1} max={100} step={1} format={(v) => `${v}`} onChange={(v) => patch({ review_max_open: v })} />
        </div>
        <div className="field"><label>Unsicherheits-Band unter der Schwelle</label>
          <Range value={f.review_band} min={0.02} max={0.3} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => patch({ review_band: v })} />
          <span className="hint">Gegenstände mit einer Konfidenz knapp unter (bzw. knapp über) der Schwelle werden nachgefragt.</span>
        </div>
        <div className="field"><label>Schwellen nachführen</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.review_auto_tune} onChange={(v) => patch({ review_auto_tune: v })} label={f.review_auto_tune ? "aus Antworten lernen (je Kamera und Gegenstand)" : "aus"} />
          </div>
          <span className="hint">Ab 3 Antworten, höchstens 0,05 je Antwort; Werte in Administration → Kameras → Gegenstände.</span>
        </div>
        <div className="field"><label>Exemplar-Schwelle (Kosinus-Ähnlichkeit)</label>
          <Range value={f.item_match_threshold} min={0.3} max={0.9} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => patch({ item_match_threshold: v })} />
          <span className="hint">Eigene Gegenstände: darunter (bis 0,15 tiefer) wird nachgefragt statt zugeordnet.</span>
        </div>
        <div className="field"><label>Mindestgröße für eigene Gegenstände</label>
          <Range value={f.item_min_px} min={0} max={300} step={10} format={(v) => `${v} px`} onChange={(v) => patch({ item_min_px: v })} />
          <span className="hint">Kleinere Gegenstände im Kamerabild werden nie automatisch zugeordnet (Messung: erst ab ≈ 90 px zuverlässig).</span>
        </div>
        <div className="field span-2"><span className="hint">Push für Rückfragen (mit Ja/Nein-Knöpfen bei ntfy): Einstellungen → Benachrichtigungen, Quelle „Rückfragen“. Die Knöpfe brauchen eine öffentliche Adresse (public_base_url).</span></div>
      </div>
    </div>
  );
}
