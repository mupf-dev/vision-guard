import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Send } from "lucide-react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import type { Settings } from "../../lib/types";
import { fmtDateTime } from "../../lib/format";
import { ChipSelect, Switch } from "../../components/ui";
import { toast } from "../../lib/store";
import { DAYS } from "../../lib/labels";
import { msgs, useMsgs } from "../../i18n";

const channelName = (k: string) => msgs().settings.report.channels[k] ?? k;

const SETTINGS_KEYS = ["report_enabled", "report_time", "report_days", "report_generator", "report_channels",
  "notify_routes", "push_targets", "llm_provider", "llm_base_url", "llm_model", "mqtt_prefix"] as const;

/**
 * Einstellungen → Tagesbericht (B3): Uhrzeit, Wochentage, Text-Generator (Satzbausteine oder Sprachmodell mit
 * Datenschutzhinweis), Versandwege (MQTT, Webhook, Mail; Push über die Zuordnung „Tagesbericht“ bei den Hinweisen),
 * „Testbericht senden“ und Zustellstatus des letzten Berichts.
 */
export function ReportSettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: (p: Partial<Settings>) => void }) {
  const qc = useQueryClient();
  const all = useMsgs();
  const m = all.settings.report;
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
      const t = msgs();
      if (failed.length) toast.error(t.settings.report.toastCreated, failed.map(([k, d]) => `${channelName(k)}: ${d.error ?? t.common.error}`).join(" · "));
      else toast.success(t.settings.report.toastSent, Object.keys(r.delivery).map(channelName).join(", ") || t.settings.report.noChannels);
    },
    onError: (e) => toast.error(msgs().settings.report.toastFailed, (e as Error).message),
  });
  const setCh = (p: Partial<typeof ch>) => patch({ report_channels: { ...ch, ...p } });
  const pushRoutes = f.notify_routes?.report ?? [];
  const llmMissing = f.report_generator === "llm" && f.llm_provider === "none";
  return (
    <div className="card">
      <div className="card-head"><FileText size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.daily}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.report_enabled} onChange={(v) => patch({ report_enabled: v })} label={f.report_enabled ? all.common.on : all.common.off} />
          </div>
          <span className="hint">{m.dailyHintPre} <Link to="/berichte">{m.dailyHintLink}</Link>{m.dailyHintPost}</span>
        </div>
        <div className="field"><label>{m.time}</label>
          <input type="time" className="input" style={{ width: 130 }} value={f.report_time} onChange={(e) => patch({ report_time: e.target.value })} />
          <span className="hint">{m.timeHint}</span>
        </div>
        <div className="field span-2"><label>{m.weekdays}</label>
          <ChipSelect options={[0, 1, 2, 3, 4, 5, 6]} value={f.report_days} render={(d) => DAYS[d]}
            onChange={(v) => patch({ report_days: [...v].sort((a, b) => a - b) })} />
        </div>
        <div className="field span-2"><label>{m.generator}</label>
          <select className="input" value={f.report_generator} onChange={(e) => patch({ report_generator: e.target.value as Settings["report_generator"] })}>
            <option value="template">{m.genTemplate}</option>
            <option value="llm">{m.genLlm}</option>
          </select>
          <span className="hint" style={llmMissing ? { color: "var(--warn)" } : undefined}>
            {f.report_generator === "template"
              ? m.templateHint
              : llmMissing
                ? m.llmMissing
                : m.llmHint(f.llm_provider === "anthropic")}
          </span>
        </div>
        <div className="field"><label>MQTT</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={ch.mqtt} onChange={(v) => setCh({ mqtt: v })} label={<code>{f.mqtt_prefix}/report</code>} />
          </div>
        </div>
        <div className="field"><label>{m.push}</label>
          <div className="dim" style={{ minHeight: 38, display: "flex", alignItems: "center", fontSize: 13 }}>
            {pushRoutes.length ? m.pushTo(pushRoutes.join(", ")) : m.noTarget}
          </div>
          <span className="hint">{m.pushHint}</span>
        </div>
        <div className="field span-2"><label>{m.webhook}</label>
          <input className="input mono" placeholder="https://…" value={ch.webhook_url ?? ""} onChange={(e) => setCh({ webhook_url: e.target.value || null })} />
          <span className="hint">{m.webhookHint} <code>{"{type, date, report_id, text, html, counts, …}"}</code>.</span>
        </div>
        <div className="field span-2"><label>{m.mail}</label>
          <input className="input" placeholder="anna@example.org, ben@example.org" value={mail}
            onChange={(e) => setMail(e.target.value)}
            onBlur={() => setCh({ mail_to: mail.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean).slice(0, 10) })} />
          <span className="hint">{m.mailHintPre} <code>.env</code> (<code>SMTP_HOST</code>, <code>SMTP_FROM</code> …).</span>
        </div>
        <div className="field span-2"><label>{m.test}</label>
          <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
            <button className="btn btn-sm" disabled={test.isPending || dirty} title={dirty ? all.settings.shared.saveFirst : m.testTitle} onClick={() => test.mutate()}>
              {test.isPending ? <span className="spinner" /> : <Send size={14} />}{m.sendTest}
            </button>
            {lastReport ? <>
              <span className="muted" style={{ fontSize: 13 }}>{fmtDateTime(lastReport.created_at)}:</span>
              {Object.keys(lastReport.delivery).length === 0 && <span className="badge outline">{m.notSent}</span>}
              {Object.entries(lastReport.delivery).map(([k, d]) => (
                <span key={k} className={`badge ${d.ok ? "ok" : "bad"}`} title={d.error ?? undefined}>{channelName(k)}: {d.ok ? m.ok : d.error ?? all.common.error}</span>
              ))}
            </> : <span className="muted" style={{ fontSize: 13 }}>{m.noReport}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
