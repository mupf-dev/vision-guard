import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setUnauthorizedHandler } from "./lib/api";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/Login";
import { LivePage } from "./pages/Live";
import { EventsPage } from "./pages/Events";
import { IdentitiesPage } from "./pages/Identities";
import { StatsPage } from "./pages/Stats";
import { ObjectsPage } from "./pages/Objects";
import { NotificationsPage } from "./pages/Notifications";
import { ReviewPage } from "./pages/Review";
import { PetsPage } from "./pages/Pets";
import { ReportsPage } from "./pages/Reports";
import { HistoryPage } from "./pages/History";
import { SuppliesPage } from "./pages/Supplies";
import { PresencePage } from "./pages/Presence";
import { SensorsPage } from "./pages/admin/Sensors";
import { AdminLayout } from "./pages/admin/AdminLayout";
import { CamerasPage } from "./pages/admin/Cameras";
import { RulesPage } from "./pages/admin/Rules";
import { SettingsPage } from "./pages/admin/Settings";
import { SystemPage } from "./pages/admin/System";
import { TokensPage } from "./pages/admin/Tokens";
import { Loading } from "./components/ui";
import { Toasts } from "./components/Toasts";
import { useMsgs } from "./i18n";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  const m = useMsgs();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, staleTime: 5 * 60_000 });
  if (me.isLoading) return <Loading text={m.ui.checkingLogin} />;
  if (me.isError || !me.data) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return <>{children}</>;
}

export function App() {
  const qc = useQueryClient();
  const nav = useNavigate();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      qc.setQueryData(["me"], null);
      qc.removeQueries({ queryKey: ["me"] });
      if (location.pathname !== "/login") nav("/login", { replace: true, state: { from: location.pathname } });
    });
  }, [qc, nav]);

  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          <Route index element={<LivePage />} />
          <Route path="live/:cameraId" element={<LivePage />} />
          <Route path="ereignisse" element={<EventsPage />} />
          <Route path="identitaeten" element={<IdentitiesPage />} />
          <Route path="identitaeten/:identityId" element={<IdentitiesPage />} />
          <Route path="gegenstaende" element={<ObjectsPage />} />
          <Route path="hinweise" element={<NotificationsPage />} />
          <Route path="bestaetigen" element={<ReviewPage />} />
          <Route path="tiere" element={<PetsPage />} />
          <Route path="berichte" element={<ReportsPage />} />
          <Route path="zeitmaschine" element={<HistoryPage />} />
          <Route path="vorraete" element={<SuppliesPage />} />
          <Route path="anwesenheit" element={<PresencePage />} />
          <Route path="statistik" element={<StatsPage />} />
          <Route path="admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="kameras" replace />} />
            <Route path="kameras" element={<CamerasPage />} />
            <Route path="regeln" element={<RulesPage />} />
            <Route path="sensoren" element={<SensorsPage />} />
            <Route path="einstellungen" element={<SettingsPage />} />
            <Route path="system" element={<SystemPage />} />
            <Route path="zugaenge" element={<TokensPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <Toasts />
    </>
  );
}
