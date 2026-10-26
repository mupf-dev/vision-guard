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
import { CameraAskSection } from "./CameraAsk";
import { CameraHistorySection } from "./CameraHistory";
import { CameraPtzTab, PTZ_DEFAULT } from "./CameraPtz";
import { Move } from "lucide-react";
import { HealthBadge } from "../../components/HealthBadge";
import { StatusBadge } from "../Live";
import { toast, useLive } from "../../lib/store";
import { isMock, mockSnapshot } from "../../lib/mock";
import { useMsgs } from "../../i18n";

const EMPTY: CameraInput = {
  name: "", url: "", detect_url: null, enabled: true, detect_fps: 5, labels: ["person", "car", "dog", "cat"], min_score: 0.5,
  face_recognition: true, plate_recognition: true, pet_recognition: true, zones: [],
  room: null, inventory_enabled: false, inventory_interval_s: 3, inventory_labels: [], inventory_min_score: 0.35,
  inventory_tiling: false, health_enabled: true,
  pose_enabled: false, gesture_enabled: false, inventory_label_min_scores: {}, inventory_suppress: [],
  vlm_cloud_allowed: false, history_enabled: false, history_interval_s: 30, history_tiling: false,
  reid_enabled: false,
  ptz_enabled: false, ptz: PTZ_DEFAULT,
  face_fullres: true,
};

function strip(c: Camera): CameraInput {
  const { id: _id, status: _s, ptz_state: _p, ...rest } = c;
  void _id; void _s; void _p;
  return { ...EMPTY, ...rest };
}

/** W2 (C1/D1): Haltung/Sturz und Gesten je Kamera (Pose-Modell yolo11n-pose, nur bei Personen im Bild) */
function PersonAnalysis({ f, set }: { f: CameraInput; set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void }) {
  const sys = useQuery({ queryKey: ["system"], queryFn: api.system, staleTime: 10_000, enabled: f.pose_enabled || f.gesture_enabled || f.reid_enabled });
  const models = sys.data?.worker?.models as Record<string, string> | undefined;
  const model = models?.pose;
  const reidModel = models?.reid;
  const noPerson = !f.labels.includes("person");
  const m = useMsgs().cameras;
  return (
    <div className="field span-2"><label><PersonStanding size={13} style={{ verticalAlign: -2 }} /> {m.personAnalysis}</label>
      <div className="row-wrap" style={{ gap: 24 }}>
        <Switch checked={f.pose_enabled} onChange={(v) => set("pose_enabled", v)} label={m.poseSwitch} />
        <Switch checked={f.gesture_enabled} onChange={(v) => set("gesture_enabled", v)} label={m.gestureSwitch} />
        <Switch checked={f.reid_enabled} onChange={(v) => set("reid_enabled", v)} label={m.reidSwitch} />
      </div>
      <span className="hint muted" style={{ fontSize: 12 }}>
        {m.poseHint}
        {(f.pose_enabled || f.gesture_enabled) && model ? <> {m.model} <b>{model}</b>.</> : null}
      </span>
      {f.reid_enabled && (
        <span className="hint muted" style={{ fontSize: 12 }}>
          {m.reidHint}
          {reidModel ? <> {m.model} <b>{reidModel}</b>.</> : null}
        </span>
      )}
      {(f.pose_enabled || f.gesture_enabled || f.reid_enabled) && noPerson && (
        <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>{m.noPersonWarn}</span>
      )}
    </div>
  );
}

function CameraDialog({ cam, onClose }: { cam: Camera | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<CameraInput>(cam ? strip(cam) : EMPTY);
  const [tab, setTab] = useState<"general" | "zones" | "inventory" | "health" | "ptz">("general");
  const [test, setTest] = useState<{ url: string; res?: CameraTestResult; busy?: boolean; err?: string } | null>(null);
  const set = <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => setF((p) => ({ ...p, [k]: v }));
  const valid = f.name.trim() && f.url.trim();
  const msg = useMsgs();
  const m = msg.cameras;

  const save = useMutation({
    mutationFn: () => {
      const body = { ...f, name: f.name.trim(), url: f.url.trim(), detect_url: f.detect_url?.trim() || null };
      return cam ? api.updateCamera(cam.id, body) : api.createCamera(body);
    },
    onSuccess: () => { toast.success(cam ? m.saved : m.created, f.name); qc.invalidateQueries({ queryKey: ["cameras"] }); onClose(); },
    onError: (e: Error) => toast.error(m.saveFailed, e.message),
  });

  const runTest = async (url: string) => {
    if (!url.trim()) return;
    setTest({ url, busy: true });
    try { setTest({ url, res: await api.testCamera(url.trim()) }); }
    catch (e) { setTest({ url, err: (e as Error).message }); }
  };

  const TestRes = ({ url }: { url: string }) => test && test.url === url ? (
    test.busy ? <div className="test-result" style={{ background: "var(--bg-1)", border: "1px solid var(--line)" }}><span className="spinner" />{m.testing}</div>
      : test.res?.ok ? <div className="test-result ok"><CheckCircle2 size={16} />{m.connectionOk} · {test.res.width}×{test.res.height}{test.res.codec ? ` · ${test.res.codec}` : ""}</div>
      : <div className="test-result bad"><XCircle size={16} />{test.res?.error ?? test.err ?? m.failed}</div>
  ) : null;

  return (
    <Dialog size="xl" onClose={onClose} icon={<Cctv size={18} />} title={cam ? m.editTitle(cam.name) : m.addTitle} footer={<>
      <button className="btn btn-ghost" onClick={onClose}>{msg.common.cancel}</button>
      <button className="btn btn-primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{save.isPending && <span className="spinner" />}{msg.common.save}</button>
    </>}>
      <div className="tabs" style={{ marginTop: -8 }}>
        <button className={tab === "general" ? "on" : ""} onClick={() => setTab("general")}><Cctv size={15} />{m.tabGeneral}</button>
        <button className={tab === "zones" ? "on" : ""} onClick={() => setTab("zones")}><MapPin size={15} />{m.tabZones}<span className="count">{f.zones.length}</span></button>
        <button className={tab === "inventory" ? "on" : ""} onClick={() => setTab("inventory")}><Package size={15} />{m.tabItems}{f.inventory_enabled && <span className="count">{m.on}</span>}</button>
        <button className={tab === "health" ? "on" : ""} onClick={() => setTab("health")}><HeartPulse size={15} />{m.tabHealth}{cam?.status?.health?.state === "warn" && <span className="count" style={{ color: "var(--warn)" }}>!</span>}</button>
        <button className={tab === "ptz" ? "on" : ""} onClick={() => setTab("ptz")}><Move size={15} />{m.tabPtz}{f.ptz_enabled && <span className="count">{m.on}</span>}</button>
      </div>
      {tab === "general" ? (
        <div className="form-grid">
          <div className="field"><label>{m.nameLabel}</label><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder={m.namePlaceholder} autoFocus /></div>
          <div className="field"><label>{m.activeLabel}</label><div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.enabled} onChange={(v) => set("enabled", v)} label={f.enabled ? m.analysed : m.disabled} /></div></div>
          <div className="field span-2"><label>{m.streamUrl}</label>
            <div className="row">
              <input className="input mono" value={f.url} onChange={(e) => set("url", e.target.value)} placeholder={m.streamPlaceholder} />
              <button type="button" className="btn" onClick={() => runTest(f.url)} disabled={!f.url.trim()}><Plug size={16} />{m.testConnection}</button>
            </div>
            <TestRes url={f.url} />
          </div>
          <div className="field span-2"><label>{m.detectUrl}</label>
            <div className="row">
              <input className="input mono" value={f.detect_url ?? ""} onChange={(e) => set("detect_url", e.target.value || null)} placeholder={m.detectPlaceholder} />
              <button type="button" className="btn" onClick={() => runTest(f.detect_url ?? "")} disabled={!f.detect_url?.trim()}><Plug size={16} />{m.test}</button>
            </div>
            <TestRes url={f.detect_url ?? ""} />
          </div>
          <div className="field"><label>{m.fps}</label><Range value={f.detect_fps} min={1} max={15} step={1} onChange={(v) => set("detect_fps", v)} format={(v) => `${v} fps`} /></div>
          <div className="field"><label>{m.minScore}</label><Range value={f.min_score} min={0.1} max={0.95} step={0.05} onChange={(v) => set("min_score", v)} format={(v) => `${Math.round(v * 100)} %`} /></div>
          <div className="field span-2"><label>{m.objects}</label>
            <ChipSelect options={[...ALL_LABELS]} value={f.labels} onChange={(v) => set("labels", v)} color={labelColor}
              render={(l) => <span className="row" style={{ gap: 6 }}><LabelIcon label={l} />{labelName(l)}</span>} />
          </div>
          <div className="field span-2"><label>{m.recognition}</label>
            <div className="row-wrap" style={{ gap: 24 }}>
              <Switch checked={f.face_recognition} onChange={(v) => set("face_recognition", v)} label={m.faces} />
              <Switch checked={f.plate_recognition} onChange={(v) => set("plate_recognition", v)} label={m.plates} />
              <Switch checked={f.pet_recognition} onChange={(v) => set("pet_recognition", v)} label={m.pets} />
            </div>
          </div>
          <PersonAnalysis f={f} set={set} />
          <CameraAskSection f={f} set={set} />
          <CameraHistorySection f={f} set={set} />
        </div>
      ) : tab === "inventory" ? (
        <CameraInventoryTab f={f} set={set} cameraId={cam?.id} />
      ) : tab === "health" ? (
        <CameraHealthTab cam={cam} f={f} set={set} />
      ) : tab === "ptz" ? (
        <CameraPtzTab f={f} set={set} cameraId={cam?.id} savedEnabled={!!cam?.ptz_enabled} />
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
  const msg = useMsgs();
  const m = msg.cameras;
  const delM = useMutation({
    mutationFn: (id: number) => api.deleteCamera(id),
    onSuccess: () => { toast.success(m.deleted); qc.invalidateQueries({ queryKey: ["cameras"] }); setDel(null); },
    onError: (e: Error) => toast.error(m.deleteFailed, e.message),
  });
  const toggle = useMutation({
    mutationFn: (c: Camera) => api.updateCamera(c.id, { ...strip(c), enabled: !c.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
    onError: (e: Error) => toast.error(msg.common.error, e.message),
  });

  return (
    <>
      <div className="page-header">
        <div><h1>{m.title}</h1><div className="sub">{m.sub}</div></div>
        <button className="btn btn-primary" onClick={() => setEdit("new")}><Plus size={16} />{m.add}</button>
      </div>
      {cams.isLoading ? <Loading /> : !cams.data?.length ? (
        <div className="card"><Empty icon={<Cctv size={44} />} title={m.emptyTitle} text={m.emptyText} /></div>
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
                  {c.zones.length > 0 && <span className="badge"><MapPin size={11} />{m.zonesCount(c.zones.length)}</span>}
                  {c.face_recognition && <span className="badge info">{m.faces}</span>}
                  {c.plate_recognition && <span className="badge warn">{m.plates}</span>}
                  {c.pet_recognition && <span className="badge ok" style={{ animation: "none" }}>{m.animals}</span>}
                  {(c.pose_enabled || c.gesture_enabled || c.reid_enabled) && <span className="badge info" title={m.personAnalysis}><PersonStanding size={11} />{[c.pose_enabled && m.posture, c.gesture_enabled && m.gestures, c.reid_enabled && m.reid].filter(Boolean).join(" + ")}</span>}
                  {c.inventory_enabled && <span className="badge accent" title={m.inventoryTitle(c.inventory_interval_s, c.inventory_labels.length)}><Package size={11} />{m.items}</span>}
                  {c.ptz_enabled && <span className="badge info" title={m.ptzTitle}><Move size={11} />PTZ{c.ptz?.patrol?.enabled ? ` · ${m.patrol}` : ""}{c.ptz?.follow?.enabled ? ` · ${m.follow}` : ""}</span>}
                  {c.room && <span className="badge outline">{c.room}</span>}
                  <span className="muted" style={{ fontSize: 12 }}>{c.labels.map(labelName).join(", ")}</span>
                </div>
              </div>
              <Switch checked={c.enabled} onChange={() => toggle.mutate(c)} />
              <button className="btn btn-sm" onClick={() => setEdit(c)}><Pencil size={14} />{msg.common.edit}</button>
              <button className="btn btn-sm btn-ghost btn-icon" title={msg.common.delete} onClick={() => setDel(c)}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      )}
      {edit && <CameraDialog cam={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      {del && <ConfirmDialog title={m.deleteTitle} busy={delM.isPending} text={m.deleteText(del.name)}
        onConfirm={() => delM.mutate(del.id)} onClose={() => setDel(null)} />}
    </>
  );
}
