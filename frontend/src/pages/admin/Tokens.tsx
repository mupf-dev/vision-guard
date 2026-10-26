import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Plus, ShieldOff, TriangleAlert } from "lucide-react";
import { api } from "../../lib/api";
import type { ApiToken, ApiTokenCreated, ApiTokenInput } from "../../lib/types";
import { ConfirmDialog, Dialog, Empty, Loading } from "../../components/ui";
import { fmtAgo, fmtShort } from "../../lib/format";
import { toast } from "../../lib/store";
import { msgs, useMsgs } from "../../i18n";

const expiryOptions = (): { days: number | null; label: string }[] => {
  const m = msgs().tokens;
  return [
    { days: null, label: m.expiryNever },
    { days: 30, label: m.expiry30 },
    { days: 90, label: m.expiry90 },
    { days: 365, label: m.expiry365 },
  ];
};

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

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  const m = useMsgs().tokens;
  return (
    <button type="button" className="btn btn-sm" onClick={async () => {
      if (await copyText(text)) { setDone(true); setTimeout(() => setDone(false), 1800); }
      else toast.error(m.copyFailed, m.copyFailedText);
    }}>
      {done ? <Check size={14} /> : <Copy size={14} />}{done ? m.copied : label ?? m.copy}
    </button>
  );
}

function scopeText(t: Pick<ApiToken, "scopes">) {
  const m = msgs().tokens;
  return t.scopes.includes("write") ? m.scopeReadWrite : m.scopeRead;
}

function StatusBadge({ t }: { t: ApiToken }) {
  const m = useMsgs().tokens;
  if (t.revoked) return <span className="badge bad">{m.revoked}</span>;
  if (t.expired) return <span className="badge warn">{m.expired}</span>;
  return <span className="badge ok">{m.active}</span>;
}

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (t: ApiTokenCreated) => void }) {
  const [f, setF] = useState<ApiTokenInput>({ name: "", scopes: ["read"], expires_days: null });
  const msg = useMsgs();
  const m = msg.tokens;
  const create = useMutation({
    mutationFn: () => api.createToken({ ...f, name: f.name.trim() }),
    onSuccess: onCreated,
    onError: (e: Error) => toast.error(m.createFailed, e.message),
  });
  const write = f.scopes.includes("write");
  return (
    <Dialog title={m.newTitle} icon={<KeyRound size={18} />} onClose={onClose} footer={<>
      <button className="btn btn-ghost" onClick={onClose}>{msg.common.cancel}</button>
      <button className="btn btn-primary" disabled={!f.name.trim() || create.isPending} onClick={() => create.mutate()}>
        {create.isPending && <span className="spinner" />}{m.create}
      </button>
    </>}>
      <form style={{ display: "flex", flexDirection: "column", gap: 16 }} onSubmit={(e) => { e.preventDefault(); if (f.name.trim()) create.mutate(); }}>
        <div className="field">
          <label>{m.name}</label>
          <input className="input" autoFocus maxLength={100} value={f.name} placeholder={m.namePlaceholder}
            onChange={(e) => setF({ ...f, name: e.target.value })} />
          <span className="hint">{m.nameHint}</span>
        </div>
        <div className="field">
          <label>{m.rights}</label>
          <div className="seg">
            <button type="button" className={!write ? "on" : ""} onClick={() => setF({ ...f, scopes: ["read"] })}>{m.scopeRead}</button>
            <button type="button" className={write ? "on" : ""} onClick={() => setF({ ...f, scopes: ["read", "write"] })}>{m.scopeReadWrite}</button>
          </div>
          <span className="hint">
            {write ? m.writeHint : m.readHint}
          </span>
        </div>
        <div className="field">
          <label>{m.expiry}</label>
          <div className="seg">
            {expiryOptions().map((o) => (
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
  const m = useMsgs().tokens;
  return (
    <Dialog title={m.createdTitle} icon={<KeyRound size={18} />} onClose={onClose} size="lg"
      footer={<button className="btn btn-primary" onClick={onClose}>{m.done}</button>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="row" style={{ gap: 10, alignItems: "flex-start", color: "#fbbf24" }}>
          <TriangleAlert size={18} style={{ flexShrink: 0, marginTop: 2 }} />
          <div>{m.showOnceA}<b>{m.showOnceB}</b>{m.showOnceC}</div>
        </div>
        <div className="field">
          <label>{m.keyFor(token.name, scopeText(token))}</label>
          <div className="row" style={{ gap: 8 }}>
            <input className="input mono" readOnly value={token.token} style={{ flex: 1 }} onFocus={(e) => e.target.select()} />
            <CopyButton text={token.token} />
          </div>
        </div>
        <div className="field">
          <label>{m.example}</label>
          <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
            <code className="mono" style={{ flex: 1, fontSize: 12, padding: "8px 10px", background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 8, overflowWrap: "anywhere" }}>{curl}</code>
            <CopyButton text={curl} label={m.command} />
          </div>
          <span className="hint">
            {m.headerHintA}<code>Authorization: Bearer …</code>{m.headerHintB}<code>{origin}/mcp</code>{m.headerHintC}
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
  const m = useMsgs().tokens;
  const revokeM = useMutation({
    mutationFn: (id: number) => api.revokeToken(id),
    onSuccess: () => { toast.success(m.revokedToast); qc.invalidateQueries({ queryKey: ["tokens"] }); setRevoke(null); },
    onError: (e: Error) => toast.error(m.revokeFailed, e.message),
  });

  const all = tokens.data ?? [];
  const inactive = all.filter((t) => t.revoked || t.expired).length;
  const list = showRevoked ? all : all.filter((t) => !t.revoked && !t.expired);

  return (
    <>
      <div className="page-header">
        <div><h1>{m.title}</h1><div className="sub">{m.sub}</div></div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={16} />{m.newAccess}</button>
      </div>

      {tokens.isLoading ? <Loading /> : tokens.isError ? (
        <div className="err">{m.loadFailed((tokens.error as Error).message)}</div>
      ) : all.length === 0 ? (
        <div className="card"><Empty icon={<KeyRound size={44} />} title={m.emptyTitle}
          text={m.emptyText} /></div>
      ) : (
        <>
          <div className="card" style={{ overflow: "auto" }}>
            <table className="table">
              <thead><tr><th>{m.colName}</th><th>{m.colPrefix}</th><th>{m.colRights}</th><th>{m.colStatus}</th><th>{m.colCreated}</th><th>{m.colLastUsed}</th><th>{m.colExpiry}</th><th /></tr></thead>
              <tbody>
                {list.map((t) => (
                  <tr key={t.id} style={t.revoked || t.expired ? { opacity: 0.6 } : undefined}>
                    <td><b>{t.name}</b></td>
                    <td className="mono">{t.prefix}…</td>
                    <td>{t.scopes.includes("write") ? <span className="badge warn">{m.scopeReadWrite}</span> : <span className="badge info">{m.scopeRead}</span>}</td>
                    <td><StatusBadge t={t} /></td>
                    <td className="muted">{fmtShort(t.created_at)}</td>
                    <td className="muted">{t.last_used_at ? fmtAgo(t.last_used_at) : m.never}</td>
                    <td className="muted">{t.revoked ? m.revokedAt(fmtShort(t.revoked_at)) : t.expires_at ? fmtShort(t.expires_at) : m.never}</td>
                    <td style={{ textAlign: "right" }}>
                      {!t.revoked && (
                        <button className="btn btn-sm btn-ghost" onClick={() => setRevoke(t)}><ShieldOff size={14} />{m.revoke}</button>
                      )}
                    </td>
                  </tr>
                ))}
                {list.length === 0 && <tr><td colSpan={8} className="muted">{m.noActive}</td></tr>}
              </tbody>
            </table>
          </div>
          {inactive > 0 && (
            <div style={{ marginTop: 12 }}>
              <button className="btn btn-sm btn-ghost" onClick={() => setShowRevoked(!showRevoked)}>
                {showRevoked ? m.hideInactive : m.showInactive(inactive)}
              </button>
            </div>
          )}
        </>
      )}

      <div className="card card-pad" style={{ marginTop: 16 }}>
        <div className="section-title">{m.notes}</div>
        <ul className="dim" style={{ margin: "6px 0 0", paddingLeft: 18, lineHeight: 1.6, fontSize: 13 }}>
          <li>{m.note1}</li>
          <li>{m.note2}</li>
          <li>{m.note3A}<i>{m.note3Settings}</i>{m.note3B}</li>
        </ul>
      </div>

      {creating && <CreateDialog onClose={() => setCreating(false)} onCreated={(t) => {
        setCreating(false); setCreated(t); qc.invalidateQueries({ queryKey: ["tokens"] });
      }} />}
      {created && <CreatedDialog token={created} onClose={() => setCreated(null)} />}
      {revoke && <ConfirmDialog title={m.revokeTitle} confirmLabel={m.revoke} busy={revokeM.isPending}
        text={<>{m.revokeTextA}<b>{revoke.name}</b>{m.revokeTextB(revoke.prefix)}</>}
        onConfirm={() => revokeM.mutate(revoke.id)} onClose={() => setRevoke(null)} />}
    </>
  );
}
