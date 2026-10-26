import { useState } from "react";
import { BellRing, CloudRain, Plus, Send, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { NotifyRouteSource, PushTarget, Settings } from "../../lib/types";
import { ChipSelect, Switch } from "../../components/ui";
import { toast } from "../../lib/store";
import { msgs, useMsgs } from "../../i18n";

/**
 * Einstellungen → Benachrichtigungen (W1, K1): Push-Ziele (ntfy/Pushover), Routen je Quelle, Tageslimit,
 * Wetter-Standort, Aufbewahrung. Geheimnisse kommen als "***" vom Backend und bleiben so, solange sie nicht
 * überschrieben werden.
 */

const ROUTE_SOURCES: NotifyRouteSource[] = ["health", "pet", "review", "report", "sensor"];
const ID_RE = /^[a-z0-9_-]{1,40}$/;

function newTarget(existing: PushTarget[], type: PushTarget["type"]): PushTarget {
  let n = existing.length + 1;
  while (existing.some((t) => t.id === `${type}${n}`)) n++;
  return {
    id: `${type}${n}`, type, name: msgs().settings.notifications.phone(type === "ntfy" ? "ntfy" : "Pushover"), enabled: true,
    server: type === "ntfy" ? "https://ntfy.sh" : null, topic: null, token: null, user_key: null, app_token: null,
  };
}

export function targetProblem(t: PushTarget): string | null {
  const m = msgs().settings;
  if (!ID_RE.test(t.id)) return m.shared.idProblem;
  if (!t.name.trim()) return m.shared.nameMissing;
  if (t.type === "ntfy" && t.topic !== "***" && !/^[A-Za-z0-9_-]{1,64}$/.test(t.topic ?? "")) return m.notifications.topicProblem;
  if (t.type === "pushover" && (!t.user_key || !t.app_token)) return m.notifications.pushoverProblem;
  return null;
}

function TargetEditor({ t, onChange, onRemove }: { t: PushTarget; onChange: (t: PushTarget) => void; onRemove: () => void }) {
  const all = useMsgs();
  const m = all.settings.notifications;
  const [busy, setBusy] = useState(false);
  const problem = targetProblem(t);
  const test = async () => {
    setBusy(true);
    try {
      const r = await api.pushTest({ config: t });
      if (r.ok) toast.success(msgs().settings.notifications.toastTestSent, msgs().settings.notifications.toastTestSentText(t.name));
      else toast.error(msgs().settings.shared.testFailed, r.error ?? msgs().common.errorUnknown);
    } catch (e) {
      toast.error(msgs().settings.shared.testFailed, (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const field = (k: "server" | "topic" | "token" | "user_key" | "app_token", label: string, placeholder?: string, secret?: boolean) => (
    <div className="field"><label>{label}</label>
      <input className="input input-sm mono" type={secret && t[k] !== "***" ? "password" : "text"} value={t[k] ?? ""} placeholder={placeholder}
        autoComplete="off" onChange={(e) => onChange({ ...t, [k]: e.target.value || null })} />
    </div>
  );
  return (
    <div className="action-card">
      <div className="ah">
        <span className="ico"><BellRing size={15} /></span>{t.type === "ntfy" ? "ntfy" : "Pushover"}
        <span className="spacer" />
        <Switch checked={t.enabled} onChange={(v) => onChange({ ...t, enabled: v })} label={all.common.active} />
        <button type="button" className="btn btn-sm" disabled={busy || !!problem} onClick={test} title={problem ?? m.sendTest}>
          {busy ? <span className="spinner" /> : <Send size={14} />}{all.settings.shared.test}
        </button>
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={onRemove} title={m.removeTarget}><Trash2 size={15} /></button>
      </div>
      <div className="form-grid">
        <div className="field"><label>{all.settings.shared.name}</label><input className="input input-sm" value={t.name} onChange={(e) => onChange({ ...t, name: e.target.value })} /></div>
        <div className="field"><label>{all.settings.shared.idForRules}</label><input className="input input-sm mono" value={t.id} onChange={(e) => onChange({ ...t, id: e.target.value.trim() })} /></div>
        {t.type === "ntfy" ? <>
          {field("server", m.server, "https://ntfy.sh")}
          {field("topic", m.topic, m.topicPlaceholder, true)}
          {field("token", m.accessToken, "tk_…", true)}
        </> : <>
          {field("user_key", m.userKey, "u…", true)}
          {field("app_token", m.appToken, "a…", true)}
        </>}
      </div>
      {problem && <span className="hint" style={{ color: "var(--warn)" }}>{problem}</span>}
      {t.type === "ntfy" && <span className="hint">{m.credsHint}</span>}
      {t.type === "ntfy" && (t.server ?? "").includes("ntfy.sh") && !t.token && (
        <span className="hint">{m.publicTopicHint}</span>
      )}
    </div>
  );
}

export function NotificationSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.notifications;
  const targets = f.push_targets ?? [];
  const setTargets = (t: PushTarget[]) => patch({ push_targets: t });
  const ids = targets.map((t) => t.id);
  const loc = f.weather_location;
  return (
    <div className="card">
      <div className="card-head"><BellRing size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{m.targets}</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
            {m.targetsHint}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {targets.map((t, i) => (
              <TargetEditor key={i} t={t} onChange={(n) => setTargets(targets.map((x, k) => (k === i ? n : x)))}
                onRemove={() => setTargets(targets.filter((_, k) => k !== i))} />
            ))}
          </div>
          <div className="row-wrap" style={{ marginTop: 10 }}>
            <button type="button" className="btn btn-sm" onClick={() => setTargets([...targets, newTarget(targets, "ntfy")])}><Plus size={14} />ntfy</button>
            <button type="button" className="btn btn-sm" onClick={() => setTargets([...targets, newTarget(targets, "pushover")])}><Plus size={14} />Pushover</button>
          </div>
        </div>
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{m.routing}</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>{m.routingHint}</div>
          {!ids.length ? <span className="muted">{m.noTargets}</span> : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {ROUTE_SOURCES.map((src) => (
                <div key={src} className="row-wrap" style={{ gap: 12 }}>
                  <span style={{ width: 140 }}>{m.routes[src]}</span>
                  <ChipSelect options={ids} value={(f.notify_routes?.[src] ?? []).filter((x) => ids.includes(x))}
                    onChange={(v) => patch({ notify_routes: { ...f.notify_routes, [src]: v } })}
                    render={(id) => targets.find((t) => t.id === id)?.name ?? id} />
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="form-grid">
          <div className="field"><label>{m.maxPerDay}</label>
            <div className="row"><input type="number" min={0} max={100} className="input" value={f.push_max_per_rule_per_day}
              onChange={(e) => patch({ push_max_per_rule_per_day: Number(e.target.value) })} /><span className="muted">{m.unlimited}</span></div>
            <span className="hint">{m.maxPerDayHint}</span>
          </div>
          <div className="field"><label>{m.retention}</label>
            <div className="row"><input type="number" min={1} max={3650} className="input" value={f.retention_days_notifications}
              onChange={(e) => patch({ retention_days_notifications: Number(e.target.value) })} /><span className="muted">{all.settings.shared.daysUnit}</span></div>
          </div>
          <div className="field"><label>{m.healthAfter}</label>
            <div className="row"><input type="number" min={10} max={3600} className="input" value={f.health_alert_after_s}
              onChange={(e) => patch({ health_alert_after_s: Number(e.target.value) })} /><span className="muted">{all.settings.shared.secondsUnit}</span></div>
            <span className="hint">{m.healthAfterHint}</span>
          </div>
          <div className="field"><label><CloudRain size={14} style={{ verticalAlign: -2 }} /> {m.weather}</label>
            <div className="row">
              <input type="number" step={0.01} className="input input-sm" placeholder={m.lat} value={loc?.lat ?? ""}
                onChange={(e) => patch({ weather_location: e.target.value === "" ? null : { lat: Number(e.target.value), lon: loc?.lon ?? 0 } })} />
              <input type="number" step={0.01} className="input input-sm" placeholder={m.lon} value={loc?.lon ?? ""}
                onChange={(e) => patch({ weather_location: e.target.value === "" ? null : { lat: loc?.lat ?? 0, lon: Number(e.target.value) } })} />
            </div>
            <span className="hint">{m.weatherHint}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
