import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calendar, Car, Clock, Combine, Eye, Fingerprint, Images, PawPrint, Pencil, Save, Search, Trash2, User, X } from "lucide-react";
import { api, asPaged } from "../lib/api";
import type { Identity, IdentityKind, VGEvent } from "../lib/types";
import { KIND_NAME, KIND_PLURAL } from "../lib/labels";
import { fmtAgo, fmtDateTime } from "../lib/format";
import { ConfirmDialog, Dialog, Empty, Img, Loading, SearchSelect } from "../components/ui";
import { EventDetailDialog, Plate } from "../components/EventBits";
import { EventGrid } from "./Events";
import { toast } from "../lib/store";

const KINDS: { k: IdentityKind; icon: React.ReactNode }[] = [
  { k: "face", icon: <User size={16} /> },
  { k: "plate", icon: <Car size={16} /> },
  { k: "pet", icon: <PawPrint size={16} /> },
];

function KindIcon({ kind, size = 16 }: { kind: IdentityKind; size?: number }) {
  return kind === "face" ? <User size={size} /> : kind === "plate" ? <Car size={size} /> : <PawPrint size={size} />;
}

function IdCard({ i, onClick, delay }: { i: Identity; onClick: () => void; delay: number }) {
  return (
    <div className="id-card" onClick={onClick} role="button" tabIndex={0} onKeyDown={(k) => k.key === "Enter" && onClick()} style={{ animationDelay: `${delay}ms` }}>
      <div className="cover">
        <Img src={i.cover_url} fallback={<div style={{ height: "100%", display: "grid", placeItems: "center", color: "var(--line-2)" }}><KindIcon kind={i.kind} size={56} /></div>} />
        {!i.name && <span className="badge warn">unbenannt</span>}
        <div className="nm">{i.display_name}</div>
      </div>
      <div className="info">
        <span title="Samples"><Images />{i.sample_count}</span>
        <span title="Ereignisse"><Eye />{i.event_count}</span>
        <span title={fmtDateTime(i.last_seen)}><Clock />{fmtAgo(i.last_seen)}</span>
      </div>
    </div>
  );
}

function IdentityDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["identity", id], queryFn: () => api.identity(id) });
  const samples = useQuery({ queryKey: ["samples", id], queryFn: () => api.samples(id) });
  const events = useQuery({ queryKey: ["identity-events", id], queryFn: async () => asPaged(await api.identityEvents(id, { limit: 24 })) });
  const all = useQuery({ queryKey: ["identities", "all"], queryFn: () => api.identities({}) });
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeTarget, setMergeTarget] = useState<number[]>([]);
  const [confirmDel, setConfirmDel] = useState(false);
  const [picked, setPicked] = useState<VGEvent | null>(null);
  const ident = q.data;
  useEffect(() => { if (ident) { setName(ident.name ?? ""); setNotes(ident.notes ?? ""); } }, [ident]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["identities"] });
    qc.invalidateQueries({ queryKey: ["identity", id] });
    qc.invalidateQueries({ queryKey: ["events"] });
  };
  const save = useMutation({
    mutationFn: () => api.updateIdentity(id, { name: name.trim() || null, notes }),
    onSuccess: (r) => { toast.success("Gespeichert", r?.display_name); setEditing(false); invalidate(); },
    onError: (e: Error) => toast.error("Speichern fehlgeschlagen", e.message),
  });
  const delSample = useMutation({
    mutationFn: (sid: number) => api.deleteSample(id, sid),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["samples", id] }); invalidate(); },
    onError: (e: Error) => toast.error("Sample nicht gelöscht", e.message),
  });
  const merge = useMutation({
    mutationFn: () => api.mergeIdentity(id, mergeTarget[0]),
    onSuccess: () => {
      const target = all.data?.find((x) => x.id === mergeTarget[0]);
      toast.success("Zusammengeführt", `in ${target?.display_name ?? "#" + mergeTarget[0]}`);
      invalidate();
      setMergeOpen(false);
      onClose();
    },
    onError: (e: Error) => toast.error("Zusammenführen fehlgeschlagen", e.message),
  });
  const del = useMutation({
    mutationFn: () => api.deleteIdentity(id),
    onSuccess: () => { toast.success("Identität gelöscht"); invalidate(); onClose(); },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });

  const targets = useMemo(
    () => (all.data ?? []).filter((x) => x.id !== id && (!ident || x.kind === ident.kind))
      .map((x) => ({ value: x.id, label: x.display_name, image: x.cover_url, sub: `${x.sample_count} Samples · zuletzt ${fmtAgo(x.last_seen)}` })),
    [all.data, id, ident],
  );

  return (
    <Dialog size="xl" onClose={onClose} icon={ident && <KindIcon kind={ident.kind} size={18} />}
      title={ident?.display_name ?? "Identität"}
      footer={<>
        <button className="btn btn-danger" onClick={() => setConfirmDel(true)} disabled={!ident}><Trash2 size={16} />Löschen</button>
        <button className="btn" onClick={() => setMergeOpen(true)} disabled={!ident}><Combine size={16} />Zusammenführen …</button>
        <span className="spacer" />
        <button className="btn btn-primary" onClick={onClose}>Schließen</button>
      </>}>
      {!ident ? <Loading /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div className="id-hero">
            <Img src={ident.cover_url} className="avatar lg" />
            <div className="meta">
              {editing ? (
                <div className="form-grid">
                  <div className="field"><label>Name</label>
                    <input className="input" autoFocus value={name} placeholder={`leer = automatisch (${ident.kind === "face" ? "Person" : ident.kind === "pet" ? "Tier" : "Kennzeichen"} #…)`} onChange={(e) => setName(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && save.mutate()} />
                  </div>
                  <div className="field span-2"><label>Notizen</label>
                    <textarea className="textarea prose" value={notes} onChange={(e) => setNotes(e.target.value)} />
                  </div>
                  <div className="row span-2">
                    <button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? <span className="spinner" /> : <Save size={16} />}Speichern</button>
                    <button className="btn btn-ghost" onClick={() => { setEditing(false); setName(ident.name ?? ""); setNotes(ident.notes ?? ""); }}><X size={16} />Abbrechen</button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="row">
                    <h1>{ident.display_name}</h1>
                    {!ident.name && <span className="badge warn">unbenannt</span>}
                    <span className="badge outline">{KIND_NAME[ident.kind]}</span>
                    <button className="btn btn-sm" onClick={() => setEditing(true)}><Pencil size={14} />Umbenennen</button>
                  </div>
                  {ident.plate_text && <div><Plate text={ident.plate_text} /></div>}
                  <div className="row-wrap dim" style={{ gap: 18, fontSize: 13 }}>
                    <span className="row" style={{ gap: 6 }}><Calendar size={14} />erstmals {fmtDateTime(ident.first_seen)}</span>
                    <span className="row" style={{ gap: 6 }}><Clock size={14} />zuletzt {fmtDateTime(ident.last_seen)}</span>
                    <span className="row" style={{ gap: 6 }}><Images size={14} />{ident.sample_count} Samples</span>
                    <span className="row" style={{ gap: 6 }}><Eye size={14} />{ident.event_count} Ereignisse</span>
                  </div>
                  <div className={ident.notes ? "" : "muted"} style={{ whiteSpace: "pre-wrap" }}>{ident.notes || "Keine Notizen."}</div>
                </>
              )}
            </div>
          </div>

          <section>
            <div className="section-title">Samples ({samples.data?.length ?? 0})</div>
            {samples.isLoading ? <Loading /> : samples.data?.length ? (
              <div className="samples">
                {samples.data.map((s) => (
                  <div className="sample" key={s.id} title={fmtDateTime(s.created_at)}>
                    <Img src={s.image_url} />
                    <button className="btn btn-danger btn-icon btn-sm" title="Sample löschen" onClick={() => delSample.mutate(s.id)}><Trash2 size={13} /></button>
                  </div>
                ))}
              </div>
            ) : <div className="muted">Keine Samples.</div>}
            <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>Falsch zugeordnete Samples löschen verbessert die Wiedererkennung.</div>
          </section>

          <section>
            <div className="section-title">Ereignisverlauf ({events.data?.total ?? 0})</div>
            {events.isLoading ? <Loading /> : events.data?.items.length ? <EventGrid items={events.data.items} onPick={setPicked} /> : <div className="muted">Keine Ereignisse.</div>}
          </section>
        </div>
      )}
      {mergeOpen && ident && (
        <Dialog title="Identität zusammenführen" icon={<Combine size={18} />} onClose={() => setMergeOpen(false)} footer={<>
          <button className="btn btn-ghost" onClick={() => setMergeOpen(false)}>Abbrechen</button>
          <button className="btn btn-primary" disabled={!mergeTarget.length || merge.isPending} onClick={() => merge.mutate()}>
            {merge.isPending ? <span className="spinner" /> : <Combine size={16} />}Zusammenführen
          </button>
        </>}>
          <p className="dim" style={{ marginTop: 0 }}>
            Alle Samples und Ereignisse von <b>{ident.display_name}</b> werden auf die Ziel-Identität übertragen. <b>{ident.display_name}</b> wird anschließend gelöscht.
          </p>
          <div className="field"><label>Ziel-Identität</label>
            <SearchSelect inline options={targets} value={mergeTarget} onChange={setMergeTarget} placeholder="Identität suchen …" />
          </div>
        </Dialog>
      )}
      {confirmDel && ident && <ConfirmDialog title="Identität löschen?" busy={del.isPending}
        text={<>„{ident.display_name}“ mit allen {ident.sample_count} Samples wird gelöscht. Ereignisse bleiben erhalten, verlieren aber die Zuordnung.</>}
        onConfirm={() => del.mutate()} onClose={() => setConfirmDel(false)} />}
      {picked && <EventDetailDialog event={picked} onClose={() => setPicked(null)} />}
    </Dialog>
  );
}

export function IdentitiesPage() {
  const { identityId } = useParams();
  const nav = useNavigate();
  const [kind, setKind] = useState<IdentityKind>((sessionStorage.getItem("vg.idkind") as IdentityKind) ?? "face");
  const [named, setNamed] = useState<"" | "true" | "false">("");
  const [search, setSearch] = useState("");
  useEffect(() => sessionStorage.setItem("vg.idkind", kind), [kind]);
  const all = useQuery({ queryKey: ["identities", "all"], queryFn: () => api.identities({}) });
  const list = useMemo(() => {
    let l = (all.data ?? []).filter((i) => i.kind === kind);
    if (named) l = l.filter((i) => !!i.name === (named === "true"));
    if (search) l = l.filter((i) => `${i.display_name} ${i.plate_text ?? ""} ${i.notes}`.toLowerCase().includes(search.toLowerCase()));
    return [...l].sort((a, b) => ((a.last_seen ?? "") < (b.last_seen ?? "") ? 1 : -1));
  }, [all.data, kind, named, search]);
  const countOf = (k: IdentityKind) => all.data?.filter((i) => i.kind === k).length ?? 0;

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Identitäten</h1><div className="sub">Wiedererkannte Personen, Fahrzeuge und Tiere – benennen, zusammenführen, bereinigen.</div></div>
        <div className="row">
          <div className="input-group" style={{ width: 240 }}><Search size={15} />
            <input className="input input-sm" placeholder="Suchen …" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="seg">
            <button className={named === "" ? "on" : ""} onClick={() => setNamed("")}>Alle</button>
            <button className={named === "true" ? "on" : ""} onClick={() => setNamed("true")}>Benannt</button>
            <button className={named === "false" ? "on" : ""} onClick={() => setNamed("false")}>Unbenannt</button>
          </div>
        </div>
      </div>
      <div className="tabs">
        {KINDS.map(({ k, icon }) => (
          <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{icon}{KIND_PLURAL[k]}<span className="count">{countOf(k)}</span></button>
        ))}
      </div>
      {all.isLoading ? <Loading /> : list.length === 0 ? (
        <Empty icon={<Fingerprint size={44} />} title={`Keine ${KIND_PLURAL[kind]}`} text="Identitäten werden automatisch angelegt, sobald die Erkennung Gesichter, Kennzeichen oder Tiere sieht." />
      ) : (
        <div className="id-grid">
          {list.map((i, n) => <IdCard key={i.id} i={i} delay={Math.min(n, 20) * 20} onClick={() => nav(`/identitaeten/${i.id}`)} />)}
        </div>
      )}
      {identityId && <IdentityDetail key={identityId} id={Number(identityId)} onClose={() => nav("/identitaeten")} />}
    </div>
  );
}
