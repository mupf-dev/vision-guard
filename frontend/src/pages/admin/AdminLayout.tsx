import { NavLink, Outlet } from "react-router";
import { Cctv, Cpu, Gauge, KeyRound, SlidersHorizontal, Workflow } from "lucide-react";
import { useMsgs } from "../../i18n";

export function AdminLayout() {
  const m = useMsgs().admin;
  return (
    <div className="page">
      <div className="admin">
        <nav className="subnav">
          <div className="section-title" style={{ padding: "0 12px" }}>{m.title}</div>
          <NavLink to="kameras"><Cctv size={17} />{m.cameras}</NavLink>
          <NavLink to="regeln"><Workflow size={17} />{m.rules}</NavLink>
          <NavLink to="sensoren"><Gauge size={17} />{m.sensors}</NavLink>
          <NavLink to="einstellungen"><SlidersHorizontal size={17} />{m.settings}</NavLink>
          <NavLink to="system"><Cpu size={17} />{m.system}</NavLink>
          <NavLink to="zugaenge"><KeyRound size={17} />{m.tokens}</NavLink>
        </nav>
        <div style={{ minWidth: 0 }}><Outlet /></div>
      </div>
    </div>
  );
}
