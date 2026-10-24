import { memo, useRef } from "react";
import { PauseCircle } from "lucide-react";
import type { ContentRect } from "./LivePlayer";
import type { DetObject, Zone } from "../lib/types";
import { labelColor, labelName } from "../lib/labels";
import { useSyncedFrame, useVideoMonitor } from "../lib/overlaySync";

/**
 * Zum Video synchronisierte Objekte einer Kamera (für Zähler in Kachel/Toolbar, grob getaktet).
 * Die eigentliche Synchronisation (Puffer, Verzögerung, Standbild-Erkennung) steckt in lib/overlaySync.ts.
 */
export function useCameraObjects(cameraId: number): DetObject[] {
  return useSyncedFrame(cameraId, 500).objects;
}

/**
 * Overlay mit Boxen und Zonen. Muss innerhalb von <LivePlayer> gerendert werden: es sucht das <video> im
 * umgebenden `.player`, misst dessen Verzögerung/Standbild und zeichnet die passenden (verzögerten) Detektionen.
 */
export function DetectionOverlay({
  rect, cameraId, zones, showZones, compact,
}: { rect: ContentRect; cameraId: number; zones?: Zone[]; showZones?: boolean; compact?: boolean }) {
  const anchor = useRef<HTMLDivElement>(null);
  useVideoMonitor(cameraId, anchor);
  const f = useSyncedFrame(cameraId, compact ? 200 : 100);
  return (
    <>
      <div ref={anchor} hidden />
      <OverlaySvg rect={rect} objects={f.objects} zones={zones} showZones={showZones} compact={compact} dim={f.stalled} />
      {f.stalled && (
        <div className="overlay-stall" style={{ left: rect.x + rect.w / 2, top: rect.y + rect.h / 2 }} title="Das Videobild aktualisiert sich nicht – Boxen sind eingefroren">
          <PauseCircle size={compact ? 13 : 15} />Video hängt
        </div>
      )}
    </>
  );
}

const OverlaySvg = memo(function OverlaySvg({
  rect, objects, zones, showZones, compact, dim,
}: { rect: ContentRect; objects: DetObject[]; zones?: Zone[]; showZones?: boolean; compact?: boolean; dim?: boolean }) {
  const { x, y, w, h } = rect;
  const fs = compact ? 11 : 13;
  return (
    <svg className={`overlay-svg${dim ? " dim" : ""}`} style={{ left: x, top: y, width: w, height: h }} viewBox={`0 0 ${w} ${h}`}>
      {showZones && zones?.map((z) => (
        <g key={z.id}>
          <polygon points={z.polygon.map(([px, py]) => `${px * w},${py * h}`).join(" ")}
            fill={z.color} fillOpacity={0.12} stroke={z.color} strokeWidth={1.5} strokeDasharray="6 4" />
          {z.polygon[0] && (
            <text x={z.polygon[0][0] * w + 6} y={z.polygon[0][1] * h + fs + 4} fill={z.color} fontSize={fs} stroke="#000" strokeWidth={3} strokeOpacity={0.6}>{z.name}</text>
          )}
        </g>
      ))}
      <g className="objs">
        {objects.map((o) => {
          const [x1, y1, x2, y2] = o.bbox;
          const bx = x1 * w, by = y1 * h, bw = Math.max(1, (x2 - x1) * w), bh = Math.max(1, (y2 - y1) * h);
          const c = labelColor(o.label);
          const name = o.identity?.display_name ?? (o.attributes?.plate_text ? String(o.attributes.plate_text) : null);
          const main = `${labelName(o.label)} ${Math.round(o.score * 100)}%`;
          const sub = compact ? null : `#${o.track_id}`;
          const text = name ? `${name} · ${main}` : main;
          const tw = (text.length + (sub ? sub.length + 1 : 0)) * fs * 0.58 + 12;
          const th = fs + 8;
          const ty = by - th - 2 < 0 ? by + 2 : by - th - 2;
          const tx = Math.max(0, Math.min(bx, w - tw));
          const L = Math.min(18, bw / 3, bh / 3);
          return (
            <g key={o.track_id}>
              <rect className="box" x={bx} y={by} width={bw} height={bh} rx={3} stroke={c} strokeOpacity={0.55} fill={c} fillOpacity={0.06} />
              {/* Eckmarkierungen */}
              <path className="corners" d={`M${bx},${by + L}V${by}H${bx + L} M${bx + bw - L},${by}H${bx + bw}V${by + L} M${bx + bw},${by + bh - L}V${by + bh}H${bx + bw - L} M${bx + L},${by + bh}H${bx}V${by + bh - L}`}
                stroke={c} strokeWidth={3} fill="none" strokeLinecap="round" />
              <g className="tag">
                <rect x={tx} y={ty} width={tw} height={th} rx={4} fill={c} fillOpacity={0.92} />
                <text x={tx + 6} y={ty + th - 5} fontSize={fs} fill="#05080d">
                  {text}
                  {sub && <tspan fillOpacity={0.6} dx={4}>{sub}</tspan>}
                </text>
              </g>
            </g>
          );
        })}
      </g>
    </svg>
  );
});
