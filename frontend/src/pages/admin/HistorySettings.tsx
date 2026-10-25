import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { Settings } from "../../lib/types";
import { fmtShort } from "../../lib/format";
import { toast } from "../../lib/store";
import { ConfirmDialog, Range } from "../../components/ui";

function fmtBytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  return `${(n / 1024 ** 3).toFixed(2).replace(".", ",")} GB`;
}

/** Einstellungen → Zeitmaschine (W3/B1): Aufbewahrung, Änderungsschwelle, Modell, Speicheranzeige, „Verlauf löschen“. */
export function HistorySettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const qc = useQueryClient();
  const stats = useQuery({ queryKey: ["history", "stats"], queryFn: api.historyStats });
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [confirm, setConfirm] = useState(false);
  const del = useMutation({
    mutationFn: () => api.deleteHistory(),
    onSuccess: (r) => {
      toast.success("Verlauf gelöscht", `${r.deleted} gespeicherte Bilder entfernt.`);
      qc.invalidateQueries({ queryKey: ["history"] });
      setConfirm(false);
    },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });
  const st = stats.data;
  const name = (id: string) => cams.data?.find((c) => String(c.id) === id)?.name ?? `Kamera ${id}`;
  return (
    <div className="card">
      <div className="card-head"><Clock size={18} color="var(--accent)" /><h3>Zeitmaschine</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Aufbewahrung</label>
          <Range value={f.retention_days_history} min={1} max={90} step={1} format={(v) => `${v} Tage`} onChange={(v) => patch({ retention_days_history: v })} />
        </div>
        <div className="field"><label>Änderungsschwelle (neues Bild ab)</label>
          <Range value={f.history_min_change} min={0} max={0.3} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => patch({ history_min_change: v })} />
          <span className="hint muted" style={{ fontSize: 12 }}>Kleiner = mehr gespeicherte Bilder. Bildrauschen liegt bei ≈ 0,002; Standard 0,05.</span>
        </div>
        <div className="field"><label>Bildmodell</label>
          <select className="select" value={f.history_model} onChange={(e) => patch({ history_model: e.target.value as Settings["history_model"] })}>
            <option value="siglip2-b16-256">SigLIP 2 B/16 (empfohlen, versteht Deutsch, ≈ 0,3 GB VRAM)</option>
            <option value="mobileclip2-s0">MobileCLIP2-S0 (sparsamer, nur Englisch, schwächer)</option>
          </select>
          {stats.data?.model && stats.data.model !== f.history_model && (
            <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>Nach dem Speichern sind bisher gespeicherte Bilder nicht mehr durchsuchbar (anderes Modell).</span>
          )}
        </div>
        <div className="field"><label>Gespeichert</label>
          {st && !st.enabled ? <span className="muted">Nicht verfügbar – Datenbank ohne pgvector.</span> : st ? (
            <div style={{ fontSize: 13 }}>
              <div>{st.frames} Bilder · {fmtBytes(st.thumb_bytes)} Vorschauen · {st.vectors} Vektoren</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                {st.oldest ? `ältestes ${fmtShort(st.oldest)}` : "noch nichts gespeichert"}
                {Object.keys(st.by_camera).length > 0 && ` · ${Object.entries(st.by_camera).map(([id, n]) => `${name(id)}: ${n}`).join(", ")}`}
              </div>
              <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 8, color: "var(--bad)" }} disabled={!st.frames}
                onClick={() => setConfirm(true)}><Trash2 size={14} />Verlauf löschen</button>
            </div>
          ) : <span className="muted">Lade …</span>}
        </div>
      </div>
      {confirm && (
        <ConfirmDialog title="Verlauf der Zeitmaschine löschen?" busy={del.isPending} onConfirm={() => del.mutate()} onClose={() => setConfirm(false)}
          text={<>Alle {st?.frames ?? 0} gespeicherten Bilder und Vektoren aller Kameras werden endgültig entfernt. Ereignisse, Clips und Gegenstände bleiben erhalten.</>} />
      )}
    </div>
  );
}
