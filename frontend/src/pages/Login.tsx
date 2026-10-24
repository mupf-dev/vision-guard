import { useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Lock, LogIn, User } from "lucide-react";
import { api } from "../lib/api";
import { Brand } from "../components/Layout";

export function LoginPage() {
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const qc = useQueryClient();
  const from = (loc.state as { from?: string } | null)?.from ?? "/";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.login(username, password);
      qc.setQueryData(["me"], r);
      nav(from === "/login" ? "/" : from, { replace: true });
    } catch (e) {
      setErr(e instanceof Error && (e as { status?: number }).status === 401 ? "Benutzername oder Passwort falsch." : `Anmeldung fehlgeschlagen: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-grid-bg" />
      <div className="card login-card">
        <div className="row" style={{ justifyContent: "center" }}><Brand /></div>
        <p className="muted" style={{ textAlign: "center", margin: 0 }}>KI-Kameraüberwachung · Anmeldung</p>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="u">Benutzername</label>
            <div className="input-group"><User size={15} />
              <input id="u" className="input" autoFocus autoComplete="username" value={username} onChange={(e) => setU(e.target.value)} required />
            </div>
          </div>
          <div className="field">
            <label htmlFor="p">Passwort</label>
            <div className="input-group"><Lock size={15} />
              <input id="p" type="password" className="input" autoComplete="current-password" value={password} onChange={(e) => setP(e.target.value)} required />
            </div>
          </div>
          {err && <div className="err">{err}</div>}
          <button className="btn btn-primary" style={{ height: 42, marginTop: 6 }} disabled={busy}>
            {busy ? <span className="spinner" /> : <LogIn size={17} />} Anmelden
          </button>
        </form>
      </div>
    </div>
  );
}
