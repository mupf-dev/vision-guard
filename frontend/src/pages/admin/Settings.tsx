import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Fingerprint, Link2, Radio, RotateCcw, Save } from "lucide-react";
import { api } from "../../lib/api";
import type { Settings } from "../../lib/types";
import { Loading, Range, Switch } from "../../components/ui";
import { toast } from "../../lib/store";
import { InventorySettingsCard } from "./InventorySettings";
import { NotificationSettingsCard, targetProblem } from "./NotificationSettings";
import { TvSettingsCard, tvProblem } from "./TvSettings";
import { LanguageSettingsCard } from "./LanguageSettings";
import { ReviewSettingsCard } from "./ReviewSettings";
import { PersonSettingsCard } from "./PersonSettings";
import { ReidSettingsCard } from "./ReidSettings";
import { PetSettingsCard } from "./PetSettings";
import { ReportSettingsCard } from "./ReportSettings";
import { AiSettingsCard } from "./AiSettings";
import { VlmSettingsCard } from "./VlmSettings";
import { SensorSettingsCard } from "./SensorSettings";
import { HistorySettingsCard } from "./HistorySettings";
import { FaceSettingsCard, NightlySettingsCard } from "./FaceSettings";

// R1: Sprungmarken der langen Einstellungsseite (Reihenfolge wie die Karten)
const SECTIONS: [string, string][] = [
  ["s-sprache", "Sprache"], ["s-aufbewahrung", "Aufbewahrung"], ["s-wiedererkennung", "Wiedererkennung"], ["s-gesichter", "Gesichter"], ["s-nachtlauf", "Nachtlauf"],
  ["s-gegenstaende", "Gegenstände"],
  ["s-benachrichtigungen", "Benachrichtigungen"], ["s-fernseher", "Fernseher"], ["s-rueckfragen", "Rückfragen"], ["s-haltung", "Haltung & Gesten"],
  ["s-reid", "Über Kameras"], ["s-tiere", "Tiere"], ["s-bericht", "Tagesbericht"], ["s-ki", "KI-Sprachmodell"],
  ["s-frag", "Frag die Kamera"], ["s-sensoren", "Sensoren"], ["s-zeitmaschine", "Zeitmaschine"], ["s-mqtt", "MQTT"],
  ["s-zugriff", "Externer Zugriff"],
];

export function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [f, setF] = useState<Settings | null>(null);
  useEffect(() => { if (q.data) setF(q.data); }, [q.data]);
  const save = useMutation({
    // weather_location null → {} (PUT ignoriert null-Werte, {} setzt zurück)
    // tv_stream_base_url null → "" (setzt zurück)
    mutationFn: (s: Settings) => api.saveSettings({ ...s, weather_location: s.weather_location ?? ({} as Settings["weather_location"]),
      tv_stream_base_url: s.tv_stream_base_url ?? "" }),
    onSuccess: (r) => { toast.success("Einstellungen gespeichert"); qc.setQueryData(["settings"], r ?? f); },
    onError: (e: Error) => toast.error("Speichern fehlgeschlagen", e.message),
  });
  const dirty = !!f && JSON.stringify(f) !== JSON.stringify(q.data);
  // R1: Warnung beim Schließen/Neuladen mit ungespeicherten Änderungen (BrowserRouter: kein useBlocker)
  useEffect(() => {
    if (!dirty) return;
    const onBefore = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", onBefore);
    return () => window.removeEventListener("beforeunload", onBefore);
  }, [dirty]);
  if (q.isLoading || !f) return <Loading />;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setF({ ...f, [k]: v });
  const invalid = (f.push_targets ?? []).some((t) => targetProblem(t) !== null)
    || (f.tv_targets ?? []).some((t) => tvProblem(t) !== null);

  return (
    <>
      <div className={`page-header settings-head${dirty ? " dirty" : ""}`}>
        <div><h1>Einstellungen</h1><div className="sub">{dirty ? "Ungespeicherte Änderungen" : "Erkennung, Benachrichtigungen, KI, Zugriff – Speichern gilt für alle Bereiche"}</div></div>
        <div className="row">
          {dirty && <button className="btn btn-ghost" onClick={() => setF(q.data!)}><RotateCcw size={16} />Verwerfen</button>}
          <button className="btn btn-primary" disabled={!dirty || invalid || save.isPending} title={invalid ? "Push-Ziel oder Fernseher unvollständig" : undefined} onClick={() => save.mutate(f)}>{save.isPending ? <span className="spinner" /> : <Save size={16} />}Speichern</button>
        </div>
      </div>
      <nav className="settings-toc" aria-label="Bereiche">
        {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{label}</a>)}
      </nav>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div id="s-sprache" className="settings-anchor">
          <LanguageSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div className="card" id="s-aufbewahrung">
          <div className="card-head"><Archive size={18} color="var(--accent)" /><h3>Aufbewahrung</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>Ereignisse aufbewahren</label>
              <div className="row"><input type="number" min={1} className="input" value={f.retention_days_events} onChange={(e) => set("retention_days_events", Number(e.target.value))} /><span className="muted">Tage</span></div>
            </div>
            <div className="field"><label>Medien (Snapshots, Clips) aufbewahren</label>
              <div className="row"><input type="number" min={1} className="input" value={f.retention_days_media} onChange={(e) => set("retention_days_media", Number(e.target.value))} /><span className="muted">Tage</span></div>
            </div>
          </div>
        </div>
        <div className="card" id="s-wiedererkennung">
          <div className="card-head"><Fingerprint size={18} color="var(--accent)" /><h3>Wiedererkennung (Gesicht, Kennzeichen, Tier)</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>Gesichts-Schwelle (Kosinus-Ähnlichkeit)</label>
              <Range value={f.face_match_threshold} min={0.2} max={0.9} step={0.01} onChange={(v) => set("face_match_threshold", v)} format={(v) => v.toFixed(2)} />
              <span className="hint">Höher = strenger, weniger Verwechslungen, aber mehr neue Identitäten.</span>
            </div>
            <div className="field"><label>Tier-Schwelle (Kosinus-Ähnlichkeit)</label>
              <Range value={f.pet_match_threshold} min={0.5} max={0.99} step={0.01} onChange={(v) => set("pet_match_threshold", v)} format={(v) => v.toFixed(2)} />
            </div>
            <div className="field"><label>Kennzeichen: max. Zeichen-Abweichung</label>
              <Range value={f.plate_max_distance} min={0} max={3} step={1} onChange={(v) => set("plate_max_distance", v)} format={(v) => `${v}`} />
              <span className="hint">Levenshtein-Distanz für OCR-Toleranz.</span>
            </div>
          </div>
        </div>
        <div id="s-gesichter" className="settings-anchor">
          <FaceSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-nachtlauf" className="settings-anchor">
          <NightlySettingsCard f={f} saved={q.data} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-gegenstaende" className="settings-anchor">
          <InventorySettingsCard f={f} saved={q.data} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-benachrichtigungen" className="settings-anchor">
          <NotificationSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-fernseher" className="settings-anchor">
          <TvSettingsCard f={f} saved={q.data} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        {/* W2: je Thema eine Karte (Fach-Agenten ändern nur ihre Datei) */}
        <div id="s-rueckfragen" className="settings-anchor">
          <ReviewSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-haltung" className="settings-anchor">
          <PersonSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-reid" className="settings-anchor">
          <ReidSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-tiere" className="settings-anchor">
          <PetSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-bericht" className="settings-anchor">
          <ReportSettingsCard f={f} saved={q.data} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-ki" className="settings-anchor">
          <AiSettingsCard f={f} saved={q.data} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-frag" className="settings-anchor">
          <VlmSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-sensoren" className="settings-anchor">
          <SensorSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div id="s-zeitmaschine" className="settings-anchor">
          <HistorySettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div className="card" id="s-mqtt">
          <div className="card-head"><Radio size={18} color="var(--accent)" /><h3>MQTT</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>Topic-Präfix</label><input className="input mono" value={f.mqtt_prefix} onChange={(e) => set("mqtt_prefix", e.target.value)} />
              <span className="hint">z. B. <code>{f.mqtt_prefix || "visionguard"}/events</code>, <code>{f.mqtt_prefix || "visionguard"}/einfahrt/person</code></span>
            </div>
            <div className="field"><label>Alle Ereignisse veröffentlichen</label>
              <div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.publish_all_events} onChange={(v) => set("publish_all_events", v)} label={<code>{f.mqtt_prefix}/events</code>} /></div>
            </div>
          </div>
        </div>
        <div className="card" id="s-zugriff">
          <div className="card-head"><Link2 size={18} color="var(--accent)" /><h3>Externer Zugriff &amp; Medien-Links</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>Öffentliche Adresse</label>
              <input className="input mono" value={f.public_base_url ?? ""} placeholder="https://vg.example.org"
                onChange={(e) => set("public_base_url", e.target.value)} />
              <span className="hint">Ohne „/“ am Ende. Wenn gesetzt, enthalten Webhooks, MQTT und MCP absolute Links auf Bilder und Clips.</span>
            </div>
            <div className="field"><label>Gültigkeit signierter Links</label>
              <div className="row"><input type="number" min={1} max={720} className="input" value={f.media_link_ttl_h}
                onChange={(e) => set("media_link_ttl_h", Number(e.target.value))} /><span className="muted">Stunden</span></div>
              <span className="hint">Bilder/Clips in Webhooks, MQTT und API-Antworten mit <code>signed=1</code> sind nur so lange ohne Anmeldung abrufbar.</span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
