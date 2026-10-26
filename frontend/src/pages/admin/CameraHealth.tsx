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
import { fmtNum, msgs, useMsgs } from "../../i18n";

const TYPE_COLOR: Record<HealthProblemType, string> = {
  dark: "#64748b", blur: "#a78bfa", covered: "#f59e0b", frozen: "#38bdf8", moved: "#f472b6",
};

function fmtDuration(s: number): string {
  if (s < 90) return `${Math.max(1, Math.round(s))} s`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ${m % 60} min` : msgs().cameras.health.days(Math.round(h / 24));
}

function num(v: unknown): number | null {
  return typeof v === "number" && isFinite(v) ? v : null;
}

/** Zeitleiste der Probleme über das gewählte Fenster. */
function Timeline({ log, hours }: { log: CameraHealthLogEntry[]; hours: number }) {
  const end = Date.now();
  const start = end - hours * 3600_000;
  const types = [...new Set(log.map((e) => e.type))];
  const m = useMsgs().cameras.health;
  if (!log.length) return <div className="muted" style={{ fontSize: 13 }}>{m.noProblems}</div>;
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
                title={`${HEALTH_LABEL[t]}: ${fmtShort(e.start_ts)} – ${e.end_ts ? fmtShort(e.end_ts) : m.running}`} />;
            })}
          </div>
        </div>
      ))}
      <div className="ht-axis"><span>{m.ago(hours)}</span><span>{m.now}</span></div>
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
  const msg = useMsgs();
  const hm = msg.cameras.health;
  const liveHealth = useLive((s) => (cam ? s.status[cam.id]?.health : undefined));
  const q = useQuery({
    queryKey: ["camera-health", cam?.id, hours], enabled: !!cam,
    queryFn: () => api.cameraHealth(cam!.id, hours), refetchInterval: 15000,
  });
  const ref = useMutation({
    mutationFn: () => api.setHealthReference(cam!.id, mode),
    onSuccess: (r) => {
      if (r.ok === false) toast.error(hm.refNotSet, r.error ?? msg.common.errorUnknown);
      else if (r.ok) toast.success(hm.refSet, hm.refSetText);
      else toast.info(hm.jobSent, hm.jobSentText);
      setBust(Date.now());
      qc.invalidateQueries({ queryKey: ["camera-health", cam?.id] });
    },
    onError: (e: Error) => toast.error(hm.refNotSet, e.message),
  });

  const sw = (
    <div className="field span-2">
      <label>{hm.check}</label>
      <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
        <Switch checked={f.health_enabled} onChange={(v) => set("health_enabled", v)}
          label={f.health_enabled ? hm.checkOn : msg.common.off} />
      </div>
      <span className="hint">
        {hm.checkHint}
      </span>
    </div>
  );

  if (!cam) return <div className="form-grid">{sw}<div className="field span-2 muted">{hm.afterSave}</div></div>;
  if (q.isLoading) return <div className="form-grid">{sw}<div className="span-2"><Loading /></div></div>;
  const h = q.data;
  const cur = h?.current;
  const health = liveHealth !== undefined ? liveHealth : cur ? { state: cur.state, problems: cur.problems } : null;
  const m = cur?.metrics ?? {};
  const sharpRef = num(m.sharpness_ref);
  const sharp = num(m.sharpness);
  const rows: [string, string][] = [
    [hm.mode, m.mode === "night" ? hm.night : m.mode === "day" ? hm.day : "–"],
    [hm.brightness, num(m.brightness) !== null ? `${Math.round(num(m.brightness)!)} / 255` : "–"],
    [hm.sharpness, sharp !== null && sharpRef ? hm.sharpnessVal(Math.round((sharp / sharpRef) * 100)) : "–"],
    [hm.shift, num(m.shift) !== null ? hm.shiftVal(fmtNum(num(m.shift)! * 100, 1)) : "–"],
    [hm.match, num(m.inliers) !== null ? hm.matchVal(Math.round(Math.min(1.5, num(m.inliers)!) * 100)) : "–"],
    [hm.change, num(m.diff) !== null ? hm.changeVal(fmtNum(num(m.diff)!, 2)) : "–"],
    [hm.checked, cur?.ts ? fmtAgo(cur.ts) : "–"],
  ];

  return (
    <div className="form-grid">
      {sw}
      <div className="field span-2">
        <label>{hm.current}</label>
        <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
          {f.health_enabled ? <HealthBadge health={health} full /> : <span className="badge">{hm.checkOff}</span>}
          {(health?.problems ?? []).map((p) => <span key={p.type} className="badge warn" style={{ animation: "none" }}>{p.text}</span>)}
          {(cur?.pending ?? []).map((p) => <span key={p.type} className="badge outline" title={hm.belowThreshold}>{hm.watching(p.text)}</span>)}
        </div>
      </div>
      <div className="field span-2">
        <label>{hm.images}</label>
        <div className="health-images">
          {([[hm.imgCurrent, isMock ? mockSnapshot(cam.id) : api.snapshotUrl(cam.id, bust)],
             [hm.imgDay, h?.reference_urls.day ?? null],
             [hm.imgNight, h?.reference_urls.night ?? null]] as [string, string | null][]).map(([label, src]) => (
            <figure key={label}>
              <Img src={src} fallback={<div className="hi-empty"><ImageOff size={22} /><span>{hm.noneYet}</span></div>} />
              <figcaption>{label}</figcaption>
            </figure>
          ))}
        </div>
        <div className="row-wrap" style={{ gap: 8, marginTop: 10 }}>
          <select className="input" style={{ width: "auto" }} value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
            <option value="auto">{hm.modeAuto}</option>
            <option value="day">{hm.modeDay}</option>
            <option value="night">{hm.modeNight}</option>
          </select>
          <button type="button" className="btn" disabled={ref.isPending || !cam.enabled} onClick={() => ref.mutate()}>
            {ref.isPending ? <span className="spinner" /> : <RefreshCw size={15} />}{hm.resetRef}
          </button>
          <span className="hint">{hm.resetHint}</span>
        </div>
      </div>
      <div className="field">
        <label>{hm.metrics}</label>
        <dl className="kv">{rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
      </div>
      <div className="field">
        <label className="row" style={{ justifyContent: "space-between" }}>
          <span><HeartPulse size={13} style={{ verticalAlign: -2 }} /> {hm.timeline}</span>
          <select className="input" style={{ width: "auto", height: 28, padding: "0 8px" }} value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={24}>{hm.h24}</option><option value={168}>{hm.d7}</option><option value={720}>{hm.d30}</option>
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
              <span className="muted">{fmtShort(e.start_ts)} · {e.end_ts ? fmtDuration((b - a) / 1000) : hm.running}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
