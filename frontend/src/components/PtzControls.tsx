import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookmarkPlus, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Crosshair, Home, Move, Pause, Play, Route, X, ZoomIn, ZoomOut,
} from "lucide-react";
import { ConfirmDialog } from "./ui";
import { api } from "../lib/api";
import type { Camera, PtzState } from "../lib/types";
import { toast, useLive } from "../lib/store";
import { labelName } from "../lib/labels";
import { parseTs } from "../lib/format";

/** Aktueller PTZ-Zustand: WebSocket (live) vor dem Stand aus der Kamera-Liste. */
export function usePtzState(cam: Camera): PtzState | null {
  const live = useLive((s) => s.ptz[cam.id]);
  if (!cam.ptz_enabled) return null;
  return live ?? cam.ptz_state ?? null;
}

/** Zonen/Gegenstände gelten nur in der Grundposition (PTZ.B). Nicht-PTZ-Kameras: immer. */
export function useAtHome(cam: Camera): boolean {
  const st = usePtzState(cam);
  return !st || (st.view === "home" && !st.moving);
}

function useCountdown(iso: string | null | undefined): string | null {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!iso) return;
    const t = window.setInterval(() => tick((x) => x + 1), 1000);
    return () => window.clearInterval(t);
  }, [iso]);
  const end = parseTs(iso ?? null)?.getTime();
  if (!end) return null;
  const s = Math.max(0, Math.round((end - Date.now()) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} min` : `${s} s`;
}

export function ptzModeText(st: PtzState): string {
  switch (st.mode) {
    case "manual": return "Handsteuerung";
    case "rule": return `Regel-Position${st.preset_name ? ` „${st.preset_name}“` : ""}`;
    case "follow": return `Verfolgt ${st.target ? labelName(st.target.label) : "Ziel"}`;
    case "patrol": return `Patrouille${st.patrol_step !== null ? ` · Schritt ${st.patrol_step + 1}` : ""}`;
    case "error": return "PTZ nicht verbunden";
    default: return st.view === "home" ? "Grundposition" : st.preset_name ? `Position „${st.preset_name}“` : "Freie Position";
  }
}

/** Kompakte Anzeige der Betriebsart (Kachel/Symbolleiste). */
export function PtzBadge({ cam }: { cam: Camera }) {
  const st = usePtzState(cam);
  const left = useCountdown(st && ["manual", "rule", "patrol"].includes(st.mode) ? st.until : null);
  if (!st) return null;
  const cls = st.mode === "error" ? "bad" : st.mode === "follow" || st.mode === "rule" ? "warn" : st.view === "home" ? "" : "info";
  return (
    <span className={`badge ${cls}`} title={st.error ?? (st.view === "home" ? "Zonen, Gegenstände und Sensoren aktiv" : "Nicht in der Grundposition – Zonen, Gegenstände und Sensoren pausieren")}>
      {st.mode === "follow" ? <Crosshair size={11} /> : st.mode === "patrol" ? <Route size={11} /> : <Move size={11} />}
      {ptzModeText(st)}{st.moving ? " · fährt" : ""}{left ? ` · ${st.mode === "patrol" ? "noch" : "zurück in"} ${left}` : ""}
    </span>
  );
}

type Dir = { pan: number; tilt: number; zoom: number };
const REPEAT_MS = 350;
const PULSE_MS = 600;

/** Steuerkreuz über dem Live-Bild (Fokusansicht). Gedrückt halten = weiter bewegen. */
export function PtzPad({ cam }: { cam: Camera }) {
  const st = usePtzState(cam);
  const info = useQuery({ queryKey: ["ptz", cam.id], queryFn: () => api.ptz(cam.id), staleTime: 30_000 });
  const [open, setOpen] = useState(true);
  const timer = useRef<number | undefined>(undefined);
  const active = useRef(false);

  const fail = (e: unknown) => toast.error("PTZ", (e as Error).message);
  const gotoM = useMutation({ mutationFn: (p: string | null) => api.ptzGoto(cam.id, p), onError: fail });
  const resumeM = useMutation({ mutationFn: () => api.ptzResume(cam.id), onError: fail });
  const pauseM = useMutation({ mutationFn: (min: number) => api.ptzPause(cam.id, min), onError: fail });
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ptz", cam.id] });
    qc.invalidateQueries({ queryKey: ["cameras"] });
  };
  const [confirmHome, setConfirmHome] = useState(false);
  const [naming, setNaming] = useState<string | null>(null);   // null = Eingabe zu
  const homeM = useMutation({
    mutationFn: () => api.ptzSetHome(cam.id),
    onSuccess: () => { setConfirmHome(false); refresh(); toast.success("Grundposition gespeichert", "Zonen, Gegenstände und Sensoren gelten für diesen Blick."); },
    onError: (e) => { setConfirmHome(false); fail(e); },
  });
  const saveM = useMutation({
    mutationFn: (name: string) => api.ptzSavePreset(cam.id, name),
    onSuccess: (p) => { setNaming(null); refresh(); toast.success("Position gespeichert", `„${p.name}“ – anfahrbar hier, in Regeln und in der Patrouille.`); },
    onError: fail,
  });

  const stopHold = () => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = undefined;
    if (active.current) {
      active.current = false;
      api.ptzStop(cam.id).catch(() => undefined);
    }
  };
  useEffect(() => stopHold, []); // eslint-disable-line react-hooks/exhaustive-deps

  const startHold = (d: Dir) => {
    stopHold();
    active.current = true;
    const send = () => api.ptzMove(cam.id, { ...d, duration_ms: PULSE_MS }).catch((e) => { stopHold(); fail(e); });
    send();
    timer.current = window.setInterval(send, REPEAT_MS);
  };

  const btn = (d: Dir, icon: React.ReactNode, title: string, cls: string) => (
    <button type="button" className={`ptz-btn ${cls}`} title={title} aria-label={title}
      onPointerDown={(e) => { e.preventDefault(); (e.target as HTMLElement).setPointerCapture?.(e.pointerId); startHold(d); }}
      onPointerUp={stopHold} onPointerCancel={stopHold} onLostPointerCapture={stopHold}
      onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && !e.repeat) startHold(d); }}
      onKeyUp={stopHold}
      disabled={!info.data?.connected}>
      {icon}
    </button>
  );

  const caps = info.data?.capabilities;
  const presets = info.data?.presets ?? [];
  const home = cam.ptz?.home_preset;
  const automatic = cam.ptz?.patrol?.enabled || cam.ptz?.follow?.enabled;
  const paused = st?.paused_until && (parseTs(st.paused_until)?.getTime() ?? 0) > Date.now();

  if (!open) {
    return <button type="button" className="btn btn-sm ptz-toggle" onClick={() => setOpen(true)} title="PTZ-Steuerung einblenden"><Move size={14} />PTZ</button>;
  }
  return (
    <div className="ptz-pad" onClick={(e) => e.stopPropagation()}>
      <div className="ptz-head">
        <b><Move size={13} /> Schwenken</b>
        <span className="spacer" />
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={() => setOpen(false)} title="Ausblenden"><ChevronDown size={14} /></button>
      </div>
      {info.data && !info.data.connected && <div className="ptz-err">{info.data.error ?? "Kamera nicht verbunden"}</div>}
      <div className="ptz-cross">
        {btn({ pan: 0, tilt: 1, zoom: 0 }, <ChevronUp size={18} />, "Nach oben", "up")}
        {btn({ pan: -1, tilt: 0, zoom: 0 }, <ChevronLeft size={18} />, "Nach links", "left")}
        <button type="button" className="ptz-btn mid" title="Grundposition" aria-label="Grundposition" disabled={!home || !info.data?.connected}
          onClick={() => gotoM.mutate(null)}><Home size={16} /></button>
        {btn({ pan: 1, tilt: 0, zoom: 0 }, <ChevronRight size={18} />, "Nach rechts", "right")}
        {btn({ pan: 0, tilt: -1, zoom: 0 }, <ChevronDown size={18} />, "Nach unten", "down")}
      </div>
      {caps?.zoom && (
        <div className="row" style={{ gap: 6, justifyContent: "center" }}>
          {btn({ pan: 0, tilt: 0, zoom: -1 }, <ZoomOut size={16} />, "Zoom heraus", "")}
          {btn({ pan: 0, tilt: 0, zoom: 1 }, <ZoomIn size={16} />, "Zoom hinein", "")}
        </div>
      )}
      {presets.length > 0 && (
        <select className="select input-sm" value="" aria-label="Position anfahren" disabled={!info.data?.connected}
          onChange={(e) => e.target.value && gotoM.mutate(e.target.value)}>
          <option value="">Position anfahren …</option>
          {presets.map((p) => <option key={p.token} value={p.token}>{p.name}{p.token === home ? " (Grundposition)" : ""}</option>)}
        </select>
      )}
      {naming === null ? (
        <>
          <button type="button" className="btn btn-sm" disabled={!info.data?.connected || !caps?.presets}
            onClick={() => setConfirmHome(true)} title="Aktuelle Ausrichtung als Grundposition speichern"><Home size={13} />Als Grundposition</button>
          <button type="button" className="btn btn-sm" disabled={!info.data?.connected || !caps?.presets}
            onClick={() => setNaming("")} title="Aktuelle Ausrichtung als weitere Position speichern"><BookmarkPlus size={13} />Position speichern</button>
        </>
      ) : (
        <form style={{ display: "flex", flexDirection: "column", gap: 6 }} onSubmit={(e) => { e.preventDefault(); if (naming.trim()) saveM.mutate(naming.trim()); }}>
          <input className="input input-sm" autoFocus maxLength={40} placeholder="Name, z. B. Fenster" value={naming}
            onChange={(e) => setNaming(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setNaming(null); }} />
          <div className="row" style={{ gap: 6 }}>
            <button type="submit" className="btn btn-sm btn-primary" style={{ flex: 1 }} disabled={!naming.trim() || saveM.isPending}>
              {saveM.isPending ? <span className="spinner" /> : "Speichern"}
            </button>
            <button type="button" className="btn btn-sm btn-ghost btn-icon" onClick={() => setNaming(null)} title="Abbrechen" aria-label="Abbrechen"><X size={14} /></button>
          </div>
        </form>
      )}
      {st && ["manual", "rule", "follow"].includes(st.mode) && (
        <button type="button" className="btn btn-sm" onClick={() => resumeM.mutate()}><Play size={13} />Automatik fortsetzen</button>
      )}
      {automatic && st && !["manual", "rule"].includes(st.mode) && (
        paused
          ? <button type="button" className="btn btn-sm" onClick={() => pauseM.mutate(0)}><Play size={13} />Automatik wieder an</button>
          : <button type="button" className="btn btn-sm" onClick={() => pauseM.mutate(60)} title="Patrouille und Verfolgung für 1 Stunde aussetzen"><Pause size={13} />Automatik 1 h pausieren</button>
      )}
      {confirmHome && (
        <ConfirmDialog title="Grundposition setzen" confirmLabel="Als Grundposition speichern" danger={false} busy={homeM.isPending}
          onConfirm={() => homeM.mutate()} onClose={() => setConfirmHome(false)}
          text={<>
            Die aktuelle Ausrichtung wird zur Grundposition{home ? " und ersetzt die bisherige" : ""}. Dorthin kehrt die Kamera nach
            Handsteuerung, Regeln, Patrouille und Verfolgung zurück.
            <br /><br />Zonen, Gegenstände und Sensoren sind auf das Bild der Grundposition eingezeichnet – bei einem anderen
            Blick bitte prüfen und ggf. neu zeichnen.
          </>} />
      )}
    </div>
  );
}
