import { MessageCircleQuestion } from "lucide-react";
import type { CameraInput } from "../../lib/types";
import { Switch } from "../../components/ui";
import { useMsgs } from "../../i18n";

/** W3 (A2): „Frag die Kamera“ – Cloud-Fallback je Kamera (Gerüst K3, Ausbau A2). */
export function CameraAskSection({ f, set }: { f: CameraInput; set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void }) {
  const m = useMsgs().cameras;
  return (
    <div className="field span-2"><label><MessageCircleQuestion size={13} style={{ verticalAlign: -2 }} /> {m.askTitle}</label>
      <Switch checked={f.vlm_cloud_allowed} onChange={(v) => set("vlm_cloud_allowed", v)}
        label={m.askCloud} />
      <span className="hint muted" style={{ fontSize: 12 }}>
        {m.askHint}
      </span>
      {f.vlm_cloud_allowed && (
        <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>{m.askWarn}</span>
      )}
    </div>
  );
}
