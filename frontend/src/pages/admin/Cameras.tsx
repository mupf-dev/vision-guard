import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cctv, CheckCircle2, MapPin, Pencil, Plug, Plus, Trash2, XCircle } from "lucide-react";
import { api } from "../../lib/api";
import type { Camera, CameraInput, CameraTestResult } from "../../lib/types";
import { ALL_LABELS, labelColor, labelName } from "../../lib/labels";
import { ChipSelect, ConfirmDialog, Dialog, Empty, Img, LabelIcon, Loading, Range, Switch } from "../../components/ui";
import { ZoneEditor } from "../../components/ZoneEditor";
import { CameraInventoryTab } from "./CameraInventory";
import { HeartPulse, Package, PersonStanding } from "lucide-react";
import { CameraHealthTab } from "./CameraHealth";
import { HealthBadge } from "../../components/HealthBadge";
import { StatusBadge } from "../Live";
import { toast, useLive } from "../../lib/store";
import { isMock, mockSnapshot } from "../../lib/mock";

const EMPTY: CameraInput = {
  name: "", url: "", detect_url: null, enabled: true, detect_fps: 5, labels: ["person", "car", "dog", "cat"], min_score: 0.5,
  face_recognition: true, plate_recognition: true, pet_recognition: true, zones: [],
  room: null, inventory_enabled: false, inventory_interval_s: 3, inventory_labels: [], inventory_min_score: 0.35,
  inventory_tiling: false, health_enabled: true,
  pose_enabled: false, gesture_enabled: false, inventory_label_min_scores: {}, inventory_suppress: [],
};

function strip(c: Camera): CameraInput {
  const { id: _id, status: _s, ...rest } = c;
  void _id; void _s;
  return { ...EMPTY, ...rest };
}

/** W2 (C1/D1): Haltung/Sturz und Gesten je Kamera (Pose-Modell yolo11n-pose, nur bei Personen im Bild) */
function PersonAnalysis({ f, set }: { f: CameraInput; set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void }) {
  const sys = useQuery({ queryKey: ["system"], queryFn: api.system, staleTime: 10_000, enabled: f.pose_enabled || f.gesture_enabled });
  const model = (sys.data?.worker?.models as Record<string, string> | undefined)?.pose;
  const noPerson = !f.labels.includes("person");
  return (
    <div className="field span-2"><label><PersonStanding size={13} style={{ verticalAlign: -2 }} /> Personenanalyse</label>
      <div className="row-wrap" style={{ gap: 24 }}>
        <Switch checked={f.pose_enabled} onChange={(v) => set("pose_enabled", v)} label="Haltung / Sturz erkennen" />
        <Switch checked={f.gesture_enabled} onChange={(v) => set("gesture_enabled", v)} label="Armgesten erkennen" />
      </div>
      <span className="hint muted" style={{ fontSize: 12 }}>
        Rechnet ein Pose-Modell (≈ 0,1 GB Grafikspeicher), nur solange eine Person im Bild ist: Haltung 2×/s, Gesten 5×/s.
        Auslösen über Regeln mit „Haltung“ bzw. „Geste“; Sofa/Bett als Zone anlegen und in der Regel ausschließen.
        {(f.pose_enabled || f.gesture_enabled) && model ? <> Modell: <b>{model}</b>.</> : null}
      </span>
      {(f.pose_enabled || f.gesture_enabled) && noPerson && (
        <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>„Person“ ist bei den erkannten Objekten nicht ausgewählt – ohne Personen-Tracks gibt es keine Pose.</span>
      )}
    </div>
  );
}

function CameraDialog({ cam, onClose }: { cam: Camera | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<CameraInput>(cam ? strip(cam) : EMPTY);
  const [tab, setTab] = useState<"general" | "zones" | "inventory" | "health">("general");
  const [test, setTest] = useState<{ url: string; res?: CameraTestResult; busy?: boolean; err?: string } | null>(null);
  const set = <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => setF((p) => ({ ...p, [k]: v }));
  const valid = f.name.trim() && f.url.trim();

  const save = useMutation({
    mutationFn: () => {
      const body = { ...f, name: f.name.trim(), url: f.url.trim(), detect_url: f.detect_url?.trim() || null };
      return cam ? api.updateCamera(cam.id, body) : api.createCamera(body);
    },
    onSuccess: () => { toast.success(cam ? "Kamera gespeichert" : "Kamera angelegt", f.name); qc.invalidateQueries({ queryKey: ["cameras"] }); onClose(); },
    onError: (e: Error) => toast.error("Speichern fehlgeschlagen", e.message),
  });

  const runTest = async (url: string) => {
    if (!url.trim()) return;
    setTest({ url, busy: true });
    try { setTest({ url, res: await api.testCamera(url.trim()) }); }
    catch (e) { setTest({ url, err: (e as Error).message }); }
  };

  const TestRes = ({ url }: { url: string }) => test && test.url === url ? (
    test.busy ? <div className="test-result" style={{ background: "var(--bg-1)", border: "1px solid var(--line)" }}><span className="spinner" />Teste Verbindung …</div>
      : test.res?.ok ? <div className="test-result ok"><CheckCircle2 size={16} />Verbindung OK · {test.res.width}×{test.res.height}{test.res.codec ? ` · ${test.res.codec}` : ""}</div>
      : <div className="test-result bad"><XCircle size={16} />{test.res?.error ?? test.err ?? "Fehlgeschlagen"}</div>
  ) : null;

  return (
    <Dialog size="xl" onClose={onClose} icon={<Cctv size={18} />} title={cam ? `Kamera bearbeiten – ${cam.name}` : "Kamera hinzufügen"} footer={<>
      <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
      <button className="btn btn-primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{save.isPending && <span className="spinner" />}Speichern</button>
    </>}>
      <div className="tabs" style={{ marginTop: -8 }}>
        <button className={tab === "general" ? "on" : ""} onClick={() => setTab("general")}><Cctv size={15} />Allgemein</button>
        <button className={tab === "zones" ? "on" : ""} onClick={() => setTab("zones")}><MapPin size={15} />Zonen<span className="count">{f.zones.length}</span></button>
        <button className={tab === "inventory" ? "on" : ""} onClick={() => setTab("inventory")}><Package size={15} />Gegenstände{f.inventory_enabled && <span className="count">an</span>}</button>
        <button className={tab === "health" ? "on" : ""} onClick={() => setTab("health")}><HeartPulse size={15} />Zustand{cam?.status?.health?.state === "warn" && <span className="count" style={{ color: "var(--warn)" }}>!</span>}</button>
      </div>
      {tab === "general" ? (
        <div className="form-grid">
          <div className="field"><label>Name *</label><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="z. B. Einfahrt" autoFocus /></div>
          <div className="field"><label>Aktiv</label><div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.enabled} onChange={(v) => set("enabled", v)} label={f.enabled ? "Kamera wird analysiert" : "deaktiviert"} /></div></div>
          <div className="field span-2"><label>Stream-URL (RTSP/HTTP/YouTube) *</label>
            <div className="row">
              <input className="input mono" value={f.url} onChange={(e) => set("url", e.target.value)} placeholder="rtsp://user:pass@192.168.1.10:554/stream1 oder YouTube-Live-Link" />
              <button type="button" className="btn" onClick={() => runTest(f.url)} disabled={!f.url.trim()}><Plug size={16} />Verbindung testen</button>
            </div>
            <TestRes url={f.url} />
          </div>
          <div className="field span-2"><label>Detect-URL (optional, Substream für die Analyse)</label>
            <div className="row">
              <input className="input mono" value={f.detect_url ?? ""} onChange={(e) => set("detect_url", e.target.value || null)} placeholder="rtsp://…/stream2 (leer = Hauptstream)" />
              <button type="button" className="btn" onClick={() => runTest(f.detect_url ?? "")} disabled={!f.detect_url?.trim()}><Plug size={16} />Testen</button>
            </div>
            <TestRes url={f.detect_url ?? ""} />
          </div>
          <div className="field"><label>Analyse-FPS</label><Range value={f.detect_fps} min={1} max={15} step={1} onChange={(v) => set("detect_fps", v)} format={(v) => `${v} fps`} /></div>
          <div className="field"><label>Mindest-Konfidenz</label><Range value={f.min_score} min={0.1} max={0.95} step={0.05} onChange={(v) => set("min_score", v)} format={(v) => `${Math.round(v * 100)} %`} /></div>
          <div className="field span-2"><label>Erkannte Objekte</label>
            <ChipSelect options={[...ALL_LABELS]} value={f.labels} onChange={(v) => set("labels", v)} color={labelColor}
              render={(l) => <span className="row" style={{ gap: 6 }}><LabelIcon label={l} />{labelName(l)}</span>} />
          </div>
          <div className="field span-2"><label>Wiedererkennung</label>
            <div className="row-wrap" style={{ gap: 24 }}>
              <Switch checked={f.face_recognition} onChange={(v) => set("face_recognition", v)} label="Gesichter" />
              <Switch checked={f.plate_recognition} onChange={(v) => set("plate_recognition", v)} label="Kennzeichen" />
              <Switch checked={f.pet_recognition} onChange={(v) => set("pet_recognition", v)} label="Haustiere" />
            </div>
          </div>
          <PersonAnalysis f={f} set={set} />
        </div>
      ) : tab === "inventory" ? (
        <CameraInventoryTab f={f} set={set} cameraId={cam?.id} />
      ) : tab === "health" ? (
        <CameraHealthTab cam={cam} f={f} set={set} />
      ) : (
        <ZoneEditor cameraId={cam?.id ?? null} zones={f.zones} onChange={(z) => set("zones", z)} />
      )}
    </Dialog>
  );
}

export function CamerasPage() {
  const qc = useQueryClient();
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const live = useLive((s) => s.status);
  const [edit, setEdit] = useState<Camera | null | "new">(null);
  const [del, setDel] = useState<Camera | null>(null);
  const delM = useMutation({
    mutationFn: (id: number) => api.deleteCamera(id),
    onSuccess: () => { toast.success("Kamera gelöscht"); qc.invalidateQueries({ queryKey: ["cameras"] }); setDel(null); },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });
  const toggle = useMutation({
    mutationFn: (c: Camera) => api.updateCamera(c.id, { ...strip(c), enabled: !c.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
    onError: (e: Error) => toast.error("Fehler", e.message),
  });

  return (
    <>
      <div className="page-header">
        <div><h1>Kameras</h1><div className="sub">Streams, Analyse-Parameter und Zonen</div></div>
        <button className="btn btn-primary" onClick={() => setEdit("new")}><Plus size={16} />Kamera hinzufügen</button>
      </div>
      {cams.isLoading ? <Loading /> : !cams.data?.length ? (
        <div className="card"><Empty icon={<Cctv size={44} />} title="Keine Kameras" text="Füge eine RTSP- oder HTTP-Kamera hinzu." /></div>
      ) : (
        <div className="list">
          {cams.data.map((c, i) => (
            <div key={c.id} className={`list-item ${c.enabled ? "" : "disabled"}`} style={{ animationDelay: `${i * 30}ms` }}>
              <Img src={isMock ? mockSnapshot(c.id) : api.snapshotUrl(c.id)} className="cam-thumb" fallback={<div className="cam-thumb" style={{ display: "grid", placeItems: "center", color: "var(--line-2)" }}><Cctv size={26} /></div>} />
              <div className="grow">
                <div className="title">{c.name}<StatusBadge status={live[c.id] ?? c.status} enabled={c.enabled} />{c.enabled && c.health_enabled && <HealthBadge health={(live[c.id] ?? c.status)?.health} />}<span className="badge outline mono">cam_{c.id}</span></div>
                <div className="sub mono">{c.url}</div>
                <div className="row-wrap" style={{ marginTop: 6, gap: 6 }}>
                  <span className="badge">{c.detect_fps} fps</span>
                  <span className="badge">≥ {Math.round(c.min_score * 100)} %</span>
                  {c.zones.length > 0 && <span className="badge"><MapPin size={11} />{c.zones.length} Zonen</span>}
                  {c.face_recognition && <span className="badge info">Gesichter</span>}
                  {c.plate_recognition && <span className="badge warn">Kennzeichen</span>}
                  {c.pet_recognition && <span className="badge ok" style={{ animation: "none" }}>Tiere</span>}
                  {(c.pose_enabled || c.gesture_enabled) && <span className="badge info" title="Personenanalyse (Pose)"><PersonStanding size={11} />{[c.pose_enabled && "Haltung", c.gesture_enabled && "Gesten"].filter(Boolean).join(" + ")}</span>}
                  {c.inventory_enabled && <span className="badge accent" title={`alle ${c.inventory_interval_s} s · ${c.inventory_labels.length ? `${c.inventory_labels.length} Gegenstände` : "gesamtes Vokabular"}`}><Package size={11} />Gegenstände</span>}
                  {c.room && <span className="badge outline">{c.room}</span>}
                  <span className="muted" style={{ fontSize: 12 }}>{c.labels.map(labelName).join(", ")}</span>
                </div>
              </div>
              <Switch checked={c.enabled} onChange={() => toggle.mutate(c)} />
              <button className="btn btn-sm" onClick={() => setEdit(c)}><Pencil size={14} />Bearbeiten</button>
              <button className="btn btn-sm btn-ghost btn-icon" title="Löschen" onClick={() => setDel(c)}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      )}
      {edit && <CameraDialog cam={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      {del && <ConfirmDialog title="Kamera löschen?" busy={delM.isPending} text={<>Kamera „{del.name}“ wird entfernt. Bestehende Ereignisse bleiben erhalten.</>}
        onConfirm={() => delM.mutate(del.id)} onClose={() => setDel(null)} />}
    </>
  );
}
