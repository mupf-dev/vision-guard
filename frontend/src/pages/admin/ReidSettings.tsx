import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Footprints, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import { toast } from "../../lib/store";
import type { Settings } from "../../lib/types";
import { Range, Switch } from "../../components/ui";
import { fmtNum, msgs, useMsgs } from "../../i18n";

/** Einstellungen → Wiedererkennung über Kameras (W4 C2). Schalter je Kamera in der Kameraverwaltung. */
export function ReidSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const qc = useQueryClient();
  const m = useMsgs().settings.reid;
  const st = useQuery({ queryKey: ["presence", "status"], queryFn: api.presenceStatus, refetchInterval: 15_000 });
  const clear = useMutation({
    mutationFn: api.clearPresence,
    onSuccess: () => {
      toast.success(msgs().settings.reid.toastCleared, msgs().settings.reid.toastClearedText);
      qc.invalidateQueries({ queryKey: ["presence"] });
    },
    onError: (e: Error) => toast.error(msgs().settings.reid.toastClearFailed, e.message),
  });
  const s = st.data;
  return (
    <div className="card">
      <div className="card-head"><Footprints size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.ttl}</label>
          <Range value={f.reid_ttl_min} min={5} max={720} step={5} format={(v) => (v >= 60 ? `${fmtNum(v / 60, v % 60 ? 1 : 0)} h` : `${v} min`)}
            onChange={(v) => patch({ reid_ttl_min: v })} />
          <span className="hint muted" style={{ fontSize: 12 }}>{m.ttlHint}</span>
        </div>
        <div className="field"><label>{m.threshold}</label>
          <Range value={f.reid_match_threshold} min={0.5} max={0.9} step={0.01} format={(v) => fmtNum(v, 2)}
            onChange={(v) => patch({ reid_match_threshold: v })} />
          <span className="hint muted" style={{ fontSize: 12 }}>
            {m.thresholdHint(fmtNum(0.7, 2))}
          </span>
        </div>
        <div className="field"><label>{m.assign}</label>
          <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.reid_assign_identity} onChange={(v) => patch({ reid_assign_identity: v })}
              label={m.assignLabel(fmtNum(f.reid_match_threshold + 0.1, 2))} />
          </div>
          <span className="hint muted" style={{ fontSize: 12 }}>
            {m.assignHint}
          </span>
        </div>
        <div className="field"><label>{m.privacy}</label>
          <span className="hint muted" style={{ fontSize: 12 }}>
            {m.privacyHint}
          </span>
          <div className="row-wrap" style={{ gap: 12, alignItems: "center", marginTop: 6 }}>
            {s && <span className="dim" style={{ fontSize: 12 }}>
              {m.status(s.entries, s.active, s.embeddings, s.linked)}
            </span>}
            <button type="button" className="btn btn-sm" disabled={clear.isPending || !s?.entries} onClick={() => clear.mutate()}>
              <Trash2 size={14} /> {m.clearNow}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
