import { CircleHelp } from "lucide-react";
import type { Settings } from "../../lib/types";
import { Range, Switch } from "../../components/ui";
import { fmtNum, useMsgs } from "../../i18n";

/** Einstellungen → Rückfragen & Exemplare (W2, Gerüst K2 – Ausbau D2/B4). */
export function ReviewSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.review;
  return (
    <div className="card">
      <div className="card-head"><CircleHelp size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.enabled}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.review_enabled} onChange={(v) => patch({ review_enabled: v })} label={f.review_enabled ? m.enabledOn : all.common.off} />
          </div>
        </div>
        <div className="field"><label>{m.maxPerDay}</label>
          <Range value={f.review_max_per_day} min={0} max={50} step={1} format={(v) => `${v}`} onChange={(v) => patch({ review_max_per_day: v })} />
        </div>
        <div className="field"><label>{m.expire}</label>
          <Range value={f.review_expire_h} min={1} max={168} step={1} format={(v) => `${v} h`} onChange={(v) => patch({ review_expire_h: v })} />
          <span className="hint">{m.expireHint}</span>
        </div>
        <div className="field"><label>{m.maxOpen}</label>
          <Range value={f.review_max_open} min={1} max={100} step={1} format={(v) => `${v}`} onChange={(v) => patch({ review_max_open: v })} />
        </div>
        <div className="field"><label>{m.band}</label>
          <Range value={f.review_band} min={0.02} max={0.3} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => patch({ review_band: v })} />
          <span className="hint">{m.bandHint}</span>
        </div>
        <div className="field"><label>{m.autoTune}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.review_auto_tune} onChange={(v) => patch({ review_auto_tune: v })} label={f.review_auto_tune ? m.autoTuneOn : all.common.off} />
          </div>
          <span className="hint">{m.autoTuneHint(fmtNum(0.05, 2))}</span>
        </div>
        <div className="field"><label>{m.itemThreshold}</label>
          <Range value={f.item_match_threshold} min={0.3} max={0.9} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => patch({ item_match_threshold: v })} />
          <span className="hint">{m.itemThresholdHint(fmtNum(0.15, 2))}</span>
        </div>
        <div className="field"><label>{m.itemMinPx}</label>
          <Range value={f.item_min_px} min={0} max={300} step={10} format={(v) => `${v} px`} onChange={(v) => patch({ item_min_px: v })} />
          <span className="hint">{m.itemMinPxHint}</span>
        </div>
        <div className="field span-2"><span className="hint">{m.pushHint}</span></div>
      </div>
    </div>
  );
}
