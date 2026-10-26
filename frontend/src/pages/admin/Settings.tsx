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
import { fmtNum, msgs, useMsgs } from "../../i18n";

// R1: Sprungmarken der langen Einstellungsseite (Reihenfolge wie die Karten); Beschriftung aus i18n `settings.page.sections`
const SECTIONS: string[] = [
  "s-sprache", "s-aufbewahrung", "s-wiedererkennung", "s-gesichter", "s-nachtlauf", "s-gegenstaende",
  "s-benachrichtigungen", "s-fernseher", "s-rueckfragen", "s-haltung", "s-reid", "s-tiere", "s-bericht", "s-ki",
  "s-frag", "s-sensoren", "s-zeitmaschine", "s-mqtt", "s-zugriff",
];

export function SettingsPage() {
  const qc = useQueryClient();
  const all = useMsgs();
  const m = all.settings;
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [f, setF] = useState<Settings | null>(null);
  useEffect(() => { if (q.data) setF(q.data); }, [q.data]);
  const save = useMutation({
    // weather_location null → {} (PUT ignoriert null-Werte, {} setzt zurück)
    // tv_stream_base_url null → "" (setzt zurück)
    mutationFn: (s: Settings) => api.saveSettings({ ...s, weather_location: s.weather_location ?? ({} as Settings["weather_location"]),
      tv_stream_base_url: s.tv_stream_base_url ?? "" }),
    onSuccess: (r) => { toast.success(msgs().settings.page.toastSaved); qc.setQueryData(["settings"], r ?? f); },
    onError: (e: Error) => toast.error(msgs().settings.page.toastSaveFailed, e.message),
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
        <div><h1>{m.page.title}</h1><div className="sub">{dirty ? m.page.subDirty : m.page.sub}</div></div>
        <div className="row">
          {dirty && <button className="btn btn-ghost" onClick={() => setF(q.data!)}><RotateCcw size={16} />{m.page.discard}</button>}
          <button className="btn btn-primary" disabled={!dirty || invalid || save.isPending} title={invalid ? m.page.invalidTitle : undefined} onClick={() => save.mutate(f)}>{save.isPending ? <span className="spinner" /> : <Save size={16} />}{all.common.save}</button>
        </div>
      </div>
      <nav className="settings-toc" aria-label={m.page.tocLabel}>
        {SECTIONS.map((id) => <a key={id} href={`#${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{m.page.sections[id] ?? id}</a>)}
      </nav>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div id="s-sprache" className="settings-anchor">
          <LanguageSettingsCard f={f} patch={(p) => setF((prev) => (prev ? { ...prev, ...p } : prev))} />
        </div>
        <div className="card" id="s-aufbewahrung">
          <div className="card-head"><Archive size={18} color="var(--accent)" /><h3>{m.retention.title}</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>{m.retention.events}</label>
              <div className="row"><input type="number" min={1} className="input" value={f.retention_days_events} onChange={(e) => set("retention_days_events", Number(e.target.value))} /><span className="muted">{m.shared.daysUnit}</span></div>
            </div>
            <div className="field"><label>{m.retention.media}</label>
              <div className="row"><input type="number" min={1} className="input" value={f.retention_days_media} onChange={(e) => set("retention_days_media", Number(e.target.value))} /><span className="muted">{m.shared.daysUnit}</span></div>
            </div>
          </div>
        </div>
        <div className="card" id="s-wiedererkennung">
          <div className="card-head"><Fingerprint size={18} color="var(--accent)" /><h3>{m.recognition.title}</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>{m.recognition.faceThreshold}</label>
              <Range value={f.face_match_threshold} min={0.2} max={0.9} step={0.01} onChange={(v) => set("face_match_threshold", v)} format={(v) => fmtNum(v, 2)} />
              <span className="hint">{m.recognition.faceHint}</span>
            </div>
            <div className="field"><label>{m.recognition.petThreshold}</label>
              <Range value={f.pet_match_threshold} min={0.5} max={0.99} step={0.01} onChange={(v) => set("pet_match_threshold", v)} format={(v) => fmtNum(v, 2)} />
            </div>
            <div className="field"><label>{m.recognition.plateDistance}</label>
              <Range value={f.plate_max_distance} min={0} max={3} step={1} onChange={(v) => set("plate_max_distance", v)} format={(v) => `${v}`} />
              <span className="hint">{m.recognition.plateHint}</span>
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
            <div className="field"><label>{m.mqtt.prefix}</label><input className="input mono" value={f.mqtt_prefix} onChange={(e) => set("mqtt_prefix", e.target.value)} />
              <span className="hint">{m.mqtt.example} <code>{f.mqtt_prefix || "visionguard"}/events</code>, <code>{f.mqtt_prefix || "visionguard"}/{m.mqtt.exampleTopic}</code></span>
            </div>
            <div className="field"><label>{m.mqtt.publishAll}</label>
              <div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.publish_all_events} onChange={(v) => set("publish_all_events", v)} label={<code>{f.mqtt_prefix}/events</code>} /></div>
            </div>
          </div>
        </div>
        <div className="card" id="s-zugriff">
          <div className="card-head"><Link2 size={18} color="var(--accent)" /><h3>{m.access.title}</h3></div>
          <div className="card-body form-grid">
            <div className="field"><label>{m.access.publicUrl}</label>
              <input className="input mono" value={f.public_base_url ?? ""} placeholder="https://vg.example.org"
                onChange={(e) => set("public_base_url", e.target.value)} />
              <span className="hint">{m.access.publicUrlHint}</span>
            </div>
            <div className="field"><label>{m.access.linkTtl}</label>
              <div className="row"><input type="number" min={1} max={720} className="input" value={f.media_link_ttl_h}
                onChange={(e) => set("media_link_ttl_h", Number(e.target.value))} /><span className="muted">{m.shared.hoursUnit}</span></div>
              <span className="hint">{m.access.linkTtlHintPre} <code>signed=1</code> {m.access.linkTtlHintPost}</span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
