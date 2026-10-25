import { Clock } from "lucide-react";
import type { CameraInput } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

/** W3 (B1): Zeitmaschine je Kamera (Gerüst K3, Ausbau B1). Kein Video – nur Bild-Embeddings + Mini-Vorschau. */
export function CameraHistorySection({ f, set }: { f: CameraInput; set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void }) {
  return (
    <div className="field span-2"><label><Clock size={13} style={{ verticalAlign: -2 }} /> Zeitmaschine</label>
      <div className="row-wrap" style={{ gap: 24 }}>
        <Switch checked={f.history_enabled} onChange={(v) => set("history_enabled", v)} label="Verlauf durchsuchbar machen" />
        {f.history_enabled && (
          <Switch checked={f.history_tiling} onChange={(v) => set("history_tiling", v)} label="Kacheln (kleine Dinge, 5× Speicher)" />
        )}
      </div>
      {f.history_enabled && (
        <div style={{ maxWidth: 360 }}>
          <Range value={f.history_interval_s} min={5} max={600} step={5} format={(v) => `alle ${v} s`}
            onChange={(v) => set("history_interval_s", v)} />
        </div>
      )}
      <span className="hint muted" style={{ fontSize: 12 }}>
        Speichert in festen Abständen eine Bildbeschreibung (Vektor) und ein kleines Vorschaubild, keine Videos.
        Suche unter „Zeitmaschine“. Unveränderte Bilder verlängern nur den letzten Eintrag.
      </span>
    </div>
  );
}
