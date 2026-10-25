import { FileText } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Switch } from "../../components/ui";

/** Einstellungen → Tagesbericht (W2, Gerüst K2 – Ausbau B3: Wochentage, Kanäle, Testbericht, Zustellstatus). */
export function ReportSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  return (
    <div className="card">
      <div className="card-head"><FileText size={18} color="var(--accent)" /><h3>Tagesbericht</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Täglicher Bericht</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.report_enabled} onChange={(v) => patch({ report_enabled: v })} label={f.report_enabled ? "an" : "aus"} />
          </div>
        </div>
        <div className="field"><label>Uhrzeit</label>
          <input type="time" className="input" style={{ width: 130 }} value={f.report_time} onChange={(e) => patch({ report_time: e.target.value })} />
        </div>
        <div className="field"><label>Text erzeugen mit</label>
          <select className="input" value={f.report_generator} onChange={(e) => patch({ report_generator: e.target.value as Settings["report_generator"] })}>
            <option value="template">Satzbausteinen (ohne KI)</option>
            <option value="llm">Sprachmodell (siehe „KI-Sprachmodell“)</option>
          </select>
        </div>
      </div>
    </div>
  );
}
