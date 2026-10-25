import { useState } from "react";
import { BellRing, CloudRain, Plus, Send, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { NotifyRouteSource, PushTarget, Settings } from "../../lib/types";
import { ChipSelect, Switch } from "../../components/ui";
import { toast } from "../../lib/store";

/**
 * Einstellungen → Benachrichtigungen (W1, K1): Push-Ziele (ntfy/Pushover), Routen je Quelle, Tageslimit,
 * Wetter-Standort, Aufbewahrung. Geheimnisse kommen als "***" vom Backend und bleiben so, solange sie nicht
 * überschrieben werden.
 */

const ROUTE_NAME: Record<NotifyRouteSource, string> = {
  health: "Kamera-Zustand", pet: "Tiere", review: "Rückfragen", report: "Tagesbericht", sensor: "Sensoren",
};
const ID_RE = /^[a-z0-9_-]{1,40}$/;

function newTarget(existing: PushTarget[], type: PushTarget["type"]): PushTarget {
  let n = existing.length + 1;
  while (existing.some((t) => t.id === `${type}${n}`)) n++;
  return {
    id: `${type}${n}`, type, name: type === "ntfy" ? "Handy (ntfy)" : "Handy (Pushover)", enabled: true,
    server: type === "ntfy" ? "https://ntfy.sh" : null, topic: null, token: null, user_key: null, app_token: null,
  };
}

export function targetProblem(t: PushTarget): string | null {
  if (!ID_RE.test(t.id)) return "ID: nur a–z, 0–9, _ und -";
  if (!t.name.trim()) return "Name fehlt";
  if (t.type === "ntfy" && t.topic !== "***" && !/^[A-Za-z0-9_-]{1,64}$/.test(t.topic ?? "")) return "Topic fehlt (Buchstaben, Ziffern, _ und -)";
  if (t.type === "pushover" && (!t.user_key || !t.app_token)) return "User-Key und App-Token nötig";
  return null;
}

function TargetEditor({ t, onChange, onRemove }: { t: PushTarget; onChange: (t: PushTarget) => void; onRemove: () => void }) {
  const [busy, setBusy] = useState(false);
  const problem = targetProblem(t);
  const test = async () => {
    setBusy(true);
    try {
      const r = await api.pushTest({ config: t });
      if (r.ok) toast.success("Test gesendet", `Ziel „${t.name}“ – bitte am Handy prüfen.`);
      else toast.error("Test fehlgeschlagen", r.error ?? "unbekannter Fehler");
    } catch (e) {
      toast.error("Test fehlgeschlagen", (e as Error).message);
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
        <Switch checked={t.enabled} onChange={(v) => onChange({ ...t, enabled: v })} label="aktiv" />
        <button type="button" className="btn btn-sm" disabled={busy || !!problem} onClick={test} title={problem ?? "Testnachricht senden"}>
          {busy ? <span className="spinner" /> : <Send size={14} />}Test
        </button>
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={onRemove} title="Ziel entfernen"><Trash2 size={15} /></button>
      </div>
      <div className="form-grid">
        <div className="field"><label>Name</label><input className="input input-sm" value={t.name} onChange={(e) => onChange({ ...t, name: e.target.value })} /></div>
        <div className="field"><label>ID (für Regeln)</label><input className="input input-sm mono" value={t.id} onChange={(e) => onChange({ ...t, id: e.target.value.trim() })} /></div>
        {t.type === "ntfy" ? <>
          {field("server", "Server", "https://ntfy.sh")}
          {field("topic", "Topic", "vg-langes-zufaelliges-wort", true)}
          {field("token", "Access-Token (optional)", "tk_…", true)}
        </> : <>
          {field("user_key", "User-Key", "u…", true)}
          {field("app_token", "App-Token", "a…", true)}
        </>}
      </div>
      {problem && <span className="hint" style={{ color: "var(--warn)" }}>{problem}</span>}
      {t.type === "ntfy" && <span className="hint">Gespeicherte Zugangsdaten (***) gelten nur für denselben Server – wer den Server ändert, gibt Topic und Token neu ein.</span>}
      {t.type === "ntfy" && (t.server ?? "").includes("ntfy.sh") && !t.token && (
        <span className="hint">Ohne Token ist ein Topic auf ntfy.sh öffentlich lesbar – langes, zufälliges Topic wählen oder eigenen Server nutzen.</span>
      )}
    </div>
  );
}

export function NotificationSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const targets = f.push_targets ?? [];
  const setTargets = (t: PushTarget[]) => patch({ push_targets: t });
  const ids = targets.map((t) => t.id);
  const loc = f.weather_location;
  return (
    <div className="card">
      <div className="card-head"><BellRing size={18} color="var(--accent)" /><h3>Benachrichtigungen</h3></div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Push-Ziele</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
            Regeln senden über die Aktion „Push“ an ein Ziel; Zugangsdaten stehen nur hier. „Test“ prüft auch noch nicht gespeicherte Angaben.
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
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Weiterleitung je Quelle</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>Welche Meldungen ohne Regel (z. B. Kamera-Zustand) an welche Ziele gehen.</div>
          {!ids.length ? <span className="muted">Erst ein Push-Ziel anlegen.</span> : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(Object.keys(ROUTE_NAME) as NotifyRouteSource[]).map((src) => (
                <div key={src} className="row-wrap" style={{ gap: 12 }}>
                  <span style={{ width: 140 }}>{ROUTE_NAME[src]}</span>
                  <ChipSelect options={ids} value={(f.notify_routes?.[src] ?? []).filter((x) => ids.includes(x))}
                    onChange={(v) => patch({ notify_routes: { ...f.notify_routes, [src]: v } })}
                    render={(id) => targets.find((t) => t.id === id)?.name ?? id} />
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="form-grid">
          <div className="field"><label>Pushes je Regel und Tag (höchstens)</label>
            <div className="row"><input type="number" min={0} max={100} className="input" value={f.push_max_per_rule_per_day}
              onChange={(e) => patch({ push_max_per_rule_per_day: Number(e.target.value) })} /><span className="muted">0 = unbegrenzt</span></div>
            <span className="hint">Schutz vor „Hinweis-Müdigkeit“; weitere Hinweise erscheinen nur noch in der Liste.</span>
          </div>
          <div className="field"><label>Hinweise aufbewahren</label>
            <div className="row"><input type="number" min={1} max={3650} className="input" value={f.retention_days_notifications}
              onChange={(e) => patch({ retention_days_notifications: Number(e.target.value) })} /><span className="muted">Tage</span></div>
          </div>
          <div className="field"><label>Kamera-Zustand: Warnung nach</label>
            <div className="row"><input type="number" min={10} max={3600} className="input" value={f.health_alert_after_s}
              onChange={(e) => patch({ health_alert_after_s: Number(e.target.value) })} /><span className="muted">Sekunden</span></div>
            <span className="hint">So lange muss ein Problem (dunkel, verdeckt, …) bestehen, bevor gewarnt wird.</span>
          </div>
          <div className="field"><label><CloudRain size={14} style={{ verticalAlign: -2 }} /> Wetter-Standort (für Regen-Bedingungen)</label>
            <div className="row">
              <input type="number" step={0.01} className="input input-sm" placeholder="Breite" value={loc?.lat ?? ""}
                onChange={(e) => patch({ weather_location: e.target.value === "" ? null : { lat: Number(e.target.value), lon: loc?.lon ?? 0 } })} />
              <input type="number" step={0.01} className="input input-sm" placeholder="Länge" value={loc?.lon ?? ""}
                onChange={(e) => patch({ weather_location: e.target.value === "" ? null : { lat: loc?.lat ?? 0, lon: Number(e.target.value) } })} />
            </div>
            <span className="hint">Nur auf 2 Nachkommastellen gerundet an Open-Meteo. Leer = keine Wetterabfrage.</span>
          </div>
        </div>
      </div>
    </div>
  );
}
