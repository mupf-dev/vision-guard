import { useState } from "react";
import { Bot, PlugZap } from "lucide-react";
import { api } from "../../lib/api";
import type { LlmProvider, Settings } from "../../lib/types";
import { Range } from "../../components/ui";
import { toast } from "../../lib/store";
import { msgs, useMsgs } from "../../i18n";

const PROVIDERS = (): { id: LlmProvider; name: string }[] => {
  const m = msgs().settings.ai;
  return [
    { id: "none", name: m.providerNone },
    { id: "openai_compat", name: m.providerOpenai },
    { id: "anthropic", name: m.providerAnthropic },
  ];
};

/**
 * Einstellungen → KI-Sprachmodell (W2, K2): gemeinsamer LLM-Client für den Tagesbericht (B3) und später „Frag die
 * Kamera“ (A2, Cloud-Fallback). Standard aus. Schlüssel nur per Env (LLM_API_KEY / ANTHROPIC_API_KEY in .env).
 */
export function AiSettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.ai;
  const [busy, setBusy] = useState(false);
  const keys = f.llm_key_configured;
  const dirty = saved !== undefined && (["llm_provider", "llm_base_url", "llm_model", "llm_timeout_s"] as const)
    .some((k) => f[k] !== saved[k]);
  const test = async () => {
    setBusy(true);
    try {
      const r = await api.llmTest();
      if (r.ok) toast.success(msgs().settings.ai.toastOk, `${r.model ?? ""} · ${Math.round(r.ms ?? 0)} ms`);
      else toast.error(msgs().settings.shared.testFailed, r.error ?? msgs().common.errorUnknown);
    } catch (e) {
      toast.error(msgs().settings.shared.testFailed, (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card">
      <div className="card-head"><Bot size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.provider}</label>
          <select className="input" value={f.llm_provider} onChange={(e) => patch({ llm_provider: e.target.value as LlmProvider })}>
            {PROVIDERS().map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <span className="hint">
            {f.llm_provider === "anthropic"
              ? <>{m.anthropicHintPre} <code>ANTHROPIC_API_KEY</code> {m.keyIn} <code>.env</code> {keys?.anthropic ? m.keySet : m.keyMissing}.</>
              : f.llm_provider === "openai_compat"
                ? <>{m.openaiHintPre} <code>LLM_API_KEY</code> {m.keyIn} <code>.env</code> {keys?.openai_compat ? m.keySet : m.keyNotSet}.</>
                : m.noneHint}
          </span>
        </div>
        {f.llm_provider !== "none" && <>
          {f.llm_provider === "openai_compat" && (
            <div className="field"><label>{m.baseUrl}</label>
              <input className="input mono" placeholder="http://ollama:11434/v1" value={f.llm_base_url ?? ""}
                onChange={(e) => patch({ llm_base_url: e.target.value })} />
            </div>
          )}
          <div className="field"><label>{m.model}</label>
            <input className="input mono" placeholder={f.llm_provider === "anthropic" ? m.modelIdPlaceholder : m.modelPlaceholder} value={f.llm_model ?? ""}
              onChange={(e) => patch({ llm_model: e.target.value })} />
          </div>
          <div className="field"><label>{m.timeout}</label>
            <Range value={f.llm_timeout_s} min={5} max={300} step={5} format={(v) => `${v} s`} onChange={(v) => patch({ llm_timeout_s: v })} />
          </div>
          <div className="field"><label>{m.connection}</label>
            <div><button className="btn btn-sm" disabled={busy || dirty} title={dirty ? all.settings.shared.saveFirst : undefined} onClick={test}>
              {busy ? <span className="spinner" /> : <PlugZap size={14} />}{m.test}</button></div>
          </div>
        </>}
      </div>
    </div>
  );
}
