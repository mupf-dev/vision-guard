import { PawPrint } from "lucide-react";
import type { Settings, ZonePurpose } from "../../lib/types";
import { labelName } from "../../lib/labels";
import { ChipSelect, Range, Switch } from "../../components/ui";

const PURPOSE_NAME: Record<ZonePurpose, string> = { food: "Napf", water: "Wasser", litter: "Klo" };
const PET_LABELS = ["cat", "dog", "bird", "horse"];

/** Einstellungen → Haustier-Tagebuch (C3). Zonen-Zweck im Zonen-Editor der Kameraverwaltung. */
export function PetSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const routes = f.notify_routes?.pet ?? [];
  return (
    <div className="card">
      <div className="card-head"><PawPrint size={18} color="var(--accent)" /><h3>Haustier-Tagebuch</h3></div>
      <div className="card-body form-grid">
        <div className="field span-2">
          <div className="muted" style={{ fontSize: 13 }}>
            Zählt Besuche von Tieren in Zonen mit dem Zweck „Napf“, „Wasser“ oder „Katzenklo“ (Kameraverwaltung → Zonen)
            und vergleicht sie mit den letzten 14 Tagen. Abweichungen erscheinen als Hinweis „Tiere“, sobald mindestens
            7 Tage Daten vorliegen.
          </div>
        </div>
        <div className="field span-2"><label>Tierarten</label>
          <ChipSelect options={Array.from(new Set([...PET_LABELS, ...f.pet_diary_labels]))} value={f.pet_diary_labels}
            render={(l) => labelName(l)} onChange={(v) => patch({ pet_diary_labels: v })} />
        </div>
        <div className="field"><label>Abweichungen melden</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.pet_diary_notify} onChange={(v) => patch({ pet_diary_notify: v })} label={f.pet_diary_notify ? "an (Hinweis „Tiere“)" : "aus"} />
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            {routes.length ? `Push an: ${routes.join(", ")}` : "Push-Ziele: Einstellungen → Benachrichtigungen (Quelle „Tiere“)"}
          </div>
        </div>
        <div className="field"><label>Nur ein Tier benannt</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.pet_diary_single_pet_fallback} onChange={(v) => patch({ pet_diary_single_pet_fallback: v })}
              label={f.pet_diary_single_pet_fallback ? "unerkannte Besuche ihm zuordnen" : "aus"} />
          </div>
          <div className="muted" style={{ fontSize: 12 }}>Hilft, wenn das Tier von oben am Napf nicht erkannt wird.</div>
        </div>
        {(Object.keys(PURPOSE_NAME) as ZonePurpose[]).map((k) => (
          <div className="field" key={k}><label>{PURPOSE_NAME[k]}: Besuch zählt ab</label>
            <Range value={f.pet_diary_min_visit_s[k] ?? 0} min={0} max={120} step={1} format={(v) => `${v} s`}
              onChange={(v) => patch({ pet_diary_min_visit_s: { ...f.pet_diary_min_visit_s, [k]: v } })} />
          </div>
        ))}
      </div>
    </div>
  );
}
