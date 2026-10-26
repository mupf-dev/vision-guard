import { Globe } from "lucide-react";
import type { Settings } from "../../lib/types";
import { languages, setLang, useLang, useMsgs } from "../../i18n";

/** Einstellungen → Sprache: Oberfläche (je Browser) und Systemsprache (Setting `language`, CONTRACT I18N.B). */
export function LanguageSettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const m = useMsgs().language;
  const cur = useLang();
  const system = f.languages_available?.length ? f.languages_available : languages().map((l) => ({ code: l.code, name: l.name }));
  return (
    <div className="card">
      <div className="card-head"><Globe size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.uiLabel}</label>
          <select className="select" value={cur} onChange={(e) => setLang(e.target.value)}>
            {languages().map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
          </select>
          <span className="hint">{m.uiHint}</span>
        </div>
        <div className="field"><label>{m.systemLabel}</label>
          <select className="select" value={f.language ?? "en"} onChange={(e) => patch({ language: e.target.value })}>
            {system.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
          </select>
          <span className="hint">{m.systemHint}</span>
        </div>
        <span className="hint span-2">{m.addHint}</span>
      </div>
    </div>
  );
}
