import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { api } from "../../lib/api";
import { Empty, Loading } from "../../components/ui";

/**
 * Administration → Virtuelle Sensoren (W3, Platzhalter aus K3 – Umsetzung A3+C4): Liste mit Zustand/seit, Editor
 * (Bereich im ZoneEditor, Art, Parameter, Referenzen aufnehmen, „Jetzt prüfen“, Verlauf).
 * Daten: api.sensors(), api.createSensor(), api.updateSensor(), api.evaluateSensor(), api.sensorHistory(),
 * api.sensorReferences(), api.addSensorReference().
 */
export function SensorsPage() {
  const q = useQuery({ queryKey: ["sensors"], queryFn: () => api.sensors() });
  if (q.isLoading) return <Loading />;
  return (
    <div>
      <div className="page-header">
        <div><h1>Virtuelle Sensoren</h1><div className="sub">Bildbereiche als Schalter: Licht an/aus, Fenster offen, Vorrat knapp</div></div>
      </div>
      {!q.data?.length ? (
        <Empty icon={<Gauge size={44} />} title="Noch keine Sensoren" text="Sensoren werten einen Bildbereich aus (Helligkeit, Blinken, Referenzbilder, Gegenstände zählen, Füllstand, Bildmodell)." />
      ) : (
        <div className="card"><div className="card-body">
          {q.data.map((s) => <div key={s.id}>{s.name} ({s.camera_name}): {s.state_text || s.state}</div>)}
        </div></div>
      )}
    </div>
  );
}
