import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Plus, ShieldOff, TriangleAlert } from "lucide-react";
import { api } from "../../lib/api";
import type { ApiToken, ApiTokenCreated, ApiTokenInput } from "../../lib/types";
import { ConfirmDialog, Dialog, Empty, Loading } from "../../components/ui";
import { fmtAgo, fmtShort } from "../../lib/format";
import { toast } from "../../lib/store";

const EXPIRY_OPTIONS: { days: number | null; label: string }[] = [
  { days: null, label: "Nie" },
  { days: 30, label: "30 Tage" },
  { days: 90, label: "90 Tage" },
  { days: 365, label: "1 Jahr" },
];

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback (z. B. ohne HTTPS): verstecktes Textfeld
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

function CopyButton({ text, label = "Kopieren" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="btn btn-sm" onClick={async () => {
      if (await copyText(text)) { setDone(true); setTimeout(() => setDone(false), 1800); }
      else toast.error("Kopieren nicht möglich", "Bitte manuell markieren und kopieren.");
    }}>
      {done ? <Check size={14} /> : <Copy size={14} />}{done ? "Kopiert" : label}
    </button>
  );
}

function scopeText(t: Pick<ApiToken, "scopes">) {
  return t.scopes.includes("write") ? "Lesen + Schreiben" : "Nur lesen";
}

function StatusBadge({ t }: { t: ApiToken }) {
  if (t.revoked) return <span className="badge bad">widerrufen</span>;
  if (t.expired) return <span className="badge warn">abgelaufen</span>;
  return <span className="badge ok">aktiv</span>;
}

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (t: ApiTokenCreated) => void }) {
  const [f, setF] = useState<ApiTokenInput>({ name: "", scopes: ["read"], expires_days: null });
  const create = useMutation({
    mutationFn: () => api.createToken({ ...f, name: f.name.trim() }),
    onSuccess: onCreated,
    onError: (e: Error) => toast.error("Anlegen fehlgeschlagen", e.message),
  });
  const write = f.scopes.includes("write");
  return (
    <Dialog title="Neuer API-Zugang" icon={<KeyRound size={18} />} onClose={onClose} footer={<>
      <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
      <button className="btn btn-primary" disabled={!f.name.trim() || create.isPending} onClick={() => create.mutate()}>
        {create.isPending && <span className="spinner" />}Zugang erzeugen
      </button>
    </>}>
      <form style={{ display: "flex", flexDirection: "column", gap: 16 }} onSubmit={(e) => { e.preventDefault(); if (f.name.trim()) create.mutate(); }}>
        <div className="field">
          <label>Name</label>
          <input className="input" autoFocus maxLength={100} value={f.name} placeholder="z. B. Claude-MCP, Home Assistant"
            onChange={(e) => setF({ ...f, name: e.target.value })} />
          <span className="hint">Wofür der Zugang genutzt wird – nur zur eigenen Übersicht.</span>
        </div>
        <div className="field">
          <label>Rechte</label>
          <div className="seg">
            <button type="button" className={!write ? "on" : ""} onClick={() => setF({ ...f, scopes: ["read"] })}>Nur lesen</button>
            <button type="button" className={write ? "on" : ""} onClick={() => setF({ ...f, scopes: ["read", "write"] })}>Lesen + Schreiben</button>
          </div>
          <span className="hint">
            {write ? "Darf auch Kameras, Regeln, Identitäten und Einstellungen ändern oder löschen."
              : "Abfragen von Ereignissen, Gegenständen, Identitäten und Medien. Empfohlen für KI-Assistenten (MCP)."}
          </span>
        </div>
        <div className="field">
          <label>Ablauf</label>
          <div className="seg">
            {EXPIRY_OPTIONS.map((o) => (
              <button type="button" key={String(o.days)} className={f.expires_days === o.days ? "on" : ""}
                onClick={() => setF({ ...f, expires_days: o.days })}>{o.label}</button>
            ))}
          </div>
        </div>
      </form>
    </Dialog>
  );
}

function CreatedDialog({ token, onClose }: { token: ApiTokenCreated; onClose: () => void }) {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const curl = `curl -H "Authorization: Bearer ${token.token}" ${origin}/api/cameras`;
  return (
    <Dialog title="Zugang erstellt" icon={<KeyRound size={18} />} onClose={onClose} size="lg"
      footer={<button className="btn btn-primary" onClick={onClose}>Fertig</button>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="row" style={{ gap: 10, alignItems: "flex-start", color: "#fbbf24" }}>
          <TriangleAlert size={18} style={{ flexShrink: 0, marginTop: 2 }} />
          <div>Der Schlüssel wird <b>nur jetzt</b> angezeigt und kann später nicht erneut abgerufen werden.
            Bitte sofort kopieren und sicher aufbewahren.</div>
        </div>
        <div className="field">
          <label>API-Schlüssel für „{token.name}“ ({scopeText(token)})</label>
          <div className="row" style={{ gap: 8 }}>
            <input className="input mono" readOnly value={token.token} style={{ flex: 1 }} onFocus={(e) => e.target.select()} />
            <CopyButton text={token.token} />
          </div>
        </div>
        <div className="field">
          <label>Beispiel</label>
          <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
            <code className="mono" style={{ flex: 1, fontSize: 12, padding: "8px 10px", background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 8, overflowWrap: "anywhere" }}>{curl}</code>
            <CopyButton text={curl} label="Befehl" />
          </div>
          <span className="hint">
            Übertragung nur im Header <code>Authorization: Bearer …</code>. MCP-Clients (z. B. Claude) nutzen
            denselben Schlüssel für <code>{origin}/mcp</code>.
          </span>
        </div>
      </div>
    </Dialog>
  );
}

export function TokensPage() {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: api.tokens });
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [revoke, setRevoke] = useState<ApiToken | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);
  const revokeM = useMutation({
    mutationFn: (id: number) => api.revokeToken(id),
    onSuccess: () => { toast.success("Zugang widerrufen"); qc.invalidateQueries({ queryKey: ["tokens"] }); setRevoke(null); },
    onError: (e: Error) => toast.error("Widerrufen fehlgeschlagen", e.message),
  });

  const all = tokens.data ?? [];
  const inactive = all.filter((t) => t.revoked || t.expired).length;
  const list = showRevoked ? all : all.filter((t) => !t.revoked && !t.expired);

  return (
    <>
      <div className="page-header">
        <div><h1>API-Zugänge</h1><div className="sub">Schlüssel für externe Programme (REST-API, MCP, Home Assistant …)</div></div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={16} />Neuer Zugang</button>
      </div>

      {tokens.isLoading ? <Loading /> : tokens.isError ? (
        <div className="err">Zugänge konnten nicht geladen werden: {(tokens.error as Error).message}</div>
      ) : all.length === 0 ? (
        <div className="card"><Empty icon={<KeyRound size={44} />} title="Keine API-Zugänge"
          text="Mit einem API-Zugang können Programme wie ein KI-Assistent (MCP) oder Home Assistant VisionGuard abfragen – auch von außerhalb." /></div>
      ) : (
        <>
          <div className="card" style={{ overflow: "auto" }}>
            <table className="table">
              <thead><tr><th>Name</th><th>Kennung</th><th>Rechte</th><th>Status</th><th>Erstellt</th><th>Zuletzt genutzt</th><th>Ablauf</th><th /></tr></thead>
              <tbody>
                {list.map((t) => (
                  <tr key={t.id} style={t.revoked || t.expired ? { opacity: 0.6 } : undefined}>
                    <td><b>{t.name}</b></td>
                    <td className="mono">{t.prefix}…</td>
                    <td>{t.scopes.includes("write") ? <span className="badge warn">Lesen + Schreiben</span> : <span className="badge info">Nur lesen</span>}</td>
                    <td><StatusBadge t={t} /></td>
                    <td className="muted">{fmtShort(t.created_at)}</td>
                    <td className="muted">{t.last_used_at ? fmtAgo(t.last_used_at) : "nie"}</td>
                    <td className="muted">{t.revoked ? `widerrufen ${fmtShort(t.revoked_at)}` : t.expires_at ? fmtShort(t.expires_at) : "nie"}</td>
                    <td style={{ textAlign: "right" }}>
                      {!t.revoked && (
                        <button className="btn btn-sm btn-ghost" onClick={() => setRevoke(t)}><ShieldOff size={14} />Widerrufen</button>
                      )}
                    </td>
                  </tr>
                ))}
                {list.length === 0 && <tr><td colSpan={8} className="muted">Keine aktiven Zugänge.</td></tr>}
              </tbody>
            </table>
          </div>
          {inactive > 0 && (
            <div style={{ marginTop: 12 }}>
              <button className="btn btn-sm btn-ghost" onClick={() => setShowRevoked(!showRevoked)}>
                {showRevoked ? "Widerrufene/abgelaufene ausblenden" : `Widerrufene/abgelaufene anzeigen (${inactive})`}
              </button>
            </div>
          )}
        </>
      )}

      <div className="card card-pad" style={{ marginTop: 16 }}>
        <div className="section-title">Hinweise</div>
        <ul className="dim" style={{ margin: "6px 0 0", paddingLeft: 18, lineHeight: 1.6, fontSize: 13 }}>
          <li>Schlüssel werden nur als Prüfsumme gespeichert. Verlorene Schlüssel widerrufen und neu erzeugen.</li>
          <li>API-Zugänge können keine weiteren Zugänge verwalten und keine Live-Videos (go2rtc) öffnen.</li>
          <li>Bilder und Clips erhalten externe Programme als zeitlich begrenzte, signierte Links
            (Gültigkeit unter <i>Einstellungen</i> → „Gültigkeit signierter Links“).</li>
        </ul>
      </div>

      {creating && <CreateDialog onClose={() => setCreating(false)} onCreated={(t) => {
        setCreating(false); setCreated(t); qc.invalidateQueries({ queryKey: ["tokens"] });
      }} />}
      {created && <CreatedDialog token={created} onClose={() => setCreated(null)} />}
      {revoke && <ConfirmDialog title="Zugang widerrufen?" confirmLabel="Widerrufen" busy={revokeM.isPending}
        text={<>„<b>{revoke.name}</b>“ ({revoke.prefix}…) funktioniert danach sofort nicht mehr. Das lässt sich nicht rückgängig machen.</>}
        onConfirm={() => revokeM.mutate(revoke.id)} onClose={() => setRevoke(null)} />}
    </>
  );
}
