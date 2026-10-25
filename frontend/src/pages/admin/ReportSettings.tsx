import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Send } from "lucide-react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import type { Settings } from "../../lib/types";
import { fmtDateTime } from "../../lib/format";
import { ChipSelect, Switch } from "../../components/ui";
import { toast } from "../../lib/store";

const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const CHANNEL_NAME: Record<string, string> = { mqtt: "MQTT", push: "Push", webhook: "Webhook", mail: "Mail" };

const SETTINGS_KEYS = ["report_enabled", "report_time", "report_days", "report_generator", "report_channels",
  "notify_routes", "push_targets", "llm_provider", "llm_base_url", "llm_model", "mqtt_prefix"] as const;

/**
 * Einstellungen → Tagesbericht (B3): Uhrzeit, Wochentage, Text-Generator (Satzbausteine oder Sprachmodell mit
 * Datenschutzhinweis), Versandwege (MQTT, Webhook, Mail; Push über die Zuordnung „Tagesbericht“ bei den Hinweisen),
 * „Testbericht senden“ und Zustellstatus des letzten Berichts.
 */
export function ReportSettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: (p: Partial<Settings>) => void }) {
  const qc = useQueryClient();
  const ch = f.report_channels ?? { mqtt: true, webhook_url: null, mail_to: [] };
  const [mail, setMail] = useState(ch.mail_to.join(", "));
  useEffect(() => setMail((f.report_channels?.mail_to ?? []).join(", ")), [f.report_channels?.mail_to]);
  const last = useQuery({ queryKey: ["reports", "last"], queryFn: () => api.reports(1) });
  const lastReport = last.data?.items[0];
  const dirty = saved !== undefined && SETTINGS_KEYS.some((k) => JSON.stringify(f[k]) !== JSON.stringify(saved[k]));
  const test = useMutation({
    mutationFn: () => api.generateReport({ since: "today", deliver: true }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["reports"] });
      const failed = Object.entries(r.delivery).filter(([, d]) => !d.ok);
      if (failed.length) toast.error("Testbericht erstellt", failed.map(([k, d]) => `${CHANNEL_NAME[k] ?? k}: ${d.error ?? "Fehler"}`).join(" · "));
      else toast.success("Testbericht versendet", Object.keys(r.delivery).map((k) => CHANNEL_NAME[k] ?? k).join(", ") || "keine Kanäle eingerichtet");
    },
    onError: (e) => toast.error("Testbericht fehlgeschlagen", (e as Error).message),
  });
  const setCh = (p: Partial<typeof ch>) => patch({ report_channels: { ...ch, ...p } });
  const pushRoutes = f.notify_routes?.report ?? [];
  const llmMissing = f.report_generator === "llm" && f.llm_provider === "none";
  return (
    <div className="card">
      <div className="card-head"><FileText size={18} color="var(--accent)" /><h3>Tagesbericht</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Täglicher Bericht</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.report_enabled} onChange={(v) => patch({ report_enabled: v })} label={f.report_enabled ? "an" : "aus"} />
          </div>
          <span className="hint">Fertige Berichte stehen unter <Link to="/berichte">Berichte</Link>. Verpasste Berichte werden bis 6 h später nachgeholt.</span>
        </div>
        <div className="field"><label>Uhrzeit</label>
          <input type="time" className="input" style={{ width: 130 }} value={f.report_time} onChange={(e) => patch({ report_time: e.target.value })} />
          <span className="hint">Der Bericht umfasst die 24 Stunden bis zu dieser Uhrzeit.</span>
        </div>
        <div className="field span-2"><label>Wochentage</label>
          <ChipSelect options={[0, 1, 2, 3, 4, 5, 6]} value={f.report_days} render={(d) => DAYS[d]}
            onChange={(v) => patch({ report_days: [...v].sort((a, b) => a - b) })} />
        </div>
        <div className="field span-2"><label>Text erzeugen mit</label>
          <select className="input" value={f.report_generator} onChange={(e) => patch({ report_generator: e.target.value as Settings["report_generator"] })}>
            <option value="template">Satzbausteinen (ohne KI, Standard)</option>
            <option value="llm">Sprachmodell (siehe „KI-Sprachmodell“)</option>
          </select>
          <span className="hint" style={llmMissing ? { color: "var(--warn)" } : undefined}>
            {f.report_generator === "template"
              ? "Feste deutsche Sätze aus den gezählten Ereignissen – keine Daten verlassen das Haus."
              : llmMissing
                ? "Kein Sprachmodell eingerichtet – der Bericht nutzt weiter Satzbausteine."
                : <>Namen, Uhrzeiten und Zählungen (keine Bilder) gehen an das Sprachmodell{f.llm_provider === "anthropic" ? " bei Anthropic (Cloud)" : ""}. Jede Uhrzeit, Zahl und jeder Name im Text wird gegen die Fakten geprüft; bei Abweichungen oder Fehlern nutzt der Bericht Satzbausteine.</>}
          </span>
        </div>
        <div className="field"><label>MQTT</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={ch.mqtt} onChange={(v) => setCh({ mqtt: v })} label={<code>{f.mqtt_prefix}/report</code>} />
          </div>
        </div>
        <div className="field"><label>Push</label>
          <div className="dim" style={{ minHeight: 38, display: "flex", alignItems: "center", fontSize: 13 }}>
            {pushRoutes.length ? `an ${pushRoutes.join(", ")}` : "kein Ziel gewählt"}
          </div>
          <span className="hint">Ziele unter „Benachrichtigungen“ → Zuordnung „Tagesbericht“. Jeder Bericht erscheint auch unter Hinweise.</span>
        </div>
        <div className="field span-2"><label>Webhook (optional)</label>
          <input className="input mono" placeholder="https://…" value={ch.webhook_url ?? ""} onChange={(e) => setCh({ webhook_url: e.target.value || null })} />
          <span className="hint">POST mit JSON <code>{"{type, date, report_id, text, html, counts, …}"}</code>.</span>
        </div>
        <div className="field span-2"><label>Mail an (optional)</label>
          <input className="input" placeholder="anna@example.org, ben@example.org" value={mail}
            onChange={(e) => setMail(e.target.value)}
            onBlur={() => setCh({ mail_to: mail.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean).slice(0, 10) })} />
          <span className="hint">Versand über den Mailserver aus <code>.env</code> (<code>SMTP_HOST</code>, <code>SMTP_FROM</code> …).</span>
        </div>
        <div className="field span-2"><label>Test und letzter Versand</label>
          <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
            <button className="btn btn-sm" disabled={test.isPending || dirty} title={dirty ? "Erst speichern" : "Bericht für heute erstellen und über alle Kanäle senden"} onClick={() => test.mutate()}>
              {test.isPending ? <span className="spinner" /> : <Send size={14} />}Testbericht senden
            </button>
            {lastReport ? <>
              <span className="muted" style={{ fontSize: 13 }}>{fmtDateTime(lastReport.created_at)}:</span>
              {Object.keys(lastReport.delivery).length === 0 && <span className="badge outline">nicht versendet</span>}
              {Object.entries(lastReport.delivery).map(([k, d]) => (
                <span key={k} className={`badge ${d.ok ? "ok" : "bad"}`} title={d.error ?? undefined}>{CHANNEL_NAME[k] ?? k}: {d.ok ? "ok" : d.error ?? "Fehler"}</span>
              ))}
            </> : <span className="muted" style={{ fontSize: 13 }}>noch kein Bericht</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
