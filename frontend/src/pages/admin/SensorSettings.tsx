import { Gauge } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Range, Switch } from "../../components/ui";
import { useMsgs } from "../../i18n";

/** Einstellungen → Sensoren und Vorräte (W3, Gerüst K3 – Ausbau A3+C4). */
export function SensorSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.sensors;
  return (
    <div className="card">
      <div className="card-head"><Gauge size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.shoppingAuto}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.shopping_list_auto} onChange={(v) => patch({ shopping_list_auto: v })}
              label={f.shopping_list_auto ? m.shoppingAutoOn : all.common.off} />
          </div>
        </div>
        <div className="field"><label>{m.retention}</label>
          <Range value={f.retention_days_sensors} min={1} max={365} step={1} format={(v) => all.settings.shared.days(v)} onChange={(v) => patch({ retention_days_sensors: v })} />
        </div>
        <div className="hint muted span-2" style={{ fontSize: 12 }}>
          {m.hintPre}<code>{m.prefix}/sensor/&lt;name&gt;</code>, <code>{m.prefix}/shopping_list</code>{m.hintPost}
        </div>
      </div>
    </div>
  );
}
