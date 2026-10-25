import { Gauge } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Range, Switch } from "../../components/ui";

/** Einstellungen → Sensoren und Vorräte (W3, Gerüst K3 – Ausbau A3+C4). */
export function SensorSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  return (
    <div className="card">
      <div className="card-head"><Gauge size={18} color="var(--accent)" /><h3>Sensoren und Vorräte</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Einkaufsliste automatisch</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.shopping_list_auto} onChange={(v) => patch({ shopping_list_auto: v })}
              label={f.shopping_list_auto ? "knappe Vorräte eintragen" : "aus"} />
          </div>
        </div>
        <div className="field"><label>Sensor-Verlauf aufbewahren</label>
          <Range value={f.retention_days_sensors} min={1} max={365} step={1} format={(v) => `${v} Tage`} onChange={(v) => patch({ retention_days_sensors: v })} />
        </div>
      </div>
    </div>
  );
}
