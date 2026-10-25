import { useEffect, useRef, useState } from "react";
import { Eye, Gauge, Image as ImageIcon, Lightbulb, Package, RefreshCw, Sparkles, Square, Waves, PenLine, Check, X } from "lucide-react";
import type { SensorKind, SensorState, VirtualSensor } from "../lib/types";
import { api } from "../lib/api";
import { isMock, mockSnapshot } from "../lib/mock";

/** W3 (A3+C4): gemeinsame Bausteine für Sensoren (Admin → Sensoren, Vorräte). */

export const SENSOR_KIND: Record<SensorKind, { name: string; icon: React.ReactNode; text: string }> = {
  brightness: { name: "Helligkeit", icon: <Lightbulb size={15} />, text: "Licht an/aus, Bildschirm an, Fenster hell – mittlere Helligkeit im Bereich (CPU, sehr günstig)." },
  blink: { name: "Blinken", icon: <Waves size={15} />, text: "Blinkende LED, flackernder Bildschirm – Helligkeitsschwankung über wenige Sekunden." },
  reference: { name: "Referenzbilder", icon: <ImageIcon size={15} />, text: "Vergleich mit aufgenommenen Bildern „an“ und „aus“ (z. B. Fenster offen/zu, Tür, Rollladen)." },
  inventory_count: { name: "Gegenstände zählen", icon: <Package size={15} />, text: "Zählt erkannte Gegenstände im Bereich (z. B. Flaschen im Regal). Braucht die Gegenstandserkennung der Kamera." },
  fill: { name: "Füllstand", icon: <Gauge size={15} />, text: "Vergleich mit Bildern „voll“, „halb“ und „leer“ (z. B. Obstschale, Futterbehälter, Holzstapel)." },
  vlm: { name: "Bildmodell", icon: <Sparkles size={15} />, text: "Ja/Nein-Frage an das Bildmodell („Is the stove on?“) – nur bei sichtbarer Änderung oder nach Ablauf der Höchstzeit." },
};

export const STATE_NAME: Record<SensorState, string> = { on: "an", off: "aus", ok: "ok", low: "knapp", out: "leer", unknown: "unklar" };
export const STATE_CLASS: Record<SensorState, string> = { on: "ok", off: "outline", ok: "ok", low: "warn", out: "bad", unknown: "" };
export const REF_NAME: Record<string, string> = { on: "an", off: "aus", full: "voll", half: "halb", empty: "leer" };

export function SensorStateBadge({ s }: { s: Pick<VirtualSensor, "state" | "enabled"> }) {
  if (!s.enabled) return <span className="badge">deaktiviert</span>;
  return <span className={`badge ${STATE_CLASS[s.state]}`}>{s.state !== "unknown" && s.state !== "off" && <span className="dot" />}{STATE_NAME[s.state]}</span>;
}

/** Messwert lesbar: Helligkeit/Füllstand in %, Blinken als Schwankung, Zählung als Anzahl, Referenz als P(an). */
export function fmtSensorValue(s: Pick<VirtualSensor, "kind" | "value">): string {
  const v = s.value;
  if (v === null || v === undefined) return "–";
  switch (s.kind) {
    case "brightness": return `Helligkeit ${Math.round(v * 100)} %`;
    case "blink": return `Schwankung ${v.toFixed(3)}`;
    case "reference": return `„an“ zu ${Math.round(v * 100)} %`;
    case "fill": return `Füllstand ${Math.round(v * 100)} %`;
    case "inventory_count": return `${Math.round(v)} Stück`;
    case "vlm": return v >= 0.5 ? "Antwort: ja" : "Antwort: nein";
  }
}

/** Bild mit eingezeichnetem Bereich (Referenzbilder, Standbilder) */
export function RegionImage({ src, region, height = 90 }: { src: string | null; region: [number, number][]; height?: number }) {
  const pts = region.map(([x, y]) => `${x * 100},${y * 100}`).join(" ");
  return (
    <div style={{ position: "relative", height, aspectRatio: "16/9", borderRadius: 8, overflow: "hidden", background: "#000", flex: "none" }}>
      {src && <img src={src} alt="" style={{ width: "100%", height: "100%", objectFit: "fill", display: "block" }} />}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <polygon points={pts} fill="rgba(45,212,191,.15)" stroke="#2dd4bf" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

/**
 * Bereichs-Editor für einen Sensor (ein Polygon, normiert 0..1) auf dem Kamera-Standbild.
 * Rechteck aufziehen (Standard), Polygon zeichnen, Punkte ziehen, Rechtsklick entfernt einen Punkt.
 */
export function RegionEditor({ cameraId, region, onChange }: { cameraId: number | null; region: [number, number][]; onChange: (r: [number, number][]) => void }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [mode, setMode] = useState<"idle" | "rect" | "poly">(region.length ? "idle" : "rect");
  const [draft, setDraft] = useState<[number, number][]>([]);
  const [rectStart, setRectStart] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<[number, number] | null>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const [bust, setBust] = useState(Date.now());
  const [imgErr, setImgErr] = useState(false);
  const [size, setSize] = useState({ w: 1, h: 1 });
  const imgSrc = cameraId === null ? null : isMock ? mockSnapshot(cameraId) : api.snapshotUrl(cameraId, bust);

  useEffect(() => { setImgErr(false); }, [cameraId]);
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth || 1, h: el.clientHeight || 1 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const pt = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = svgRef.current!.getBoundingClientRect();
    const c = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
    return [c((e.clientX - r.left) / r.width), c((e.clientY - r.top) / r.height)];
  };
  useEffect(() => {
    if (drag === null) return;
    const move = (e: PointerEvent) => { const p = pt(e); onChange(region.map((q, i) => (i === drag ? p : q))); };
    const up = () => setDrag(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  }, [drag, region, onChange]);
  const finishPoly = () => { if (draft.length >= 3) { onChange(draft); setDraft([]); setMode("idle"); } };
  useEffect(() => {
    if (mode !== "poly") return;
    const k = (e: KeyboardEvent) => {
      if (e.key === "Enter") { e.preventDefault(); finishPoly(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); setDraft([]); setMode(region.length ? "idle" : "rect"); }
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  });
  const W = size.w, H = size.h;
  const toPts = (poly: [number, number][]) => poly.map(([x, y]) => `${x * W},${y * H}`).join(" ");
  const rectPoly = (a: [number, number], b: [number, number]): [number, number][] => {
    const [x1, x2] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
    const [y1, y2] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]];
  };
  const preview = mode === "rect" && rectStart && hover ? rectPoly(rectStart, hover) : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="ze-stage">
        {imgSrc && !imgErr ? <img src={imgSrc} alt="Kamerabild" draggable={false} onError={() => setImgErr(true)} />
          : <div style={{ aspectRatio: "16/9", display: "grid", placeItems: "center", color: "var(--text-3)" }}>{cameraId === null ? "Erst eine Kamera wählen" : "Kein Standbild verfügbar"}</div>}
        <svg ref={svgRef} className={mode !== "idle" ? "drawing" : ""}
          onPointerDown={(e) => { if (mode === "rect") { e.preventDefault(); setRectStart(pt(e)); setHover(pt(e)); } }}
          onPointerMove={(e) => { if (mode !== "idle") setHover(pt(e)); }}
          onPointerUp={(e) => {
            if (mode === "rect" && rectStart) {
              const p = rectPoly(rectStart, pt(e));
              setRectStart(null);
              if (p[1][0] - p[0][0] > 0.01 && p[2][1] - p[0][1] > 0.01) { onChange(p); setMode("idle"); }
            }
          }}
          onClick={(e) => {
            if (mode !== "poly") return;
            const p = pt(e);
            if (draft.length >= 3 && Math.hypot((draft[0][0] - p[0]) * W, (draft[0][1] - p[1]) * H) < 12) { finishPoly(); return; }
            setDraft([...draft, p]);
          }}
          onDoubleClick={(e) => { if (mode === "poly") { e.preventDefault(); finishPoly(); } }}>
          {region.length >= 3 && mode === "idle" && (
            <g>
              <polygon points={toPts(region)} fill="#2dd4bf" fillOpacity={0.18} stroke="#2dd4bf" strokeWidth={2.5} />
              {region.map(([x, y], i) => (
                <circle key={i} className="vertex" cx={x * W} cy={y * H} r={7} fill="#05080d" stroke="#2dd4bf" strokeWidth={2.5}
                  onPointerDown={(e) => { e.stopPropagation(); e.preventDefault(); setDrag(i); }}
                  onContextMenu={(e) => { e.preventDefault(); if (region.length > 3) onChange(region.filter((_, k) => k !== i)); }} />
              ))}
            </g>
          )}
          {preview && <polygon points={toPts(preview)} fill="rgba(45,212,191,.15)" stroke="#2dd4bf" strokeWidth={2} strokeDasharray="6 4" pointerEvents="none" />}
          {mode === "poly" && draft.length > 0 && (
            <g pointerEvents="none">
              <polyline points={toPts(hover ? [...draft, hover] : draft)} fill="rgba(45,212,191,.12)" stroke="#2dd4bf" strokeWidth={2} />
              {draft.map(([x, y], i) => <circle key={i} cx={x * W} cy={y * H} r={i === 0 && draft.length >= 3 ? 9 : 5} fill={i === 0 ? "#2dd4bf" : "#05080d"} stroke="#2dd4bf" strokeWidth={2} />)}
            </g>
          )}
        </svg>
        <div className="ze-hint">
          {mode === "rect" ? "Rechteck aufziehen: Maus gedrückt halten und ziehen"
            : mode === "poly" ? `Punkte setzen (${draft.length}) · Klick auf ersten Punkt / Doppelklick / Enter = fertig · Esc = abbrechen`
            : "Punkte ziehen · Rechtsklick = Punkt entfernen"}
        </div>
      </div>
      <div className="row-wrap" style={{ gap: 6 }}>
        <button type="button" className={`btn btn-sm ${mode === "rect" ? "btn-primary" : ""}`} onClick={() => { setMode("rect"); setDraft([]); }}><Square size={14} />Rechteck</button>
        <button type="button" className={`btn btn-sm ${mode === "poly" ? "btn-primary" : ""}`} onClick={() => { setMode("poly"); setDraft([]); }}><PenLine size={14} />Vieleck</button>
        {mode === "poly" && <button type="button" className="btn btn-sm" disabled={draft.length < 3} onClick={finishPoly}><Check size={14} />Fertig</button>}
        {mode !== "idle" && region.length >= 3 && <button type="button" className="btn btn-sm btn-ghost" onClick={() => { setMode("idle"); setDraft([]); }}><X size={14} />Abbrechen</button>}
        <span className="spacer" />
        {region.length >= 3 && <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}><Eye size={12} style={{ verticalAlign: -2 }} /> {region.length} Punkte</span>}
        {cameraId !== null && <button type="button" className="btn btn-ghost btn-icon btn-sm" title="Standbild neu laden" onClick={() => { setImgErr(false); setBust(Date.now()); }}><RefreshCw size={14} /></button>}
      </div>
    </div>
  );
}
