import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera as CamIcon, Clock, ImagePlus, Lock, MapPin, Package, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { Identity, InventoryItem } from "../lib/types";
import { fmtAgo } from "../lib/format";
import { ConfirmDialog, Empty, Img, Loading, Switch } from "../components/ui";
import { ItemTeachDialog } from "../components/ItemTeachDialog";
import { toast } from "../lib/store";

/** Tab „Meine Gegenstände“ (W2, B4): eigene Exemplare mit Fotos, Fundort und Lernsperre. */
export function MyItems({ onPickItem }: { onPickItem: (it: InventoryItem) => void }) {
  const qc = useQueryClient();
  const idents = useQuery({ queryKey: ["identities", "item"], queryFn: () => api.identities({ kind: "item" }) });
  const inv = useQuery({ queryKey: ["inventory", "list", "mine"], queryFn: () => api.inventory({ limit: 1000 }) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [teach, setTeach] = useState<{ identity: Identity | null } | null>(null);
  const [del, setDel] = useState<Identity | null>(null);
  const vocabName = useMemo(() => new Map((settings.data?.inventory_vocabulary ?? []).map((v) => [v.label, v.name_de])), [settings.data]);
  const where = useMemo(() => {
    const m = new Map<number, InventoryItem>();
    for (const it of inv.data?.items ?? []) {
      const id = it.identity?.id;
      if (id === undefined) continue;
      const cur = m.get(id);
      if (!cur || Number(it.visible) > Number(cur.visible) || (it.visible === cur.visible && it.last_seen > cur.last_seen)) m.set(id, it);
    }
    return m;
  }, [inv.data]);

  const lock = useMutation({
    mutationFn: ({ id, v }: { id: number; v: boolean }) => api.updateIdentity(id, { learning_locked: v }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["identities"] }),
    onError: (e: Error) => toast.error("Nicht gespeichert", e.message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteIdentity(id),
    onSuccess: () => { toast.success("Gegenstand entfernt"); setDel(null); qc.invalidateQueries({ queryKey: ["identities"] }); qc.invalidateQueries({ queryKey: ["inventory"] }); },
    onError: (e: Error) => toast.error("Entfernen fehlgeschlagen", e.message),
  });

  const list = idents.data ?? [];
  return (
    <div>
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="muted" style={{ flex: 1, fontSize: 13 }}>
          Eigene Exemplare werden erkannt, sobald die Kamera die passende Art sieht (z. B. „Annas Rucksack“ statt „Rucksack“).
          Fremde Gegenstände gleicher Art bleiben unbenannt.
        </div>
        <button className="btn btn-primary" onClick={() => setTeach({ identity: null })}><Plus size={15} />Neuer Gegenstand</button>
      </div>
      {idents.isLoading ? <Loading /> : list.length === 0 ? (
        <Empty icon={<Package size={44} />} title="Noch keine eigenen Gegenstände"
          text="Legen Sie z. B. Ihren Rucksack an: Art wählen, Name vergeben, 3–5 Fotos hochladen oder einen Kamera-Ausschnitt übernehmen." />
      ) : (
        <div className="ev-grid">
          {list.map((i, k) => {
            const it = where.get(i.id);
            const lc = it?.visible ? "var(--accent)" : "var(--line-2)";
            return (
              <div key={i.id} className="ev-card" style={{ ["--lc" as string]: lc, animationDelay: `${Math.min(k, 20) * 18}ms` }}>
                <div className="media" style={{ aspectRatio: "4 / 3", cursor: it ? "pointer" : undefined }} onClick={() => it && onPickItem(it)}>
                  <Img src={i.cover_url} alt={i.display_name}
                    fallback={<div style={{ height: "100%", display: "grid", placeItems: "center", color: "var(--text-3)" }}><Package size={40} /></div>} />
                  <div className="corner">
                    {it?.visible ? <span className="badge ok"><span className="dot" />sichtbar</span> : <span className="badge outline">{it ? "nicht sichtbar" : "noch nicht erkannt"}</span>}
                    <span className="spacer" />
                    <span className="badge" title="gespeicherte Bilder">{i.sample_count} Bilder</span>
                  </div>
                </div>
                <div className="body">
                  <div className="who"><Package size={15} color={it?.visible ? "var(--accent)" : "var(--text-3)"} />{i.display_name}</div>
                  <div className="meta"><span className="muted">{vocabName.get(i.item_label ?? "") ?? i.item_label}</span></div>
                  {it && <div className="meta"><CamIcon />{it.camera_name}{it.room && it.room !== it.camera_name ? ` · ${it.room}` : ""}</div>}
                  {it?.relation && <div className="meta"><MapPin />{it.relation.text}</div>}
                  <div className="meta" style={{ color: it?.visible ? "#4ade80" : undefined }}><Clock />
                    {it ? (it.visible ? "aktuell sichtbar" : `zuletzt gesehen ${fmtAgo(it.last_seen)}`) : "bisher von keiner Kamera zugeordnet"}
                  </div>
                  <div className="row" style={{ marginTop: 8, gap: 6 }}>
                    <span title="Lernsperre: erkennen ja, aber keine neuen Bilder automatisch speichern">
                      <Switch checked={i.learning_locked} onChange={(v) => lock.mutate({ id: i.id, v })} label={<><Lock size={12} /> Lernen gesperrt</>} />
                    </span>
                    <span className="spacer" />
                    <button className="btn btn-sm" onClick={() => setTeach({ identity: i })}><ImagePlus size={14} />Bilder</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setDel(i)} aria-label="Gegenstand löschen"><Trash2 size={14} /></button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {teach && <ItemTeachDialog identity={teach.identity} onClose={() => setTeach(null)} />}
      {del && <ConfirmDialog title="Eigenen Gegenstand löschen?" busy={remove.isPending}
        text={<>„{del.display_name}“ und alle Bilder werden gelöscht. Die erkannten Gegenstände bleiben, aber ohne Namen.</>}
        onConfirm={() => remove.mutate(del.id)} onClose={() => setDel(null)} />}
    </div>
  );
}
