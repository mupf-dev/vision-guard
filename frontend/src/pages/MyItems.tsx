import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera as CamIcon, Clock, ImagePlus, Lock, MapPin, Package, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { Identity, InventoryItem } from "../lib/types";
import { fmtAgo } from "../lib/format";
import { ConfirmDialog, Empty, Img, Loading, Switch } from "../components/ui";
import { ItemTeachDialog, vocabName } from "../components/ItemTeachDialog";
import { toast } from "../lib/store";
import { useMsgs } from "../i18n";

/** Tab „Meine Gegenstände“ (W2, B4): eigene Exemplare mit Fotos, Fundort und Lernsperre. */
export function MyItems({ onPickItem }: { onPickItem: (it: InventoryItem) => void }) {
  const m = useMsgs().items;
  const c = useMsgs().common;
  const qc = useQueryClient();
  const idents = useQuery({ queryKey: ["identities", "item"], queryFn: () => api.identities({ kind: "item" }) });
  const inv = useQuery({ queryKey: ["inventory", "list", "mine"], queryFn: () => api.inventory({ limit: 1000 }) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [teach, setTeach] = useState<{ identity: Identity | null } | null>(null);
  const [del, setDel] = useState<Identity | null>(null);
  const vocabNames = useMemo(() => new Map((settings.data?.inventory_vocabulary ?? []).map((v) => [v.label, vocabName(v)])), [settings.data]);
  const where = useMemo(() => {
    const w = new Map<number, InventoryItem>();
    for (const it of inv.data?.items ?? []) {
      const id = it.identity?.id;
      if (id === undefined) continue;
      const cur = w.get(id);
      if (!cur || Number(it.visible) > Number(cur.visible) || (it.visible === cur.visible && it.last_seen > cur.last_seen)) w.set(id, it);
    }
    return w;
  }, [inv.data]);

  const lock = useMutation({
    mutationFn: ({ id, v }: { id: number; v: boolean }) => api.updateIdentity(id, { learning_locked: v }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["identities"] }),
    onError: (e: Error) => toast.error(m.notSaved, e.message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteIdentity(id),
    onSuccess: () => { toast.success(m.removed); setDel(null); qc.invalidateQueries({ queryKey: ["identities"] }); qc.invalidateQueries({ queryKey: ["inventory"] }); },
    onError: (e: Error) => toast.error(m.removeFailed, e.message),
  });

  const list = idents.data ?? [];
  return (
    <div>
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="muted" style={{ flex: 1, fontSize: 13 }}>
          {m.mineIntro1} {m.mineIntro2}
        </div>
        <button className="btn btn-primary" onClick={() => setTeach({ identity: null })}><Plus size={15} />{m.newItem}</button>
      </div>
      {idents.isLoading ? <Loading /> : list.length === 0 ? (
        <Empty icon={<Package size={44} />} title={m.noOwnItems}
          text={m.noOwnItemsText} />
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
                    {it?.visible ? <span className="badge ok"><span className="dot" />{m.stateName.present}</span> : <span className="badge outline">{it ? m.notVisible : m.notDetectedYet}</span>}
                    <span className="spacer" />
                    <span className="badge" title={m.storedImages}>{m.imagesCount(i.sample_count)}</span>
                  </div>
                </div>
                <div className="body">
                  <div className="who"><Package size={15} color={it?.visible ? "var(--accent)" : "var(--text-3)"} />{i.display_name}</div>
                  <div className="meta"><span className="muted">{vocabNames.get(i.item_label ?? "") ?? i.item_label}</span></div>
                  {it && <div className="meta"><CamIcon />{it.camera_name}{it.room && it.room !== it.camera_name ? ` · ${it.room}` : ""}</div>}
                  {it?.relation && <div className="meta"><MapPin />{it.relation.text}</div>}
                  <div className="meta" style={{ color: it?.visible ? "#4ade80" : undefined }}><Clock />
                    {it ? (it.visible ? m.visibleNow : m.lastSeen(fmtAgo(it.last_seen))) : m.notAssignedYet}
                  </div>
                  <div className="row" style={{ marginTop: 8, gap: 4, flexWrap: "wrap" }}>
                    <span title={m.lockHint} style={{ whiteSpace: "nowrap", fontSize: 12 }}>
                      <Switch checked={i.learning_locked} onChange={(v) => lock.mutate({ id: i.id, v })} label={<span className="row" style={{ gap: 4 }}><Lock size={12} />{m.learningLock}</span>} />
                    </span>
                    <span className="spacer" />
                    <button className="btn btn-sm" onClick={() => setTeach({ identity: i })}><ImagePlus size={14} />{m.images}</button>
                    <button className="btn btn-ghost btn-sm btn-icon" onClick={() => setDel(i)} aria-label={m.deleteItem} title={m.deleteItem}><Trash2 size={14} /></button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {teach && <ItemTeachDialog identity={teach.identity} onClose={() => setTeach(null)} />}
      {del && <ConfirmDialog title={m.deleteOwnTitle} busy={remove.isPending} confirmLabel={c.delete}
        text={m.deleteOwnText(del.display_name)}
        onConfirm={() => remove.mutate(del.id)} onClose={() => setDel(null)} />}
    </div>
  );
}
