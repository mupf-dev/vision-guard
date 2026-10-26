import { PawPrint } from "lucide-react";
import type { Settings, ZonePurpose } from "../../lib/types";
import { labelName } from "../../lib/labels";
import { ChipSelect, Range, Switch } from "../../components/ui";
import { useMsgs } from "../../i18n";

const PURPOSES: ZonePurpose[] = ["food", "water", "litter"];
const PET_LABELS = ["cat", "dog", "bird", "horse"];

/** Einstellungen → Haustier-Tagebuch (C3). Zonen-Zweck im Zonen-Editor der Kameraverwaltung. */
export function PetSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.pets;
  const routes = f.notify_routes?.pet ?? [];
  return (
    <div className="card">
      <div className="card-head"><PawPrint size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field span-2">
          <div className="muted" style={{ fontSize: 13 }}>
            {m.intro}
          </div>
        </div>
        <div className="field span-2"><label>{m.species}</label>
          <ChipSelect options={Array.from(new Set([...PET_LABELS, ...f.pet_diary_labels]))} value={f.pet_diary_labels}
            render={(l) => labelName(l)} onChange={(v) => patch({ pet_diary_labels: v })} />
        </div>
        <div className="field"><label>{m.notify}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.pet_diary_notify} onChange={(v) => patch({ pet_diary_notify: v })} label={f.pet_diary_notify ? m.notifyOn : all.common.off} />
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            {routes.length ? m.pushTo(routes.join(", ")) : m.pushHint}
          </div>
        </div>
        <div className="field"><label>{m.singlePet}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.pet_diary_single_pet_fallback} onChange={(v) => patch({ pet_diary_single_pet_fallback: v })}
              label={f.pet_diary_single_pet_fallback ? m.singlePetOn : all.common.off} />
          </div>
          <div className="muted" style={{ fontSize: 12 }}>{m.singlePetHint}</div>
        </div>
        {PURPOSES.map((k) => (
          <div className="field" key={k}><label>{m.minVisit(m.purpose[k])}</label>
            <Range value={f.pet_diary_min_visit_s[k] ?? 0} min={0} max={120} step={1} format={(v) => `${v} s`}
              onChange={(v) => patch({ pet_diary_min_visit_s: { ...f.pet_diary_min_visit_s, [k]: v } })} />
          </div>
        ))}
      </div>
    </div>
  );
}
