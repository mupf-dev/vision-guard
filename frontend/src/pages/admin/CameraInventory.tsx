import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Package } from "lucide-react";
import { api } from "../../lib/api";
import type { CameraInput, InventoryVocabEntry } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

/** Vokabular nach Anzeige-Gruppe sortiert (Reihenfolge des Vokabulars bleibt innerhalb der Gruppe erhalten). */
export function groupVocab(vocab: InventoryVocabEntry[]): [string, InventoryVocabEntry[]][] {
  const m = new Map<string, InventoryVocabEntry[]>();
  for (const v of vocab) {
    const g = v.group || "Sonstiges";
    if (!m.has(g)) m.set(g, []);
    m.get(g)!.push(v);
  }
  return [...m.entries()];
}

/** Abschnitt „Gegenstandserkennung“ im Kamera-Dialog. */
export function CameraInventoryTab({ f, set }: {
  f: CameraInput;
  set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void;
}) {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const vocab = useMemo(() => settings.data?.inventory_vocabulary ?? [], [settings.data]);
  const groups = useMemo(() => groupVocab(vocab), [vocab]);
  const known = new Set(vocab.map((v) => v.label));
  const sel = f.inventory_labels;
  const unknown = sel.filter((l) => vocab.length > 0 && !known.has(l));
  const toggle = (label: string) => set("inventory_labels", sel.includes(label) ? sel.filter((l) => l !== label) : [...sel, label]);
  const setGroup = (entries: InventoryVocabEntry[], on: boolean) => {
    const labels = entries.map((e) => e.label);
    set("inventory_labels", on ? [...sel, ...labels.filter((l) => !sel.includes(l))] : sel.filter((l) => !labels.includes(l)));
  };
  const off = !f.inventory_enabled;

  return (
    <div className="form-grid">
      <div className="field span-2">
        <label>Gegenstandserkennung</label>
        <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
          <Switch checked={f.inventory_enabled} onChange={(v) => set("inventory_enabled", v)}
            label={f.inventory_enabled ? "aktiv – Gegenstände im Bild werden regelmäßig erfasst" : "aus"} />
        </div>
        <span className="hint">
          Erkennt ruhende Gegenstände (z. B. Schlüssel, Fernbedienung, Buch) in festen Abständen und merkt sich, wo sie
          zuletzt gesehen wurden. Läuft zusätzlich zur Objekterkennung und benötigt etwas GPU-Speicher.
        </span>
      </div>
      <div className="field"><label>Raum</label>
        <input className="input" value={f.room ?? ""} maxLength={100} placeholder="z. B. Wohnzimmer"
          onChange={(e) => set("room", e.target.value || null)} />
        <span className="hint">Gruppiert Gegenstände auf der Seite „Gegenstände“ und in der Suche.</span>
      </div>
      <div className="field"><label>Intervall</label>
        <Range value={f.inventory_interval_s} min={1} max={60} step={1} onChange={(v) => set("inventory_interval_s", v)}
          format={(v) => `alle ${v} s`} />
        <span className="hint">Abstand zwischen zwei Durchläufen. Größer = weniger GPU-Last.</span>
      </div>
      <div className="field"><label>Mindest-Konfidenz</label>
        <Range value={f.inventory_min_score} min={0.05} max={0.95} step={0.05} onChange={(v) => set("inventory_min_score", v)}
          format={(v) => `${Math.round(v * 100)} %`} />
        <span className="hint">Gilt für alle Gegenstände ohne eigene Schwelle im Vokabular.</span>
      </div>
      <div className="field"><label>Kachelung</label>
        <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
          <Switch checked={f.inventory_tiling} onChange={(v) => set("inventory_tiling", v)}
            label={f.inventory_tiling ? "Bild in Kacheln analysieren" : "ganzes Bild auf einmal"} />
        </div>
        <span className="hint">Findet kleine Gegenstände in hochauflösenden Bildern besser, kostet aber mehr Rechenzeit.</span>
      </div>

      <div className="field span-2" style={{ opacity: off ? 0.6 : 1 }}>
        <div className="row">
          <label className="label" style={{ flex: 1 }}>
            Gegenstände{" "}
            <span className="muted">
              {sel.length === 0 ? `– alle ${vocab.length} aus dem Vokabular` : `– ${sel.length} von ${vocab.length} ausgewählt`}
            </span>
          </label>
          {sel.length > 0 && <button type="button" className="btn btn-ghost btn-sm" onClick={() => set("inventory_labels", [])}>Alle (Auswahl leeren)</button>}
        </div>
        <span className="hint">Keine Auswahl = gesamtes Vokabular (Administration → Einstellungen). Weniger Gegenstände = schneller und weniger Fehlerkennungen.</span>
        {settings.isLoading ? <div className="muted">Lade Vokabular …</div> : vocab.length === 0 ? (
          <div className="muted">Das Vokabular ist leer – unter Einstellungen Gegenstände anlegen.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 6 }}>
            {groups.map(([g, entries]) => {
              const allOn = entries.every((e) => sel.includes(e.label));
              return (
                <div key={g}>
                  <div className="row" style={{ marginBottom: 6 }}>
                    <span className="section-title" style={{ margin: 0 }}>{g}</span>
                    <button type="button" className="btn btn-ghost btn-sm" style={{ height: 22, fontSize: 11 }} onClick={() => setGroup(entries, !allOn)}>
                      {allOn ? "Gruppe abwählen" : "Gruppe wählen"}
                    </button>
                  </div>
                  <div className="chips">
                    {entries.map((e) => {
                      const on = sel.includes(e.label);
                      return (
                        <button type="button" key={e.label} className={`chip ${on ? "on" : ""}`} title={e.prompts.join(", ")} onClick={() => toggle(e.label)}>
                          <Package size={12} />{e.name_de}{on && <Check size={13} />}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {unknown.length > 0 && (
          <div className="test-result bad" style={{ marginTop: 8 }}>
            Nicht mehr im Vokabular: {unknown.join(", ")}
            <button type="button" className="btn btn-sm" style={{ marginLeft: "auto" }}
              onClick={() => set("inventory_labels", sel.filter((l) => known.has(l)))}>Entfernen</button>
          </div>
        )}
      </div>
    </div>
  );
}
