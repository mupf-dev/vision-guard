import { useState } from "react";
import { Bot, PlugZap } from "lucide-react";
import { api } from "../../lib/api";
import type { LlmProvider, Settings } from "../../lib/types";
import { Range } from "../../components/ui";
import { toast } from "../../lib/store";

const PROVIDERS: { id: LlmProvider; name: string }[] = [
  { id: "none", name: "Aus" },
  { id: "openai_compat", name: "OpenAI-kompatibel (Ollama, llama.cpp, eigener Server)" },
  { id: "anthropic", name: "Anthropic (Cloud)" },
];

/**
 * Einstellungen → KI-Sprachmodell (W2, K2): gemeinsamer LLM-Client für den Tagesbericht (B3) und später „Frag die
 * Kamera“ (A2, Cloud-Fallback). Standard aus. Schlüssel nur per Env (LLM_API_KEY / ANTHROPIC_API_KEY in .env).
 */
export function AiSettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: (p: Partial<Settings>) => void }) {
  const [busy, setBusy] = useState(false);
  const keys = f.llm_key_configured;
  const dirty = saved !== undefined && (["llm_provider", "llm_base_url", "llm_model", "llm_timeout_s"] as const)
    .some((k) => f[k] !== saved[k]);
  const test = async () => {
    setBusy(true);
    try {
      const r = await api.llmTest();
      if (r.ok) toast.success("Sprachmodell antwortet", `${r.model ?? ""} · ${Math.round(r.ms ?? 0)} ms`);
      else toast.error("Test fehlgeschlagen", r.error ?? "unbekannter Fehler");
    } catch (e) {
      toast.error("Test fehlgeschlagen", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card">
      <div className="card-head"><Bot size={18} color="var(--accent)" /><h3>KI-Sprachmodell (optional)</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Anbieter</label>
          <select className="input" value={f.llm_provider} onChange={(e) => patch({ llm_provider: e.target.value as LlmProvider })}>
            {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <span className="hint">
            {f.llm_provider === "anthropic"
              ? <>Namen und Uhrzeiten werden an Anthropic übertragen. Schlüssel: <code>ANTHROPIC_API_KEY</code> in <code>.env</code> {keys?.anthropic ? "(gesetzt)" : "(fehlt)"}.</>
              : f.llm_provider === "openai_compat"
                ? <>Schlüssel (falls nötig): <code>LLM_API_KEY</code> in <code>.env</code> {keys?.openai_compat ? "(gesetzt)" : "(nicht gesetzt)"}.</>
                : "Ohne Sprachmodell nutzt der Tagesbericht feste Satzbausteine."}
          </span>
        </div>
        {f.llm_provider !== "none" && <>
          {f.llm_provider === "openai_compat" && (
            <div className="field"><label>Basis-URL</label>
              <input className="input mono" placeholder="http://ollama:11434/v1" value={f.llm_base_url ?? ""}
                onChange={(e) => patch({ llm_base_url: e.target.value })} />
            </div>
          )}
          <div className="field"><label>Modell</label>
            <input className="input mono" placeholder={f.llm_provider === "anthropic" ? "Modell-ID" : "z. B. qwen2.5:3b-instruct"} value={f.llm_model ?? ""}
              onChange={(e) => patch({ llm_model: e.target.value })} />
          </div>
          <div className="field"><label>Zeitlimit</label>
            <Range value={f.llm_timeout_s} min={5} max={300} step={5} format={(v) => `${v} s`} onChange={(v) => patch({ llm_timeout_s: v })} />
          </div>
          <div className="field"><label>Verbindung</label>
            <div><button className="btn btn-sm" disabled={busy || dirty} title={dirty ? "Erst speichern" : undefined} onClick={test}>
              {busy ? <span className="spinner" /> : <PlugZap size={14} />}Testen</button></div>
          </div>
        </>}
      </div>
    </div>
  );
}
