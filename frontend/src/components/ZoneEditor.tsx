import { useEffect, useRef, useState } from "react";
import { MousePointer2, PenLine, Plus, RefreshCw, Trash2, Undo2, Check } from "lucide-react";
import type { Zone } from "../lib/types";
import { randomId } from "../lib/format";
import { api } from "../lib/api";
import { isMock, mockSnapshot } from "../lib/mock";

const COLORS = ["#ff9800", "#22c55e", "#3b9cff", "#e879f9", "#ef4444", "#facc15", "#2dd4bf"];

/**
 * Polygon-Editor auf dem Kamera-Standbild. Koordinaten normiert 0..1.
 * Zeichnen: Klicks setzen Punkte, Klick auf den ersten Punkt / Doppelklick / Enter schließt.
 * Bearbeiten: Punkte ziehen, Doppelklick auf Kante fügt Punkt ein, Rechtsklick auf Punkt entfernt ihn.
 */
export function ZoneEditor({ cameraId, zones, onChange }: { cameraId: number | null; zones: Zone[]; onChange: (z: Zone[]) => void }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [sel, setSel] = useState<string | null>(zones[0]?.id ?? null);
  const [draft, setDraft] = useState<[number, number][] | null>(null);
  const [hover, setHover] = useState<[number, number] | null>(null);
  const [drag, setDrag] = useState<{ zone: string; idx: number } | null>(null);
  const [bust, setBust] = useState(Date.now());
  const [imgErr, setImgErr] = useState(false);
  const [imgLoading, setImgLoading] = useState(true);
  const [size, setSize] = useState({ w: 1, h: 1 });

  const imgSrc = cameraId === null ? null : isMock ? mockSnapshot(cameraId) : api.snapshotUrl(cameraId, bust);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth || 1, h: el.clientHeight || 1 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pt = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = svgRef.current!.getBoundingClientRect();
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  };
  const round = (p: [number, number]): [number, number] => [Math.round(p[0] * 10000) / 10000, Math.round(p[1] * 10000) / 10000];

  const update = (id: string, patch: Partial<Zone>) => onChange(zones.map((z) => (z.id === id ? { ...z, ...patch } : z)));

  const finishDraft = () => {
    if (!draft || draft.length < 3) return;
    const id = randomId("z");
    const z: Zone = { id, name: `Zone ${zones.length + 1}`, color: COLORS[zones.length % COLORS.length], polygon: draft.map(round) };
    onChange([...zones, z]);
    setSel(id);
    setDraft(null);
  };

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (!draft) return;
      if (e.key === "Enter") { e.preventDefault(); finishDraft(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); setDraft(null); }
      if (e.key === "Backspace") { e.preventDefault(); setDraft((d) => (d && d.length ? d.slice(0, -1) : d)); }
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  });

  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      const p = round(pt(e));
      onChange(zones.map((z) => (z.id === drag.zone ? { ...z, polygon: z.polygon.map((q, i) => (i === drag.idx ? p : q)) } : z)));
    };
    const up = () => setDrag(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  }, [drag, zones, onChange]);

  const onStageClick = (e: React.MouseEvent) => {
    if (!draft) return;
    const p = pt(e);
    if (draft.length >= 3) {
      const [fx, fy] = draft[0];
      if (Math.hypot((fx - p[0]) * size.w, (fy - p[1]) * size.h) < 12) { finishDraft(); return; }
    }
    setDraft([...draft, p]);
  };

  const insertPoint = (z: Zone, i: number, e: React.MouseEvent) => {
    e.stopPropagation();
    const p = round(pt(e));
    const poly = [...z.polygon];
    poly.splice(i + 1, 0, p);
    update(z.id, { polygon: poly });
  };

  const W = size.w, H = size.h;
  const toPts = (poly: [number, number][]) => poly.map(([x, y]) => `${x * W},${y * H}`).join(" ");
  const selected = zones.find((z) => z.id === sel);

  return (
    <div className="zone-editor">
      <div>
        <div className="ze-stage">
          {imgSrc && !imgErr ? <img src={imgSrc} alt="Kamerabild" draggable={false} style={imgLoading ? { aspectRatio: "16/9" } : undefined}
            onLoad={() => setImgLoading(false)} onError={() => { setImgErr(true); setImgLoading(false); }} />
            : <div style={{ aspectRatio: "16/9", display: "grid", placeItems: "center", color: "var(--text-3)", background: "repeating-linear-gradient(45deg,#070a0f 0 12px,#0a0e15 12px 24px)" }}>
              {cameraId === null ? "Standbild nach dem ersten Speichern verfügbar" : "Kein Standbild verfügbar"}</div>}
          <svg ref={svgRef} className={draft ? "drawing" : ""} onClick={onStageClick}
            onDoubleClick={(e) => { if (draft) { e.preventDefault(); finishDraft(); } }}
            onMouseMove={(e) => draft && setHover(pt(e))} onMouseLeave={() => setHover(null)}>
            {zones.map((z) => {
              const on = z.id === sel && !draft;
              return (
                <g key={z.id} onClick={(e) => { if (!draft) { e.stopPropagation(); setSel(z.id); } }} style={{ cursor: draft ? "crosshair" : "pointer" }}>
                  <polygon points={toPts(z.polygon)} fill={z.color} fillOpacity={on ? 0.25 : 0.12} stroke={z.color} strokeWidth={on ? 2.5 : 1.5} strokeDasharray={on ? undefined : "6 4"} />
                  {on && z.polygon.map((p, i) => {
                    const q = z.polygon[(i + 1) % z.polygon.length];
                    return <line key={`e${i}`} x1={p[0] * W} y1={p[1] * H} x2={q[0] * W} y2={q[1] * H} stroke="transparent" strokeWidth={12}
                      style={{ cursor: "copy" }} onDoubleClick={(e) => insertPoint(z, i, e)} />;
                  })}
                  {z.polygon[0] && (
                    <text x={Math.min(...z.polygon.map((p) => p[0])) * W + 6} y={Math.min(...z.polygon.map((p) => p[1])) * H + 18}
                      fill="#fff" fontSize={13} fontWeight={600} stroke="#000" strokeWidth={3} style={{ paintOrder: "stroke", pointerEvents: "none" }}>{z.name}</text>
                  )}
                  {on && z.polygon.map(([x, y], i) => (
                    <circle key={i} className="vertex" cx={x * W} cy={y * H} r={7} fill="#05080d" stroke={z.color} strokeWidth={2.5}
                      onPointerDown={(e) => { e.stopPropagation(); e.preventDefault(); setDrag({ zone: z.id, idx: i }); }}
                      onContextMenu={(e) => { e.preventDefault(); if (z.polygon.length > 3) update(z.id, { polygon: z.polygon.filter((_, k) => k !== i) }); }} />
                  ))}
                </g>
              );
            })}
            {draft && (
              <g pointerEvents="none">
                <polyline points={toPts(hover ? [...draft, hover] : draft)} fill="rgba(45,212,191,.12)" stroke="#2dd4bf" strokeWidth={2} />
                {draft.map(([x, y], i) => <circle key={i} cx={x * W} cy={y * H} r={i === 0 && draft.length >= 3 ? 9 : 5} fill={i === 0 ? "#2dd4bf" : "#05080d"} stroke="#2dd4bf" strokeWidth={2} />)}
              </g>
            )}
          </svg>
          {imgLoading && imgSrc && !imgErr && <div className="player-state"><div className="inner"><span className="spinner" />Lade Standbild …</div></div>}
          <div className="ze-hint">
            {draft ? `Punkte setzen (${draft.length}) · Klick auf ersten Punkt / Doppelklick / Enter = fertig · Backspace = zurück · Esc = abbrechen`
              : "Punkte ziehen · Doppelklick auf Kante = Punkt einfügen · Rechtsklick = Punkt entfernen"}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="row">
          {draft ? <>
            <button type="button" className="btn btn-primary btn-sm" disabled={draft.length < 3} onClick={finishDraft}><Check size={14} />Fertig</button>
            <button type="button" className="btn btn-sm" onClick={() => setDraft((d) => d?.slice(0, -1) ?? null)}><Undo2 size={14} /></button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(null)}>Abbrechen</button>
          </> : <button type="button" className="btn btn-sm btn-primary" onClick={() => { setDraft([]); setSel(null); }}><Plus size={14} />Neue Zone zeichnen</button>}
          <span className="spacer" />
          {cameraId !== null && <button type="button" className="btn btn-ghost btn-icon btn-sm" title="Standbild neu laden" onClick={() => { setImgErr(false); setImgLoading(true); setBust(Date.now()); }}><RefreshCw size={14} /></button>}
        </div>
        {zones.length === 0 && !draft && <div className="muted" style={{ fontSize: 13 }}>Keine Zonen. Ohne Zonen gilt das ganze Bild.</div>}
        {zones.map((z) => (
          <div key={z.id} className={`zone-row ${z.id === sel ? "sel" : ""}`} onClick={() => setSel(z.id)}>
            <input type="color" value={z.color} onChange={(e) => update(z.id, { color: e.target.value })} onClick={(e) => e.stopPropagation()} />
            <input className="input input-sm" value={z.name} onChange={(e) => update(z.id, { name: e.target.value })} />
            <button type="button" className="btn btn-ghost btn-icon btn-sm" title="Zone löschen" onClick={(e) => { e.stopPropagation(); onChange(zones.filter((x) => x.id !== z.id)); if (sel === z.id) setSel(null); }}><Trash2 size={14} /></button>
          </div>
        ))}
        {selected && !draft && <div className="muted" style={{ fontSize: 12 }}><MousePointer2 size={12} style={{ verticalAlign: -2 }} /> {selected.polygon.length} Punkte · ID <code>{selected.id}</code></div>}
        {draft && <div className="muted" style={{ fontSize: 12 }}><PenLine size={12} style={{ verticalAlign: -2 }} /> Zeichenmodus aktiv</div>}
      </div>
    </div>
  );
}
