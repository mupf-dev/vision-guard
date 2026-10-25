import { MessageCircleQuestion } from "lucide-react";
import type { CameraInput } from "../../lib/types";
import { Switch } from "../../components/ui";

/** W3 (A2): „Frag die Kamera“ – Cloud-Fallback je Kamera (Gerüst K3, Ausbau A2). */
export function CameraAskSection({ f, set }: { f: CameraInput; set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void }) {
  return (
    <div className="field span-2"><label><MessageCircleQuestion size={13} style={{ verticalAlign: -2 }} /> Frag die Kamera</label>
      <Switch checked={f.vlm_cloud_allowed} onChange={(v) => set("vlm_cloud_allowed", v)}
        label="Cloud-Fallback erlauben (Bild verlässt das Haus)" />
      <span className="hint muted" style={{ fontSize: 12 }}>
        Fragen beantwortet zuerst das lokale Bildmodell. Nur mit diesem Schalter und eingerichtetem Cloud-Modell
        (Einstellungen → Frag die Kamera) darf ein Standbild an einen externen Dienst gehen.
      </span>
      {f.vlm_cloud_allowed && (
        <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>Achtung: Bilder dieser Kamera können an einen Cloud-Anbieter gesendet werden.</span>
      )}
    </div>
  );
}
