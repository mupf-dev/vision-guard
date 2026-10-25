import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, CheckCircle2, Crosshair, Home, Move, Plug, Plus, Route, Save, Trash2, XCircle } from "lucide-react";
import { api } from "../../lib/api";
import type { CameraInput, PtzConfig, PtzPreset, PtzProbeResult, PtzSchedule } from "../../lib/types";
import { DAYS, labelColor, labelName } from "../../lib/labels";
import { ChipSelect, LabelIcon, Range, Switch } from "../../components/ui";
import { toast } from "../../lib/store";

export const PTZ_DEFAULT: PtzConfig = {
  onvif_port: null, onvif_user: null, onvif_password: null, profile_token: null, home_preset: null,
  return_s: 60, home_on_start: true, speed: 0.5, invert_pan: false, invert_tilt: false,
  patrol: { enabled: false, steps: [], schedule: null, pause_s: 0 },
  follow: { enabled: false, labels: ["person"], schedule: null, lost_s: 8, max_s: 300, speed: 0.5, latency_s: 1.4, tilt: true },
};

function fmtSec(v: number): string {
  if (v <= 0) return "nie";
  if (v < 120) return `${v} s`;
  return `${Math.round(v / 60)} min`;
}

function ScheduleFields({ value, onChange }: { value: PtzSchedule | null; onChange: (v: PtzSchedule | null) => void }) {
  return (
    <div className="field span-2">
      <Switch checked={!!value} onChange={(v) => onChange(v ? { days: [0, 1, 2, 3, 4, 5, 6], start: "08:00", end: "18:00" } : null)} label="Nur zu bestimmten Zeiten" />
      {value && (
        <div className="row-wrap" style={{ gap: 10, marginTop: 8 }}>
          <ChipSelect options={[0, 1, 2, 3, 4, 5, 6]} value={value.days} onChange={(d) => onChange({ ...value, days: [...d].sort() })} render={(d) => DAYS[d]} />
          <input type="time" className="input input-sm" style={{ width: 110 }} value={value.start} onChange={(e) => onChange({ ...value, start: e.target.value })} aria-label="Von" />
          <span className="muted">bis</span>
          <input type="time" className="input input-sm" style={{ width: 110 }} value={value.end} onChange={(e) => onChange({ ...value, end: e.target.value })} aria-label="Bis" />
          {value.start > value.end && <span className="muted" style={{ fontSize: 12 }}>über Mitternacht</span>}
        </div>
      )}
    </div>
  );
}

/** Reiter „Schwenken“ im Kamera-Dialog (PTZ über ONVIF). */
export function CameraPtzTab({ f, set, cameraId, savedEnabled }: {
  f: CameraInput;
  set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void;
  cameraId?: number;
  /** PTZ ist gespeichert eingeschaltet (nur dann gibt es eine laufende Steuerung für Anfahren/Speichern) */
  savedEnabled: boolean;
}) {
  const qc = useQueryClient();
  const p: PtzConfig = { ...PTZ_DEFAULT, ...f.ptz, patrol: { ...PTZ_DEFAULT.patrol, ...f.ptz?.patrol }, follow: { ...PTZ_DEFAULT.follow, ...f.ptz?.follow } };
  const setP = (patch: Partial<PtzConfig>) => set("ptz", { ...p, ...patch });
  const [probe, setProbe] = useState<{ busy?: boolean; res?: PtzProbeResult; err?: string } | null>(null);
  const [newName, setNewName] = useState("");

  const info = useQuery({
    queryKey: ["ptz", cameraId], queryFn: () => api.ptz(cameraId!), enabled: !!cameraId && savedEnabled, staleTime: 10_000,
  });
  const live = !!cameraId && savedEnabled && !!info.data?.connected;
  const presets: PtzPreset[] = info.data?.presets?.length ? info.data.presets : probe?.res?.presets ?? [];
  const presetName = (t: string | null) => presets.find((x) => x.token === t)?.name ?? t ?? "–";

  const runProbe = async () => {
    if (!cameraId) return;
    setProbe({ busy: true });
    try {
      const res = await api.ptzProbe(cameraId, { onvif_port: p.onvif_port, onvif_user: p.onvif_user, onvif_password: p.onvif_password });
      setProbe({ res });
      if (res.ok && !p.onvif_port && res.port) setP({ onvif_port: res.port });
    } catch (e) {
      setProbe({ err: (e as Error).message });
    }
  };

  const refresh = () => qc.invalidateQueries({ queryKey: ["ptz", cameraId] });
  const fail = (e: unknown) => toast.error("PTZ", (e as Error).message);
  const saveM = useMutation({
    mutationFn: (name: string) => api.ptzSavePreset(cameraId!, name),
    onSuccess: (r) => {
      toast.success("Position gespeichert", r.name);
      setNewName("");
      if (!p.home_preset) setP({ home_preset: r.token });
      refresh();
    },
    onError: fail,
  });
  const delM = useMutation({ mutationFn: (t: string) => api.ptzDeletePreset(cameraId!, t), onSuccess: refresh, onError: fail });
  const gotoM = useMutation({ mutationFn: (t: string) => api.ptzGoto(cameraId!, t), onError: fail });

  const steps = p.patrol.steps;
  const setSteps = (s: typeof steps) => setP({ patrol: { ...p.patrol, steps: s } });

  if (!cameraId) {
    return <div className="muted" style={{ padding: "12px 0" }}>Kamera zuerst speichern – danach lässt sich die Schwenk-Steuerung (PTZ) einrichten.</div>;
  }

  return (
    <div className="form-grid">
      <div className="field span-2">
        <Switch checked={f.ptz_enabled} onChange={(v) => set("ptz_enabled", v)} label="Schwenken/Neigen über ONVIF steuern (PTZ)" />
        <span className="hint muted" style={{ fontSize: 12 }}>
          Zonen, Gegenstände, virtuelle Sensoren und die Bildprüfung gelten für die <b>Grundposition</b>. Schaut die Kamera
          woanders hin, pausieren sie; Personen, Tiere und Fahrzeuge werden weiter erkannt, Clips weiter aufgenommen.
        </span>
      </div>

      <div className="field"><label>ONVIF-Port</label>
        <input className="input" type="number" min={1} max={65535} value={p.onvif_port ?? ""} placeholder="automatisch (80, 8000, 2020 …)"
          onChange={(e) => setP({ onvif_port: e.target.value ? Number(e.target.value) : null })} />
      </div>
      <div className="field"><label>Verbindung</label>
        <button type="button" className="btn" onClick={runProbe} disabled={probe?.busy}>{probe?.busy ? <span className="spinner" /> : <Plug size={16} />}Verbindung prüfen</button>
      </div>
      <div className="field"><label>ONVIF-Benutzer (optional)</label>
        <input className="input" value={p.onvif_user ?? ""} placeholder="leer = aus der Stream-URL" autoComplete="off"
          onChange={(e) => setP({ onvif_user: e.target.value || null })} />
      </div>
      <div className="field"><label>ONVIF-Passwort (optional)</label>
        <input className="input" type="password" value={p.onvif_password ?? ""} placeholder="leer = aus der Stream-URL" autoComplete="new-password"
          onChange={(e) => setP({ onvif_password: e.target.value || null })} />
      </div>
      {probe && !probe.busy && (
        <div className="span-2">
          {probe.res?.ok ? (
            <div className="test-result ok"><CheckCircle2 size={16} />
              {[probe.res.device?.manufacturer, probe.res.device?.model].filter(Boolean).join(" ") || "Kamera"} · Port {probe.res.port}
              {" · "}{probe.res.capabilities?.zoom ? "Schwenken, Neigen, Zoom" : "Schwenken, Neigen"}
              {" · "}{probe.res.presets.length} gespeicherte Positionen
            </div>
          ) : <div className="test-result bad"><XCircle size={16} />{probe.res?.error ?? probe.err ?? "Fehlgeschlagen"}</div>}
        </div>
      )}
      {savedEnabled && info.data && !info.data.connected && (
        <div className="span-2 test-result bad"><XCircle size={16} />{info.data.error ?? "PTZ-Kamera nicht verbunden"}</div>
      )}

      <div className="field span-2"><label><Home size={13} style={{ verticalAlign: -2 }} /> Positionen</label>
        {!savedEnabled && <span className="hint muted" style={{ fontSize: 12 }}>PTZ einschalten und speichern – dann lassen sich Positionen anfahren und neue speichern.</span>}
        {presets.length === 0 ? <span className="muted" style={{ fontSize: 13 }}>Noch keine Positionen bekannt{savedEnabled ? "" : " („Verbindung prüfen“ liest sie aus)"}.</span> : (
          <div className="list" style={{ gap: 6 }}>
            {presets.map((x) => (
              <div key={x.token} className="list-item" style={{ padding: "6px 10px" }}>
                <div className="grow"><b>{x.name}</b> <span className="muted mono" style={{ fontSize: 11 }}>#{x.token}</span>
                  {x.token === p.home_preset && <span className="badge accent" style={{ marginLeft: 8 }}><Home size={11} />Grundposition</span>}
                </div>
                {live && <button type="button" className="btn btn-sm" onClick={() => gotoM.mutate(x.token)}><Move size={13} />Anfahren</button>}
                {x.token !== p.home_preset && <button type="button" className="btn btn-sm" onClick={() => setP({ home_preset: x.token })}><Home size={13} />Als Grundposition</button>}
                {live && x.token !== p.home_preset && <button type="button" className="btn btn-sm btn-ghost btn-icon" title="Position löschen" onClick={() => delM.mutate(x.token)}><Trash2 size={14} /></button>}
              </div>
            ))}
          </div>
        )}
        {live && (
          <div className="row" style={{ marginTop: 8 }}>
            <input className="input input-sm" value={newName} maxLength={40} placeholder="Name der aktuellen Position, z. B. Fenster" onChange={(e) => setNewName(e.target.value)} />
            <button type="button" className="btn btn-sm" disabled={!newName.trim() || saveM.isPending} onClick={() => saveM.mutate(newName.trim())}><Save size={13} />Aktuelle Position speichern</button>
          </div>
        )}
        {live && <span className="hint muted" style={{ fontSize: 12 }}>Ausrichtung im Live-Bild (Fokusansicht) mit dem Steuerkreuz einstellen und hier speichern.</span>}
        {!p.home_preset && f.ptz_enabled && <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>Keine Grundposition gewählt – nach dem ersten Schwenk pausieren Zonen, Gegenstände und Sensoren, bis eine festgelegt ist.</span>}
      </div>

      <div className="field"><label>Zurück zur Grundposition nach Handsteuerung</label>
        <Range value={p.return_s} min={0} max={600} step={10} onChange={(v) => setP({ return_s: v })} format={fmtSec} />
      </div>
      <div className="field"><label>Geschwindigkeit (Handsteuerung)</label>
        <Range value={p.speed} min={0.1} max={1} step={0.1} onChange={(v) => setP({ speed: v })} format={(v) => `${Math.round(v * 100)} %`} />
      </div>
      <div className="field span-2">
        <div className="row-wrap" style={{ gap: 24 }}>
          <Switch checked={p.home_on_start} onChange={(v) => setP({ home_on_start: v })} label="Beim Start zur Grundposition fahren" />
          <Switch checked={p.invert_pan} onChange={(v) => setP({ invert_pan: v })} label="Links/rechts vertauscht" />
          <Switch checked={p.invert_tilt} onChange={(v) => setP({ invert_tilt: v })} label="Oben/unten vertauscht" />
        </div>
      </div>

      <div className="field span-2"><label><Route size={13} style={{ verticalAlign: -2 }} /> Patrouille</label>
        <Switch checked={p.patrol.enabled} onChange={(v) => setP({ patrol: { ...p.patrol, enabled: v } })} label="Positionen nacheinander abfahren" />
        {p.patrol.enabled && (
          <>
            {steps.map((st, i) => (
              <div key={i} className="row" style={{ gap: 8, marginTop: 6 }}>
                <span className="muted" style={{ width: 18 }}>{i + 1}.</span>
                <select className="select input-sm" value={st.preset} onChange={(e) => setSteps(steps.map((x, j) => (j === i ? { ...x, preset: e.target.value } : x)))}>
                  {!presets.some((x) => x.token === st.preset) && <option value={st.preset}>{presetName(st.preset)}</option>}
                  {presets.map((x) => <option key={x.token} value={x.token}>{x.name}</option>)}
                </select>
                <input className="input input-sm" type="number" min={3} max={3600} style={{ width: 90 }} value={st.dwell_s} aria-label="Verweilen in Sekunden"
                  onChange={(e) => setSteps(steps.map((x, j) => (j === i ? { ...x, dwell_s: Math.max(3, Number(e.target.value) || 3) } : x)))} />
                <span className="muted" style={{ fontSize: 12 }}>s</span>
                <button type="button" className="btn btn-ghost btn-icon btn-sm" title="Nach oben" disabled={i === 0}
                  onClick={() => { const s = steps.slice(); [s[i - 1], s[i]] = [s[i], s[i - 1]]; setSteps(s); }}><ArrowUp size={14} /></button>
                <button type="button" className="btn btn-ghost btn-icon btn-sm" title="Nach unten" disabled={i === steps.length - 1}
                  onClick={() => { const s = steps.slice(); [s[i + 1], s[i]] = [s[i], s[i + 1]]; setSteps(s); }}><ArrowDown size={14} /></button>
                <button type="button" className="btn btn-ghost btn-icon btn-sm" title="Schritt entfernen" onClick={() => setSteps(steps.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
              </div>
            ))}
            <div className="row" style={{ marginTop: 8, gap: 8 }}>
              <button type="button" className="btn btn-sm" disabled={!presets.length || steps.length >= 32}
                onClick={() => setSteps([...steps, { preset: presets[0]?.token ?? "", dwell_s: 20 }])}><Plus size={13} />Schritt</button>
              {!presets.length && <span className="muted" style={{ fontSize: 12 }}>Erst Positionen speichern.</span>}
            </div>
            <div className="form-grid" style={{ marginTop: 10 }}>
              <div className="field"><label>Pause in der Grundposition zwischen zwei Runden</label>
                <Range value={p.patrol.pause_s} min={0} max={3600} step={30} onChange={(v) => setP({ patrol: { ...p.patrol, pause_s: v } })} format={(v) => (v ? fmtSec(v) : "keine")} />
              </div>
              <div />
              <ScheduleFields value={p.patrol.schedule} onChange={(v) => setP({ patrol: { ...p.patrol, schedule: v } })} />
            </div>
          </>
        )}
      </div>

      <div className="field span-2"><label><Crosshair size={13} style={{ verticalAlign: -2 }} /> Verfolgen</label>
        <Switch checked={p.follow.enabled} onChange={(v) => setP({ follow: { ...p.follow, enabled: v } })} label="Erkannte Objekte im Bild mittig halten" />
        {p.follow.enabled && (
          <>
            <span className="hint muted" style={{ fontSize: 12 }}>
              Startet, sobald ein gewähltes Objekt ≥ 1 s im Bild ist; unterbricht die Patrouille. Ist das Ziel weg, fährt die
              Kamera zurück zur Grundposition. Bewegt sich in kurzen Schritten und wartet nach jedem Schritt auf ein neues
              Bild; Schrittweite und Richtung stellt sie selbst nach. Schießt sie über das Ziel hinaus, die Bildverzögerung
              erhöhen.
            </span>
            <div className="form-grid" style={{ marginTop: 8 }}>
              <div className="field span-2"><label>Was verfolgen?</label>
                <ChipSelect options={f.labels.length ? f.labels : ["person"]} value={p.follow.labels} color={labelColor}
                  onChange={(v) => setP({ follow: { ...p.follow, labels: v.length ? v : ["person"] } })}
                  render={(l) => <span className="row" style={{ gap: 6 }}><LabelIcon label={l} />{labelName(l)}</span>} />
              </div>
              <div className="field"><label>Zurück, wenn das Ziel weg ist nach</label>
                <Range value={p.follow.lost_s} min={2} max={120} step={1} onChange={(v) => setP({ follow: { ...p.follow, lost_s: v } })} format={(v) => `${v} s`} />
              </div>
              <div className="field"><label>Höchstens verfolgen</label>
                <Range value={p.follow.max_s} min={10} max={3600} step={10} onChange={(v) => setP({ follow: { ...p.follow, max_s: v } })} format={fmtSec} />
              </div>
              <div className="field"><label>Bildverzögerung (warten nach jedem Schritt)</label>
                <Range value={p.follow.latency_s} min={0.3} max={5} step={0.1} onChange={(v) => setP({ follow: { ...p.follow, latency_s: v } })} format={(v) => `${v.toFixed(1)} s`} />
              </div>
              <div className="field"><label>Geschwindigkeit</label>
                <Range value={p.follow.speed} min={0.1} max={1} step={0.1} onChange={(v) => setP({ follow: { ...p.follow, speed: v } })} format={(v) => `${Math.round(v * 100)} %`} />
                <span className="hint muted" style={{ fontSize: 12 }}>Manche Kameras (z. B. Reolink E1 Pro) fahren immer gleich schnell.</span>
              </div>
              <div className="field span-2"><Switch checked={p.follow.tilt} onChange={(v) => setP({ follow: { ...p.follow, tilt: v } })} label="Auch neigen (aus = nur seitlich schwenken)" /></div>
              <ScheduleFields value={p.follow.schedule} onChange={(v) => setP({ follow: { ...p.follow, schedule: v } })} />
            </div>
          </>
        )}
      </div>
      {f.ptz_enabled && (p.patrol.enabled || p.follow.enabled) && !p.home_preset && (
        <div className="span-2 hint" style={{ fontSize: 12, color: "var(--warn)" }}>Patrouille und Verfolgen brauchen eine Grundposition, zu der die Kamera zurückkehrt.</div>
      )}
      {p.home_preset && <div className="span-2 muted" style={{ fontSize: 12 }}>Grundposition: <b>{presetName(p.home_preset)}</b></div>}
    </div>
  );
}
