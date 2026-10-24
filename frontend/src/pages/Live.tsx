import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft, Cctv, Clock, ChevronRight, ChevronsLeft, Eye, Gauge, Grid2x2, Grid3x3, LayoutGrid, Layers, Maximize2, Plus, Radio, Square, Timer, Users,
} from "lucide-react";
import { api } from "../lib/api";
import type { Camera, CameraStatus, DetObject, VGEvent } from "../lib/types";
import { useLive, useUi } from "../lib/store";
import { LivePlayer } from "../components/LivePlayer";
import { DetectionOverlay, useCameraObjects } from "../components/DetectionOverlay";
import { OFFSET_MAX_S, OFFSET_MIN_S, useSyncedFrame } from "../lib/overlaySync";
import { Empty, LabelIcon, Loading } from "../components/ui";
import { EventDetailDialog, FeedItem } from "../components/EventBits";
import { labelColor, labelName } from "../lib/labels";
import { fmtAgo } from "../lib/format";

function useStatus(cam: Camera): CameraStatus | null {
  const live = useLive((s) => s.status[cam.id]);
  return live ?? cam.status ?? null;
}

export function StatusBadge({ status, enabled = true }: { status: CameraStatus | null | undefined; enabled?: boolean }) {
  const st = !enabled ? "disabled" : status?.state ?? "offline";
  const cls = st === "online" ? "ok" : st === "starting" ? "warn" : st === "disabled" ? "" : "bad";
  const txt = { online: "Online", offline: "Offline", starting: "Startet", disabled: "Deaktiviert" }[st];
  return <span className={`badge ${cls}`}><span className="dot" />{txt}</span>;
}

function counts(objects: DetObject[]) {
  const m = new Map<string, number>();
  for (const o of objects) m.set(o.label, (m.get(o.label) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function CameraTile({ cam, compact, onOpen }: { cam: Camera; compact: boolean; onOpen: () => void }) {
  const status = useStatus(cam);
  const objects = useCameraObjects(cam.id);
  const showZones = useUi((s) => s.showZones);
  // Worker-Status betrifft die Analyse; das Live-Bild kommt direkt von go2rtc -> nur bei deaktivierter Kamera nicht abspielen.
  const offline = !cam.enabled;
  const analysing = status?.state === "online";
  const alert = useLive((s) => s.feed.some((e) => e.camera_id === cam.id && e.active && e.rules?.length > 0));
  const aspect = status?.width && status?.height ? status.width / status.height : null;
  return (
    <div className={`cam-tile ${alert ? "alert" : ""}`} onClick={onOpen} role="button" tabIndex={0} aria-label={cam.name} onKeyDown={(k) => k.key === "Enter" && onOpen()}>
      <LivePlayer cameraId={cam.id} aspect={aspect} offline={offline}>
        {(r) => <DetectionOverlay rect={r} cameraId={cam.id} zones={cam.zones} showZones={showZones} compact={compact} />}
      </LivePlayer>
      <div className="tile-top">
        {!offline && analysing && <span className="badge live"><span className="dot" />LIVE</span>}
        <span className="tile-name">{cam.name}</span>
        <span className="spacer" />
        <StatusBadge status={status} enabled={cam.enabled} />
      </div>
      <div className="tile-actions"><button className="btn btn-sm btn-icon" title="Fokusansicht"><Maximize2 size={14} /></button></div>
      <div className="tile-bottom">
        {counts(objects).map(([l, n]) => (
          <span key={l} className="tile-count" style={{ ["--lc" as string]: labelColor(l) }} title={labelName(l)}>
            <LabelIcon label={l} />{n}
          </span>
        ))}
        <span className="spacer" />
        {status?.state === "online" && (
          <span className="tile-meta">{status.fps?.toFixed(1) ?? "–"} fps · {status.inference_ms?.toFixed(0) ?? "–"} ms{status.width ? ` · ${status.width}×${status.height}` : ""}</span>
        )}
      </div>
    </div>
  );
}

function EventFeed({ cameraId, onPick, collapsible = true }: { cameraId?: number; onPick: (e: VGEvent) => void; collapsible?: boolean }) {
  const { feedOpen, set } = useUi();
  const feed = useLive((s) => s.feed);
  const seed = useLive((s) => s.seedFeed);
  const initial = useQuery({ queryKey: ["events", "feed"], queryFn: () => api.events({ limit: 40 }) });
  useEffect(() => { if (initial.data) seed(initial.data.items); }, [initial.data, seed]);
  const camEvents = useQuery({
    queryKey: ["events", "cam", cameraId], enabled: cameraId !== undefined,
    queryFn: () => api.events({ camera_id: cameraId, limit: 30 }),
  });
  const items = useMemo(() => {
    if (cameraId === undefined) return feed;
    const live = feed.filter((e) => e.camera_id === cameraId);
    const ids = new Set(live.map((e) => e.id));
    return [...live, ...(camEvents.data?.items ?? []).filter((e) => !ids.has(e.id))];
  }, [feed, cameraId, camEvents.data]);
  const open = !collapsible || feedOpen;
  return (
    <aside className={`feed ${open ? "" : "closed"}`} style={!collapsible ? { width: "100%", flex: 1 } : undefined}>
      <div className="feed-head">
        {collapsible && !open && <button className="btn btn-ghost btn-icon btn-sm" onClick={() => set({ feedOpen: true })} title="Feed einblenden"><ChevronsLeft size={16} /></button>}
        <h3><Radio size={16} color="var(--bad)" />{cameraId !== undefined ? "Letzte Ereignisse" : "Live-Ereignisse"}</h3>
        {open && <span className="badge outline">{items.length}</span>}
        {collapsible && open && <button className="btn btn-ghost btn-icon btn-sm" onClick={() => set({ feedOpen: false })} title="Feed ausblenden"><ChevronRight size={16} /></button>}
      </div>
      {open && (
        <div className="feed-list">
          {initial.isLoading && <Loading />}
          {!initial.isLoading && items.length === 0 && <Empty icon={<Eye size={30} />} title="Noch keine Ereignisse" text="Neue Erkennungen erscheinen hier in Echtzeit." />}
          {items.map((e) => <FeedItem key={e.id} e={e} onClick={() => onPick(e)} />)}
        </div>
      )}
    </aside>
  );
}

function Kpis({ cams }: { cams: Camera[] }) {
  const dets = useLive((s) => s.detections);
  const status = useLive((s) => s.status);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const online = cams.filter((c) => (status[c.id] ?? c.status)?.state === "online").length;
  const all: DetObject[] = Object.values(dets).filter((d) => Date.now() - d.receivedAt < 2500).flatMap((d) => d.objects);
  const g = (grp: string[]) => all.filter((o) => grp.includes(o.label)).length;
  const items = [
    { k: "Kameras online", v: `${online}/${cams.length}`, c: "var(--accent)", i: <Cctv size={16} /> },
    { k: "Personen", v: g(["person"]), c: "var(--c-person)", i: <Users size={16} /> },
    { k: "Fahrzeuge", v: g(["car", "truck", "bus", "motorcycle", "bicycle"]), c: "var(--c-vehicle)", i: <LabelIcon label="car" size={16} /> },
    { k: "Tiere", v: g(["dog", "cat", "bird", "horse"]), c: "var(--c-animal)", i: <LabelIcon label="dog" size={16} /> },
  ];
  return (
    <div className="kpis">
      {items.map((x) => (
        <div className="kpi" key={x.k} style={{ ["--kc" as string]: x.c }}>
          <span className="ico">{x.i}</span>
          <div><b>{x.v}</b><span>{x.k}</span></div>
        </div>
      ))}
    </div>
  );
}

const MODE_NAME = { webrtc: "WebRTC", mse: "MSE", mjpeg: "MJPEG", none: "–" } as const;

/** Fokusansicht: manueller Versatz der Boxen zum Video ("Overlay-Verzögerung"), je Kamera im Browser gespeichert. */
function OverlaySyncControl({ cameraId }: { cameraId: number }) {
  const [open, setOpen] = useState(false);
  const overlayDelay = useUi((s) => s.overlayDelay);
  const set = useUi((s) => s.set);
  const manual = overlayDelay?.[cameraId] ?? 0;
  const f = useSyncedFrame(cameraId, open ? 250 : 2000);
  const setManual = (v: number) => set({ overlayDelay: { ...overlayDelay, [cameraId]: Math.round(v * 10) / 10 } });
  return (
    <div className="sync-ctl">
      <button className={`btn btn-sm ${open || manual ? "active" : ""}`} onClick={() => setOpen(!open)} title="Overlay-Verzögerung: Boxen zeitlich an das Video anpassen">
        <Clock size={14} />Sync{manual ? ` ${manual > 0 ? "+" : ""}${manual.toFixed(1)} s` : ""}
      </button>
      {open && (
        <div className="sync-pop">
          <b>Overlay-Verzögerung</b>
          <span className="muted">
            Die Boxen werden automatisch um die gemessene Verzögerung des Videos ({MODE_NAME[f.mode]}: {(f.autoMs / 1000).toFixed(2)} s) verschoben.
            Laufen sie dem Bild trotzdem voraus (positiv) oder hinterher (negativ), hier nachkorrigieren.
          </span>
          <div className="range">
            <input type="range" min={OFFSET_MIN_S} max={OFFSET_MAX_S} step={0.1} value={manual} onChange={(e) => setManual(Number(e.target.value))} aria-label="Zusätzliche Verzögerung in Sekunden" />
            <output>{manual > 0 ? "+" : ""}{manual.toFixed(1)} s</output>
          </div>
          <span className="muted">Gesamt: {(f.delayMs / 1000).toFixed(2)} s{f.stalled ? " · Video hängt" : ""}</span>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button className="btn btn-sm" onClick={() => setManual(0)} disabled={!manual}>Zurücksetzen</button>
          </div>
        </div>
      )}
    </div>
  );
}

function FocusView({ cam, onBack, onPick }: { cam: Camera; onBack: () => void; onPick: (e: VGEvent) => void }) {
  const status = useStatus(cam);
  const objects = useCameraObjects(cam.id);
  const { showZones, set } = useUi();
  const offline = !cam.enabled;
  const aspect = status?.width && status?.height ? status.width / status.height : null;
  return (
    <div className="page-full">
      <div className="live-toolbar" style={{ marginBottom: 14 }}>
        <button className="btn btn-ghost" onClick={onBack}><ArrowLeft size={16} />Alle Kameras</button>
        <h1>{cam.name}</h1>
        <StatusBadge status={status} enabled={cam.enabled} />
        {status?.state === "online" && <>
          <span className="badge outline"><Gauge size={12} />{status.fps?.toFixed(1)} fps</span>
          <span className="badge outline"><Timer size={12} />{status.inference_ms?.toFixed(1)} ms</span>
        </>}
        {status?.last_seen && status.state !== "online" && <span className="muted">zuletzt {fmtAgo(status.last_seen)}</span>}
        <span className="spacer" />
        {counts(objects).map(([l, n]) => (
          <span key={l} className="tile-count" style={{ ["--lc" as string]: labelColor(l), height: 30 }}><LabelIcon label={l} />{n} {labelName(l)}</span>
        ))}
        <button className={`btn btn-sm ${showZones ? "active" : ""}`} onClick={() => set({ showZones: !showZones })}><Layers size={14} />Zonen</button>
        {!offline && <OverlaySyncControl cameraId={cam.id} />}
      </div>
      <div className="focus">
        <div className="focus-video">
          <LivePlayer cameraId={cam.id} aspect={aspect} offline={offline}>
            {(r) => <DetectionOverlay rect={r} cameraId={cam.id} zones={cam.zones} showZones={showZones} />}
          </LivePlayer>
        </div>
        <div className="focus-side"><EventFeed cameraId={cam.id} onPick={onPick} collapsible={false} /></div>
      </div>
    </div>
  );
}

export function LivePage() {
  const { cameraId } = useParams();
  const nav = useNavigate();
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras, refetchInterval: 60_000 });
  const { layout, showZones, set } = useUi();
  const [picked, setPicked] = useState<VGEvent | null>(null);

  if (cams.isLoading) return <Loading text="Lade Kameras …" />;
  const list = cams.data ?? [];
  const focus = cameraId ? list.find((c) => String(c.id) === cameraId) : undefined;

  if (focus) return <>
    <FocusView cam={focus} onBack={() => nav("/")} onPick={setPicked} />
    {picked && <EventDetailDialog event={picked} onClose={() => setPicked(null)} />}
  </>;

  const n = list.length;
  const cols = layout === "auto" ? (n <= 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4) : layout;
  const layouts: { v: typeof layout; icon: React.ReactNode; t: string }[] = [
    { v: "auto", icon: <LayoutGrid size={14} />, t: "Auto" },
    { v: 1, icon: <Square size={14} />, t: "1" },
    { v: 2, icon: <Grid2x2 size={14} />, t: "2×2" },
    { v: 3, icon: <Grid3x3 size={14} />, t: "3×3" },
    { v: 4, icon: <LayoutGrid size={14} />, t: "4×4" },
  ];

  return (
    <div className="page-full">
      <div className="live-wrap">
        <div className="live-main">
          <div className="live-toolbar">
            <Kpis cams={list} />
            <span className="spacer" />
            <button className={`btn btn-sm ${showZones ? "active" : ""}`} onClick={() => set({ showZones: !showZones })} title="Zonen anzeigen"><Layers size={14} />Zonen</button>
            <div className="seg" role="group" aria-label="Layout">
              {layouts.map((l) => (
                <button key={String(l.v)} className={layout === l.v ? "on" : ""} onClick={() => set({ layout: l.v })} title={`Layout ${l.t}`}>{l.icon}{l.t}</button>
              ))}
            </div>
          </div>
          {n === 0 ? (
            <div className="card" style={{ flex: 1 }}>
              <Empty icon={<Cctv size={44} />} title="Noch keine Kameras" text="Lege unter Administration → Kameras deine erste Kamera an.">
                <button className="btn btn-primary" onClick={() => nav("/admin/kameras")}><Plus size={16} />Kamera hinzufügen</button>
              </Empty>
            </div>
          ) : (
            <div className="cam-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
              {list.map((c) => <CameraTile key={c.id} cam={c} compact={cols >= 3} onOpen={() => nav(`/live/${c.id}`)} />)}
            </div>
          )}
        </div>
        <EventFeed onPick={setPicked} />
      </div>
      {picked && <EventDetailDialog event={picked} onClose={() => setPicked(null)} />}
    </div>
  );
}
