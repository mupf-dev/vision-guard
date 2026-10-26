import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, Crop, Cpu, MessageCircleQuestion, RefreshCw, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { AskEngine, AskResult, Camera } from "../lib/types";
import { Dialog } from "./ui";
import { fmtAgo } from "../lib/format";
import { fmtNum, msgs, useMsgs } from "../i18n";

type Region = [number, number, number, number];

/** Vorlagen: Beschriftung in der Sprache der Oberfläche, englische Frage (das lokale Modell versteht Englisch am besten). */
const yesNo = (v: string): { text: string; cls: string } | undefined => {
  const m = msgs().ui.ask;
  return v === "yes" ? { text: m.yes, cls: "ok" } : v === "no" ? { text: m.no, cls: "bad" } : undefined;
};

/** „Frag die Kamera“ (W3, A2) – Schaltfläche für die Live-Ansicht. */
export function AskCameraButton({ camera }: { camera: Camera }) {
  const [open, setOpen] = useState(false);
  const m = useMsgs().ui.ask;
  return (
    <>
      <button className="btn btn-sm" onClick={() => setOpen(true)} title={m.buttonTitle}>
        <MessageCircleQuestion size={15} />{m.button}
      </button>
      {open && <AskCameraDialog camera={camera} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Rechteck auf einem Bild aufziehen (normiert 0..1). */
function RegionPicker({ src, region, onChange, onLoadError }: {
  src: string; region: Region | null; onChange: (r: Region | null) => void; onLoadError?: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const start = useRef<[number, number] | null>(null);
  const [draft, setDraft] = useState<Region | null>(null);
  const pos = (e: React.PointerEvent): [number, number] => {
    const r = box.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const norm = (a: [number, number], b: [number, number]): Region =>
    [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  const shown = draft ?? region;
  const m = useMsgs().ui.ask;
  return (
    <div ref={box} style={{ position: "relative", borderRadius: 8, overflow: "hidden", background: "var(--bg-2, #000)", cursor: "crosshair", touchAction: "none", userSelect: "none" }}
      onPointerDown={(e) => { (e.target as Element).setPointerCapture?.(e.pointerId); start.current = pos(e); setDraft(null); }}
      onPointerMove={(e) => { if (start.current) setDraft(norm(start.current, pos(e))); }}
      onPointerUp={(e) => {
        if (!start.current) return;
        const r = norm(start.current, pos(e));
        start.current = null;
        setDraft(null);
        if (r[2] - r[0] > 0.03 && r[3] - r[1] > 0.03) onChange(r.map((v) => Math.round(v * 1000) / 1000) as Region);
      }}>
      <img src={src} alt={m.currentImage} draggable={false} onError={onLoadError} style={{ display: "block", width: "100%", height: "auto", pointerEvents: "none" }} />
      {shown && <RegionBox region={shown} />}
    </div>
  );
}

function RegionBox({ region }: { region: Region }) {
  const [x1, y1, x2, y2] = region;
  return (
    <div style={{ position: "absolute", left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%`,
      border: "2px solid var(--accent)", boxShadow: "0 0 0 9999px rgba(0,0,0,0.45)", borderRadius: 4, pointerEvents: "none" }} />
  );
}

function AnswerCard({ r }: { r: AskResult }) {
  const m = useMsgs().ui.ask;
  const yn = yesNo(r.yes_no);
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="card-body" style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 10 }}>
        <div className="row-wrap" style={{ gap: 8 }}>
          {yn && <span className={`badge ${yn.cls}`} style={{ fontSize: 14 }}>{yn.text}</span>}
          <b style={{ fontSize: 15 }}>{r.answer || m.noAnswer}</b>
        </div>
        <div className="row-wrap muted" style={{ gap: 8, fontSize: 12 }}>
          <span className="badge outline">{r.engine === "cloud" ? <><Cloud size={12} />{m.cloud}</> : <><Cpu size={12} />{m.local}</>}</span>
          <span>{r.model}</span><span>·</span><span>{m.seconds(fmtNum(r.ms / 1000, 1))}</span>
          <span>·</span><span>{m.question(r.question)}</span>
        </div>
        {r.snapshot_url && (
          <div style={{ position: "relative", borderRadius: 8, overflow: "hidden" }}>
            <img src={r.snapshot_url} alt={m.answeredImage} style={{ display: "block", width: "100%", height: "auto" }} />
            {r.region && <RegionBox region={r.region} />}
          </div>
        )}
      </div>
    </div>
  );
}

export function AskCameraDialog({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const qc = useQueryClient();
  const m = useMsgs().ui.ask;
  const [question, setQuestion] = useState("");
  const [region, setRegion] = useState<Region | null>(null);
  const [engine, setEngine] = useState<AskEngine>("auto");
  const [bust, setBust] = useState(() => Date.now());
  const [imgError, setImgError] = useState(false);
  const [res, setRes] = useState<AskResult | null>(null);
  const log = useQuery({ queryKey: ["ask-log", camera.id], queryFn: () => api.askLog({ camera_id: camera.id, limit: 8 }) });
  const ask = useMutation({
    mutationFn: () => api.askCamera(camera.id, { question: question.trim(), region, engine }),
    onSuccess: (r) => { setRes(r); qc.invalidateQueries({ queryKey: ["ask-log", camera.id] }); },
  });
  const del = useMutation({
    mutationFn: (id: number) => api.deleteAsk(id),
    onSuccess: (_, id) => { if (res?.id === id) setRes(null); qc.invalidateQueries({ queryKey: ["ask-log", camera.id] }); },
  });
  const submit = () => { if (question.trim() && !ask.isPending) ask.mutate(); };
  const german = /\b(ist|sind|gibt|der|die|das|und|nicht|brennt|liegt)\b/i.test(question);
  return (
    <Dialog title={m.dialogTitle(camera.name)} icon={<MessageCircleQuestion size={18} />} onClose={onClose} size="lg">
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 10 }}>
        <div className="row-wrap" style={{ gap: 8, justifyContent: "space-between" }}>
          <span className="hint muted" style={{ fontSize: 12 }}>
            <Crop size={12} style={{ verticalAlign: -2 }} /> {m.regionHint}
          </span>
          <span className="row-wrap" style={{ gap: 6 }}>
            {region && <button className="btn btn-ghost btn-sm" onClick={() => setRegion(null)}><X size={14} />{m.removeRegion}</button>}
            <button className="btn btn-ghost btn-sm" onClick={() => { setBust(Date.now()); setImgError(false); }} title={m.reloadImage}><RefreshCw size={14} />{m.reload}</button>
          </span>
        </div>
        {imgError
          ? <div className="hint" style={{ color: "var(--danger)" }}>{m.imageUnavailable}</div>
          : <RegionPicker src={api.snapshotUrl(camera.id, bust)} region={region} onChange={setRegion} onLoadError={() => setImgError(true)} />}
        <form className="row-wrap" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <input className="input" style={{ flex: 1, minWidth: 220 }} maxLength={300} value={question} autoFocus
            placeholder={m.placeholder} onChange={(e) => setQuestion(e.target.value)} />
          {camera.vlm_cloud_allowed && (
            <select className="input" style={{ width: "auto" }} value={engine} onChange={(e) => setEngine(e.target.value as AskEngine)} title={m.engineTitle}>
              <option value="auto">{m.engineAuto}</option>
              <option value="local">{m.engineLocal}</option>
              <option value="cloud">{m.engineCloud}</option>
            </select>
          )}
          <button className="btn btn-primary" type="submit" disabled={!question.trim() || ask.isPending}>{ask.isPending ? m.thinking : m.submit}</button>
        </form>
        <div className="row-wrap" style={{ gap: 6 }}>
          {m.templates.map((t) => <button key={t.q} className="btn btn-ghost btn-sm" title={t.q} onClick={() => setQuestion(t.q)}>{t.label}</button>)}
        </div>
        {german && <div className="hint muted" style={{ fontSize: 12 }}>{m.germanHint}</div>}
        {ask.isPending && <div className="hint muted">{m.loadingHint}</div>}
        {ask.isError && <div className="hint" style={{ color: "var(--danger)" }}>{(ask.error as Error).message}</div>}
        {res && <AnswerCard r={res} />}
        {(log.data?.items.length ?? 0) > 0 && (
          <div style={{ marginTop: 6 }}>
            <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>{m.recent}</div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 4 }}>
              {log.data!.items.map((a) => (
                <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", minWidth: 0 }} onClick={() => setRes(a)} title={m.showAnswer}>
                  {yesNo(a.yes_no) && <span className={`badge ${yesNo(a.yes_no)!.cls}`}>{yesNo(a.yes_no)!.text}</span>}
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    <b>{a.question}</b> – {a.answer}
                  </span>
                  <span className="muted" style={{ fontSize: 12 }}>{a.engine === "cloud" ? m.cloud : m.local} · {fmtAgo(a.ts)}</span>
                  <button className="btn btn-ghost btn-icon btn-sm" title={msgs().common.delete} onClick={(e) => { e.stopPropagation(); del.mutate(a.id); }}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Dialog>
  );
}
