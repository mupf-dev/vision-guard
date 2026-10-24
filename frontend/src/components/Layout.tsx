import { NavLink, Outlet, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Bell, BellOff, History, LogOut, MonitorPlay, Settings2, ShieldCheck, Users } from "lucide-react";
import { api } from "../lib/api";
import { useLive, useUi } from "../lib/store";
import { useLiveSocket } from "../lib/useLiveSocket";
import { isMock } from "../lib/mock";

export function Brand() {
  return (
    <span className="brand">
      <span className="brand-logo"><ShieldCheck size={18} /></span>
      <span className="bt">VisionGuard<small>Security Ops</small></span>
    </span>
  );
}

function WsIndicator() {
  const ws = useLive((s) => s.ws);
  const text = ws === "open" ? "Live verbunden" : ws === "connecting" ? "Verbinde …" : "Getrennt";
  return <span className={`ws-ind ${ws}`} title="WebSocket-Verbindung"><span className="dot" /><span className="nav-hide-sm">{text}</span></span>;
}

export function Layout() {
  useLiveSocket(true);
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 5 * 60_000 });
  const qc = useQueryClient();
  const nav = useNavigate();
  const { eventToasts, set } = useUi();
  const logout = async () => {
    try { await api.logout(); } catch { /* ignore */ }
    qc.clear();
    nav("/login", { replace: true });
  };
  const links = [
    { to: "/", label: "Live", icon: <MonitorPlay size={17} />, end: true },
    { to: "/ereignisse", label: "Ereignisse", icon: <History size={17} /> },
    { to: "/identitaeten", label: "Identitäten", icon: <Users size={17} /> },
    { to: "/statistik", label: "Statistik", icon: <BarChart3 size={17} /> },
    { to: "/admin", label: "Administration", icon: <Settings2 size={17} /> },
  ];
  return (
    <div className="app">
      <header className="topnav">
        <NavLink to="/" className="brand-link" style={{ textDecoration: "none" }}><Brand /></NavLink>
        <nav className="navlinks">
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end} title={l.label}
              className={({ isActive }) => (isActive || (l.to === "/" && location.pathname.startsWith("/live")) ? "active" : "")}>
              {l.icon}<span>{l.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="nav-right">
          {isMock && <span className="badge warn">Mock</span>}
          <WsIndicator />
          <button className="btn btn-ghost btn-icon btn-sm" title={eventToasts ? "Alarm-Benachrichtigungen aus" : "Alarm-Benachrichtigungen an"}
            onClick={() => set({ eventToasts: !eventToasts })}>
            {eventToasts ? <Bell size={17} /> : <BellOff size={17} />}
          </button>
          <span className="user-btn nav-hide-sm">
            <span className="av">{(me.data?.username ?? "?").slice(0, 1).toUpperCase()}</span>
            <span className="dim">{me.data?.username}</span>
          </span>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={logout} title="Abmelden"><LogOut size={17} /></button>
        </div>
      </header>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
