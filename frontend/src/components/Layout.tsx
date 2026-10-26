import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Bell, BellOff, BellRing, History, LogOut, MonitorPlay, Settings2, ShieldCheck, Users } from "lucide-react";
import { Check, ChevronDown, CircleHelp, Clock, FileText, Footprints, Globe, Menu, Package, PawPrint, ShoppingCart } from "lucide-react";
import { api } from "../lib/api";
import { useLive, useUi } from "../lib/store";
import { useLiveSocket } from "../lib/useLiveSocket";
import { isMock } from "../lib/mock";
import { AlertBanner } from "./AlertBanner";
import { languages, setLang, useLang, useMsgs } from "../i18n";

export function Brand() {
  const m = useMsgs();
  return (
    <span className="brand">
      <span className="brand-logo"><ShieldCheck size={18} /></span>
      <span className="bt">VisionGuard<small>{m.common.appTagline}</small></span>
    </span>
  );
}

/** W2: offene Rückfragen („Bitte bestätigen“) als Zähler in der Kopfzeile; WS `review` aktualisiert. */
function ReviewCounter() {
  const q = useQuery({ queryKey: ["reviews", "count"], queryFn: () => api.reviews({ status: "open", limit: 1 }), refetchInterval: 120_000 });
  const n = q.data?.open_count ?? 0;
  const m = useMsgs();
  return (
    <NavLink to="/bestaetigen" className="btn btn-ghost btn-icon btn-sm" title={n ? m.nav.openQuestions(n) : m.nav.pleaseConfirm} style={{ position: "relative" }}>
      <CircleHelp size={17} />
      {n > 0 && <span className="badge warn" style={{ position: "absolute", top: -4, right: -6, fontSize: 10, padding: "0 5px" }}>{n}</span>}
    </NavLink>
  );
}

function WsIndicator() {
  const ws = useLive((s) => s.ws);
  const m = useMsgs();
  const text = ws === "open" ? m.nav.wsOpen : ws === "connecting" ? m.nav.wsConnecting : m.nav.wsClosed;
  return <span className={`ws-ind ${ws}`} title={m.nav.wsTitle}><span className="dot" /><span className="nav-hide-sm">{text}</span></span>;
}

type NavItem = { to: string; label: string; icon: ReactNode; end?: boolean };

// R1: Hauptpunkte immer sichtbar (mit Beschriftung), seltene Seiten unter „Mehr“; mobil ein Menü mit allen Punkten.
// Pfade bleiben deutsch (stabile Links), nur die Beschriftung ist übersetzt.
function useNavItems() {
  const m = useMsgs().nav;
  const PRIMARY: NavItem[] = [
    { to: "/", label: m.live, icon: <MonitorPlay size={17} />, end: true },
    { to: "/ereignisse", label: m.events, icon: <History size={17} /> },
    { to: "/identitaeten", label: m.identities, icon: <Users size={17} /> },
    { to: "/gegenstaende", label: m.items, icon: <Package size={17} /> },
    { to: "/hinweise", label: m.notifications, icon: <BellRing size={17} /> },
  ];
  const MORE: NavItem[] = [
    { to: "/anwesenheit", label: m.presence, icon: <Footprints size={17} /> },
    { to: "/tiere", label: m.pets, icon: <PawPrint size={17} /> },
    { to: "/vorraete", label: m.supplies, icon: <ShoppingCart size={17} /> },
    { to: "/berichte", label: m.reports, icon: <FileText size={17} /> },
    { to: "/zeitmaschine", label: m.timeMachine, icon: <Clock size={17} /> },
    { to: "/statistik", label: m.stats, icon: <BarChart3 size={17} /> },
  ];
  const ADMIN: NavItem = { to: "/admin", label: m.admin, icon: <Settings2 size={17} /> };
  return { PRIMARY, MORE, ADMIN, ALL: [...PRIMARY, ...MORE, ADMIN] };
}

function isActive(item: NavItem, path: string): boolean {
  if (item.to === "/") return path === "/" || path.startsWith("/live");
  return path === item.to || path.startsWith(item.to + "/");
}

/** Klappmenü, das sich bei Klick außerhalb, Escape und Seitenwechsel schließt. */
function useDropdown() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  return { open, setOpen, ref };
}

function MainNav() {
  const path = useLocation().pathname;
  const m = useMsgs().nav;
  const { PRIMARY, MORE, ADMIN, ALL } = useNavItems();
  const more = useDropdown();
  const mobile = useDropdown();
  const moreActive = MORE.find((m) => isActive(m, path));
  const current = ALL.find((m) => isActive(m, path));
  const link = (l: NavItem) => (
    <NavLink key={l.to} to={l.to} end={l.end} title={l.label} className={isActive(l, path) ? "active" : ""}>
      {l.icon}<span>{l.label}</span>
    </NavLink>
  );
  return (
    <>
      <nav className="navlinks" aria-label={m.mainNav}>
        {PRIMARY.map(link)}
        <div className="nav-more" ref={more.ref}>
          <button type="button" className={`nav-more-btn${moreActive ? " active" : ""}`} aria-expanded={more.open}
            aria-haspopup="menu" onClick={() => more.setOpen(!more.open)} title={m.morePages}>
            {moreActive ? moreActive.icon : null}<span>{moreActive ? moreActive.label : m.more}</span><ChevronDown size={15} />
          </button>
          {more.open && <div className="nav-menu" role="menu">{MORE.map(link)}</div>}
        </div>
        {link(ADMIN)}
      </nav>
      <div className="nav-mobile" ref={mobile.ref}>
        <button type="button" className="nav-more-btn active" aria-expanded={mobile.open} aria-haspopup="menu"
          onClick={() => mobile.setOpen(!mobile.open)} title={m.menu}>
          <Menu size={18} /><span>{current?.label ?? (path.startsWith("/bestaetigen") ? m.pleaseConfirm : m.menu)}</span><ChevronDown size={15} />
        </button>
        {mobile.open && <div className="nav-menu nav-menu-mobile" role="menu">{ALL.map(link)}</div>}
      </div>
    </>
  );
}

/** Sprachwahl in der Kopfzeile (Liste aus i18n/locales). */
function LanguageMenu() {
  const dd = useDropdown();
  const cur = useLang();
  const m = useMsgs().nav;
  return (
    <div className="nav-more" ref={dd.ref}>
      <button type="button" className="btn btn-ghost btn-sm" aria-haspopup="menu" aria-expanded={dd.open}
        title={m.language} onClick={() => dd.setOpen(!dd.open)}>
        <Globe size={16} /><span style={{ textTransform: "uppercase", fontSize: 12 }}>{cur}</span>
      </button>
      {dd.open && (
        <div className="nav-menu" role="menu" style={{ right: 0, left: "auto", minWidth: 160 }}>
          {languages().map((l) => (
            <button key={l.code} type="button" role="menuitemradio" aria-checked={l.code === cur}
              className={l.code === cur ? "active" : ""} onClick={() => { dd.setOpen(false); setLang(l.code); }}>
              {l.code === cur ? <Check size={15} /> : <span style={{ width: 15 }} />}<span>{l.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Layout() {
  const m = useMsgs().nav;
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
  return (
    <div className="app">
      <header className="topnav">
        <NavLink to="/" className="brand-link" style={{ textDecoration: "none" }}><Brand /></NavLink>
        <MainNav />
        <div className="nav-right">
          {isMock && <span className="badge warn">Mock</span>}
          <WsIndicator />
          <ReviewCounter />
          <button className="btn btn-ghost btn-icon btn-sm" title={eventToasts ? m.alertsOff : m.alertsOn}
            onClick={() => set({ eventToasts: !eventToasts })}>
            {eventToasts ? <Bell size={17} /> : <BellOff size={17} />}
          </button>
          <LanguageMenu />
          <span className="user-btn nav-hide-sm">
            <span className="av">{(me.data?.username ?? "?").slice(0, 1).toUpperCase()}</span>
            <span className="dim">{me.data?.username}</span>
          </span>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={logout} title={m.logout}><LogOut size={17} /></button>
        </div>
      </header>
      <main className="main">
        <AlertBanner />
        <Outlet />
      </main>
    </div>
  );
}
