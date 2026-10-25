import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Footprints, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import { toast } from "../../lib/store";
import type { Settings } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

/** Einstellungen → Wiedererkennung über Kameras (W4 C2). Schalter je Kamera in der Kameraverwaltung. */
export function ReidSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["presence", "status"], queryFn: api.presenceStatus, refetchInterval: 15_000 });
  const clear = useMutation({
    mutationFn: api.clearPresence,
    onSuccess: () => {
      toast.success("Wiedererkennung geleert", "Alle Merkmale und Verknüpfungen wurden verworfen.");
      qc.invalidateQueries({ queryKey: ["presence"] });
    },
    onError: (e: Error) => toast.error("Leeren fehlgeschlagen", e.message),
  });
  const s = st.data;
  return (
    <div className="card">
      <div className="card-head"><Footprints size={18} color="var(--accent)" /><h3>Wiedererkennung über Kameras</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Merkmale behalten</label>
          <Range value={f.reid_ttl_min} min={5} max={720} step={5} format={(v) => (v >= 60 ? `${(v / 60).toFixed(v % 60 ? 1 : 0)} h` : `${v} min`)}
            onChange={(v) => patch({ reid_ttl_min: v })} />
          <span className="hint muted" style={{ fontSize: 12 }}>So lange nach dem letzten Sehen bleibt eine Person verknüpfbar. Danach werden ihre Merkmale verworfen.</span>
        </div>
        <div className="field"><label>Schwelle für eine Verknüpfung</label>
          <Range value={f.reid_match_threshold} min={0.5} max={0.9} step={0.01} format={(v) => v.toFixed(2)}
            onChange={(v) => patch({ reid_match_threshold: v })} />
          <span className="hint muted" style={{ fontSize: 12 }}>
            Höher = weniger Verwechslungen, aber öfter „unbekannt“. Testmessung bei 4 Personen im Haushalt: 0,70 → 75 % richtig
            verknüpft, 2 % falsch, ein Fremder in 7 % der Fälle zugeordnet.
          </span>
        </div>
        <div className="field"><label>Namen übernehmen</label>
          <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.reid_assign_identity} onChange={(v) => patch({ reid_assign_identity: v })}
              label={`Erkanntes Gesicht an andere Kameras weitergeben (ab ${(f.reid_match_threshold + 0.1).toFixed(2)})`} />
          </div>
          <span className="hint muted" style={{ fontSize: 12 }}>
            Ereignisse ohne eigenes Gesicht bekommen dann den Namen der verknüpften Person. Regeln mit „nur bekannte/unbekannte
            Personen“ können dadurch anders auslösen – für Alarmregeln im Zweifel ausschalten.
          </span>
        </div>
        <div className="field"><label>Datenschutz</label>
          <span className="hint muted" style={{ fontSize: 12 }}>
            Die Merkmale (Kleidung/Statur) liegen nur im Arbeitsspeicher – nicht in der Datenbank, nicht auf der Festplatte.
            Ein Neustart des Backends leert alles. Einschalten je Kamera unter Kameras → „Personenanalyse“.
          </span>
          <div className="row-wrap" style={{ gap: 12, alignItems: "center", marginTop: 6 }}>
            {s && <span className="dim" style={{ fontSize: 12 }}>
              Aktuell {s.entries} {s.entries === 1 ? "Eintrag" : "Einträge"} ({s.active} sichtbar), {s.embeddings} Merkmale,
              {" "}{s.linked} Verknüpfungen seit dem Start
            </span>}
            <button type="button" className="btn btn-sm" disabled={clear.isPending || !s?.entries} onClick={() => clear.mutate()}>
              <Trash2 size={14} /> Jetzt leeren
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
