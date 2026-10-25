import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HeartPulse, ImageOff, RefreshCw } from "lucide-react";
import { api } from "../../lib/api";
import type { Camera, CameraHealthLogEntry, CameraInput, HealthProblemType } from "../../lib/types";
import { Img, Loading, Switch } from "../../components/ui";
import { HEALTH_LABEL, HealthBadge } from "../../components/HealthBadge";
import { fmtAgo, fmtShort, parseTs } from "../../lib/format";
import { toast, useLive } from "../../lib/store";
import { isMock, mockSnapshot } from "../../lib/mock";

const TYPE_COLOR: Record<HealthProblemType, string> = {
  dark: "#64748b", blur: "#a78bfa", covered: "#f59e0b", frozen: "#38bdf8", moved: "#f472b6",
};

function fmtDuration(s: number): string {
  if (s < 90) return `${Math.max(1, Math.round(s))} s`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ${m % 60} min` : `${Math.round(h / 24)} Tage`;
}

function num(v: unknown): number | null {
  return typeof v === "number" && isFinite(v) ? v : null;
}

/** Zeitleiste der Probleme über das gewählte Fenster. */
function Timeline({ log, hours }: { log: CameraHealthLogEntry[]; hours: number }) {
  const end = Date.now();
  const start = end - hours * 3600_000;
  const types = [...new Set(log.map((e) => e.type))];
  if (!log.length) return <div className="muted" style={{ fontSize: 13 }}>Keine Probleme im Zeitraum.</div>;
  return (
    <div className="health-timeline">
      {types.map((t) => (
        <div key={t} className="ht-row">
          <span className="ht-label">{HEALTH_LABEL[t] ?? t}</span>
          <div className="ht-track">
            {log.filter((e) => e.type === t).map((e, i) => {
              const a = Math.max(start, parseTs(e.start_ts)?.getTime() ?? start);
              const b = Math.min(end, e.end_ts ? parseTs(e.end_ts)?.getTime() ?? end : end);
              if (b < start) return null;
              const left = ((a - start) / (end - start)) * 100;
              const width = Math.max(0.6, ((b - a) / (end - start)) * 100);
              return <span key={i} className="ht-seg" style={{ left: `${left}%`, width: `${width}%`, background: TYPE_COLOR[t] }}
                title={`${HEALTH_LABEL[t]}: ${fmtShort(e.start_ts)} – ${e.end_ts ? fmtShort(e.end_ts) : "läuft"}`} />;
            })}
          </div>
        </div>
      ))}
      <div className="ht-axis"><span>vor {hours >= 48 ? `${Math.round(hours / 24)} Tagen` : `${hours} h`}</span><span>jetzt</span></div>
    </div>
  );
}

/** Reiter „Zustand“ im Kamera-Dialog (C5 Kamera-Gesundheit). */
export function CameraHealthTab({ cam, f, set }: {
  cam: Camera | null;
  f: CameraInput;
  set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void;
}) {
  const qc = useQueryClient();
  const [hours, setHours] = useState(168);
  const [mode, setMode] = useState<"auto" | "day" | "night">("auto");
  const [bust, setBust] = useState(() => Date.now());
  const liveHealth = useLive((s) => (cam ? s.status[cam.id]?.health : undefined));
  const q = useQuery({
    queryKey: ["camera-health", cam?.id, hours], enabled: !!cam,
    queryFn: () => api.cameraHealth(cam!.id, hours), refetchInterval: 15000,
  });
  const ref = useMutation({
    mutationFn: () => api.setHealthReference(cam!.id, mode),
    onSuccess: (r) => {
      if (r.ok === false) toast.error("Referenz nicht gesetzt", r.error ?? "Unbekannter Fehler");
      else if (r.ok) toast.success("Referenzbild gesetzt", "Neue Vergleichsbasis für verschoben/unscharf/verdeckt.");
      else toast.info("Auftrag gesendet", "Der Worker hat noch nicht geantwortet – Seite gleich neu laden.");
      setBust(Date.now());
      qc.invalidateQueries({ queryKey: ["camera-health", cam?.id] });
    },
    onError: (e: Error) => toast.error("Referenz nicht gesetzt", e.message),
  });

  const sw = (
    <div className="field span-2">
      <label>Bildprüfung</label>
      <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
        <Switch checked={f.health_enabled} onChange={(v) => set("health_enabled", v)}
          label={f.health_enabled ? "aktiv – Bild wird alle 10 s auf Probleme geprüft" : "aus"} />
      </div>
      <span className="hint">
        Meldet, wenn das Bild länger als die eingestellte Zeit (Einstellungen → Benachrichtigungen) verdeckt, unscharf,
        zu dunkel, eingefroren oder verschoben ist. Läuft nur auf der CPU. Für schwenkbare (PTZ-)Kameras ausschalten.
      </span>
    </div>
  );

  if (!cam) return <div className="form-grid">{sw}<div className="field span-2 muted">Zustand und Referenzbilder sind nach dem Speichern verfügbar.</div></div>;
  if (q.isLoading) return <div className="form-grid">{sw}<div className="span-2"><Loading /></div></div>;
  const h = q.data;
  const cur = h?.current;
  const health = liveHealth !== undefined ? liveHealth : cur ? { state: cur.state, problems: cur.problems } : null;
  const m = cur?.metrics ?? {};
  const sharpRef = num(m.sharpness_ref);
  const sharp = num(m.sharpness);
  const rows: [string, string][] = [
    ["Modus", m.mode === "night" ? "Nacht (IR/Graustufen)" : m.mode === "day" ? "Tag (Farbe)" : "–"],
    ["Helligkeit", num(m.brightness) !== null ? `${Math.round(num(m.brightness)!)} / 255` : "–"],
    ["Schärfe", sharp !== null && sharpRef ? `${Math.round((sharp / sharpRef) * 100)} % der Referenz` : "–"],
    ["Verschiebung", num(m.shift) !== null ? `${(num(m.shift)! * 100).toFixed(1)} % der Bildbreite` : "–"],
    ["Übereinstimmung", num(m.inliers) !== null ? `${Math.round(Math.min(1.5, num(m.inliers)!) * 100)} % (Struktur ggü. Referenz)` : "–"],
    ["Bildänderung", num(m.diff) !== null ? `${num(m.diff)!.toFixed(2)} (zum vorigen Prüfbild)` : "–"],
    ["Geprüft", cur?.ts ? fmtAgo(cur.ts) : "–"],
  ];

  return (
    <div className="form-grid">
      {sw}
      <div className="field span-2">
        <label>Aktueller Zustand</label>
        <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
          {f.health_enabled ? <HealthBadge health={health} full /> : <span className="badge">Prüfung aus</span>}
          {(health?.problems ?? []).map((p) => <span key={p.type} className="badge warn" style={{ animation: "none" }}>{p.text}</span>)}
          {(cur?.pending ?? []).map((p) => <span key={p.type} className="badge outline" title="noch unter der Warnschwelle">wird beobachtet: {p.text}</span>)}
        </div>
      </div>
      <div className="field span-2">
        <label>Aktuelles Bild und Referenzen</label>
        <div className="health-images">
          {([["Aktuell", isMock ? mockSnapshot(cam.id) : api.snapshotUrl(cam.id, bust)],
             ["Referenz Tag", h?.reference_urls.day ?? null],
             ["Referenz Nacht", h?.reference_urls.night ?? null]] as [string, string | null][]).map(([label, src]) => (
            <figure key={label}>
              <Img src={src} fallback={<div className="hi-empty"><ImageOff size={22} /><span>noch keine</span></div>} />
              <figcaption>{label}</figcaption>
            </figure>
          ))}
        </div>
        <div className="row-wrap" style={{ gap: 8, marginTop: 10 }}>
          <select className="input" style={{ width: "auto" }} value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
            <option value="auto">automatisch (Tag/Nacht nach Bild)</option>
            <option value="day">als Tag-Referenz</option>
            <option value="night">als Nacht-Referenz</option>
          </select>
          <button type="button" className="btn" disabled={ref.isPending || !cam.enabled} onClick={() => ref.mutate()}>
            {ref.isPending ? <span className="spinner" /> : <RefreshCw size={15} />}Referenz neu setzen
          </button>
          <span className="hint">Nach gewolltem Umbau oder Neuausrichtung. Die Referenz wird sonst automatisch beim ersten brauchbaren Bild angelegt.</span>
        </div>
      </div>
      <div className="field">
        <label>Messwerte</label>
        <dl className="kv">{rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
      </div>
      <div className="field">
        <label className="row" style={{ justifyContent: "space-between" }}>
          <span><HeartPulse size={13} style={{ verticalAlign: -2 }} /> Zeitleiste</span>
          <select className="input" style={{ width: "auto", height: 28, padding: "0 8px" }} value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={24}>24 Stunden</option><option value={168}>7 Tage</option><option value={720}>30 Tage</option>
          </select>
        </label>
        <Timeline log={h?.log ?? []} hours={hours} />
        {(h?.log ?? []).slice(0, 8).map((e, i) => {
          const a = parseTs(e.start_ts)?.getTime() ?? 0;
          const b = e.end_ts ? parseTs(e.end_ts)?.getTime() ?? Date.now() : Date.now();
          return (
            <div key={i} className="row" style={{ fontSize: 12, gap: 8, marginTop: 4 }}>
              <span className="ht-dot" style={{ background: TYPE_COLOR[e.type] }} />
              <b>{HEALTH_LABEL[e.type] ?? e.type}</b>
              <span className="muted">{fmtShort(e.start_ts)} · {e.end_ts ? fmtDuration((b - a) / 1000) : "läuft"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
