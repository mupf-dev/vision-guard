import { NavLink, Outlet } from "react-router";
import { Cctv, Cpu, KeyRound, SlidersHorizontal, Workflow } from "lucide-react";

export function AdminLayout() {
  return (
    <div className="page">
      <div className="admin">
        <nav className="subnav">
          <div className="section-title" style={{ padding: "0 12px" }}>Administration</div>
          <NavLink to="kameras"><Cctv size={17} />Kameras</NavLink>
          <NavLink to="regeln"><Workflow size={17} />Regeln</NavLink>
          <NavLink to="einstellungen"><SlidersHorizontal size={17} />Einstellungen</NavLink>
          <NavLink to="system"><Cpu size={17} />System</NavLink>
          <NavLink to="zugaenge"><KeyRound size={17} />API-Zugänge</NavLink>
        </nav>
        <div style={{ minWidth: 0 }}><Outlet /></div>
      </div>
    </div>
  );
}
