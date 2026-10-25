import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Calendar, Car, Check, Clock, Combine, Eye, Fingerprint, Hand, History, Images, Lightbulb, ListChecks, Lock, PawPrint, Pencil,
  Save, Search, Trash2, User, X,
} from "lucide-react";
import { api, asPaged } from "../lib/api";
import type { Identity, IdentityKind, IdentitySuggestion, Sample, VGEvent } from "../lib/types";
import { KIND_NAME, KIND_PLURAL } from "../lib/labels";
import { fmtAgo, fmtDateTime } from "../lib/format";
import { ConfirmDialog, Dialog, Empty, Img, Loading, SearchSelect, Switch } from "../components/ui";
import { EventDetailDialog, Plate } from "../components/EventBits";
import { EventGrid } from "./Events";
import { toast } from "../lib/store";
import { BulkDeleteDialog, BulkMergeDialog, MergeHistoryDialog, SelectionBar, similarIds, useNightlyPairs } from "./IdentityBulk";

const KINDS: { k: IdentityKind; icon: React.ReactNode }[] = [
  { k: "face", icon: <User size={16} /> },
  { k: "plate", icon: <Car size={16} /> },
  { k: "pet", icon: <PawPrint size={16} /> },
];

function KindIcon({ kind, size = 16 }: { kind: IdentityKind; size?: number }) {
  return kind === "face" ? <User size={size} /> : kind === "plate" ? <Car size={size} /> : <PawPrint size={size} />;
}

/** Lernsperre ist technisch für alle Arten möglich, der Schalter erscheint aber nur bei Gesichtern und Tieren. */
const LOCKABLE: IdentityKind[] = ["face", "pet"];
const MAX_SAMPLES = 20;
const fmtScore = (v: number) => v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* ---------- Zusammenführen in zwei Schritten ---------- */

function MergeDialog({ ident, initialTarget, onClose, onDone }: {
  ident: Identity; initialTarget?: number; onClose: () => void; onDone: (sourceDeleted: boolean, target: Identity) => void;
}) {
  const [step, setStep] = useState<1 | 2>(initialTarget ? 2 : 1);
  const [target, setTarget] = useState<number[]>(initialTarget ? [initialTarget] : []);
  const [showUnnamed, setShowUnnamed] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [initFor, setInitFor] = useState<number | null>(null);
  const all = useQuery({ queryKey: ["identities", "all"], queryFn: () => api.identities({}) });
  const sugg = useQuery({
    queryKey: ["identity-suggestions", ident.id, 5],
    queryFn: () => api.identitySuggestions(ident.id, 5),
    enabled: LOCKABLE.includes(ident.kind),
  });
  const srcSamples = useQuery({ queryKey: ["samples", ident.id], queryFn: () => api.samples(ident.id) });
  const targetId = target[0];
  const tgtSamples = useQuery({ queryKey: ["samples", targetId], queryFn: () => api.samples(targetId), enabled: !!targetId });
  const tgt = all.data?.find((x) => x.id === targetId);
  const sourceUnknown = !ident.name;

  const scoreOf = useMemo(() => new Map((sugg.data ?? []).map((x) => [x.identity_id, x.score])), [sugg.data]);
  const options = useMemo(
    () => (all.data ?? [])
      .filter((x) => x.id !== ident.id && x.kind === ident.kind && (!sourceUnknown || showUnnamed || !!x.name))
      .sort((a, b) => (scoreOf.get(b.id) ?? -1) - (scoreOf.get(a.id) ?? -1))
      .map((x) => ({
        value: x.id, label: x.display_name, image: x.cover_url,
        sub: `${scoreOf.has(x.id) ? `Ähnlichkeit ${fmtScore(scoreOf.get(x.id)!)} · ` : ""}${x.sample_count} Bilder${x.learning_locked ? " · Lernen gesperrt" : ""}`,
      })),
    [all.data, ident, sourceUnknown, showUnnamed, scoreOf],
  );

  // Vorauswahl beim Wechsel zu Schritt 2: Top 5 nach Qualität, bei gesperrtem Ziel keine
  useEffect(() => {
    if (step !== 2 || !targetId || !srcSamples.data || !tgt || initFor === targetId) return;
    const top = tgt.learning_locked ? [] : [...srcSamples.data].sort((a, b) => b.quality - a.quality).slice(0, 5).map((x) => x.id);
    setPicked(new Set(top));
    setInitFor(targetId);
  }, [step, targetId, srcSamples.data, tgt, initFor]);

  const src = srcSamples.data ?? [];
  const noSamples = srcSamples.isSuccess && src.length === 0;
  const full = noSamples || (src.length > 0 && picked.size === src.length);
  const tgtManual = (tgtSamples.data ?? []).filter((x) => x.manual).length;
  const tgtTotal = tgtSamples.data?.length ?? tgt?.sample_count ?? 0;
  const manualAfter = tgtManual + picked.size;
  const tooMany = manualAfter > MAX_SAMPLES;

  const merge = useMutation({
    mutationFn: () => api.mergeIdentity(ident.id, targetId, noSamples ? null : [...picked]),
    onSuccess: (r) => {
      toast.success(full ? "Zusammengeführt" : "Bilder übernommen",
        full ? `in ${r.display_name}` : `${picked.size} ${picked.size === 1 ? "Bild" : "Bilder"} → ${r.display_name}`);
      onDone(full, r);
    },
    onError: (e: Error) => toast.error("Zusammenführen fehlgeschlagen", e.message),
  });

  const toggle = (sid: number) => setPicked((prev) => {
    const n = new Set(prev);
    if (n.has(sid)) n.delete(sid); else n.add(sid);
    return n;
  });

  const pickSuggestion = (x: IdentitySuggestion) => { setTarget([x.identity_id]); setStep(2); };

  return (
    <Dialog size="lg" title={step === 1 ? "Zusammenführen: Ziel wählen" : `Zusammenführen: Bilder für ${tgt?.display_name ?? "Ziel"}`}
      icon={<Combine size={18} />} onClose={onClose} footer={step === 1 ? <>
        <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>Schritt 1 von 2</span>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
        <button className="btn btn-primary" disabled={!targetId} onClick={() => setStep(2)}>Weiter</button>
      </> : <>
        <button className="btn btn-ghost" onClick={() => setStep(1)}><ArrowLeft size={16} />Zurück</button>
        <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>Schritt 2 von 2</span>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
        <button className="btn btn-primary" disabled={!targetId || (!noSamples && picked.size === 0) || tooMany || merge.isPending || !srcSamples.isSuccess}
          onClick={() => merge.mutate()}>
          {merge.isPending ? <span className="spinner" /> : <Combine size={16} />}
          {full ? "Zusammenführen" : `${picked.size} ${picked.size === 1 ? "Bild" : "Bilder"} übernehmen`}
        </button>
      </>}>
      {step === 1 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {!!sugg.data?.length && (
            <section>
              <div className="section-title">Vorschläge</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {sugg.data.map((x) => (
                  <button key={x.identity_id} className={`btn ${targetId === x.identity_id ? "btn-primary" : ""}`}
                    style={{ justifyContent: "flex-start", height: "auto", padding: "6px 10px", gap: 10 }}
                    onClick={() => pickSuggestion(x)} title="Als Ziel wählen und weiter">
                    <Img src={x.cover_url} className="avatar" />
                    <span style={{ fontWeight: 600 }}>{x.display_name}</span>
                    <span className="spacer" />
                    {x.likely && <span className="badge accent">wahrscheinlich</span>}
                    <span className="badge outline">Ähnlichkeit {fmtScore(x.score)}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
          <div className="field"><label>Ziel-Identität{sourceUnknown && !showUnnamed ? " (nur bekannte)" : ""}</label>
            <SearchSelect inline options={options} value={target} onChange={setTarget} placeholder="Identität suchen …" />
          </div>
          {sourceUnknown && (
            <Switch checked={showUnnamed} onChange={setShowUnnamed} label="Auch unbenannte Identitäten als Ziel anzeigen" />
          )}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="dim" style={{ fontSize: 13, padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg-1)" }}>
            {noSamples ? (
              <><b>{ident.display_name}</b> hat keine Bilder. Beim Zusammenführen wandern alle Ereignisse zu <b>{tgt?.display_name}</b>, und <b>{ident.display_name}</b> wird gelöscht.</>
            ) : full ? (
              <>Alle Bilder ausgewählt: <b>{ident.display_name}</b> wird vollständig mit <b>{tgt?.display_name}</b> zusammengeführt. Alle Ereignisse wandern mit, <b>{ident.display_name}</b> wird anschließend gelöscht.</>
            ) : (
              <>Nur die ausgewählten Bilder gehen zu <b>{tgt?.display_name}</b> (als „manuell“ markiert, sie werden beim Lernen nicht verdrängt).
                {" "}<b>Die Ereignisse bleiben bei {ident.display_name}</b>, ebenso die übrigen Bilder.</>
            )}
            {tgt?.learning_locked && <div style={{ marginTop: 6 }}><Lock size={12} /> {tgt.display_name} ist für das Lernen gesperrt – die Übernahme ist trotzdem möglich.</div>}
          </div>
          {!noSamples && (
            <>
              <div className="row">
                <span className="dim" style={{ fontSize: 13 }}>
                  {picked.size} von {src.length} ausgewählt · Ziel: {tgtTotal} Bilder, danach {manualAfter} manuelle von max. {MAX_SAMPLES}
                </span>
                <span className="spacer" />
                <button className="btn btn-sm btn-ghost" onClick={() => setPicked(new Set(src.map((x) => x.id)))}>Alle</button>
                <button className="btn btn-sm btn-ghost" onClick={() => setPicked(new Set())}>Keine</button>
              </div>
              {tooMany && <div className="badge bad" style={{ alignSelf: "flex-start" }}>max. {MAX_SAMPLES} manuelle Bilder im Ziel – weniger auswählen oder dort erst alte löschen</div>}
              {srcSamples.isLoading ? <Loading /> : (
                <div className="samples">
                  {src.map((x) => <PickSample key={x.id} s={x} on={picked.has(x.id)} onToggle={() => toggle(x.id)} />)}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </Dialog>
  );
}

function PickSample({ s, on, onToggle }: { s: Sample; on: boolean; onToggle: () => void }) {
  return (
    <div className="sample" role="checkbox" aria-checked={on} tabIndex={0} title={`Qualität ${fmtScore(s.quality)} · ${fmtDateTime(s.created_at)}`}
      onClick={onToggle} onKeyDown={(k) => (k.key === " " || k.key === "Enter") && (k.preventDefault(), onToggle())}
      style={{ cursor: "pointer", outline: on ? "2px solid var(--accent)" : undefined, outlineOffset: -2, opacity: on ? 1 : 0.55 }}>
      <Img src={s.image_url} />
      <span style={{
        position: "absolute", top: 5, left: 5, width: 20, height: 20, borderRadius: 6, display: "grid", placeItems: "center",
        background: on ? "var(--accent)" : "rgba(10,15,23,.7)", border: "1px solid var(--line-2)", color: "#0a0f17",
      }}>{on && <Check size={14} strokeWidth={3} />}</span>
      {s.manual && <span className="badge info" style={{ position: "absolute", bottom: 4, left: 4, fontSize: 10, background: "rgba(10,15,23,.85)" }}>manuell</span>}
    </div>
  );
}

function IdCard({ i, onClick, delay, selecting, selected, onSelect }: {
  i: Identity; onClick: () => void; delay: number;
  /** Paket A: Auswahlmodus – Klick (bzw. Leertaste) schaltet das Häkchen, Umschalt-Klick wählt einen Bereich */
  selecting?: boolean; selected?: boolean; onSelect?: (shift: boolean) => void;
}) {
  const act = (shift: boolean) => (selecting && onSelect ? onSelect(shift) : onClick());
  return (
    <div className={`id-card ${selecting ? "selecting" : ""} ${selected ? "selected" : ""}`} role={selecting ? "checkbox" : "button"}
      aria-checked={selecting ? !!selected : undefined} tabIndex={0} data-id={i.id}
      onMouseDown={(e) => { if (e.shiftKey && selecting) e.preventDefault(); }}
      onClick={(e) => act(e.shiftKey)}
      onKeyDown={(k) => {
        if (k.key === "Enter" || (selecting && k.key === " ")) { k.preventDefault(); act(k.shiftKey); }
      }} style={{ animationDelay: `${delay}ms` }}>
      <div className="cover">
        {onSelect && (
          <span className="sel-check" role="presentation" title={selected ? "Abwählen" : "Auswählen (Umschalt: Bereich)"}
            onMouseDown={(e) => e.shiftKey && e.preventDefault()}
            onClick={(e) => { e.stopPropagation(); onSelect(e.shiftKey); }}>
            {selected && <Check size={15} strokeWidth={3} />}
          </span>
        )}
        <Img src={i.cover_url} fallback={<div style={{ height: "100%", display: "grid", placeItems: "center", color: "var(--line-2)" }}><KindIcon kind={i.kind} size={56} /></div>} />
        {!i.name && <span className="badge warn">unbenannt</span>}
        {i.learning_locked && (
          <span className="badge outline" title="Lernen gesperrt" style={{ position: "absolute", top: 8, right: 8, left: "auto", zIndex: 1, background: "rgba(10,15,23,.7)" }}><Lock size={12} /></span>
        )}
        {i.suggestion && (
          <span className="badge accent" title={`Ähnlichkeit ${fmtScore(i.suggestion.score)}`} style={{ position: "absolute", top: 36, left: 8, zIndex: 1 }}>ähnelt {i.suggestion.display_name}</span>
        )}
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
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState(false);
  const [mergeOpen, setMergeOpen] = useState<false | { target?: number }>(false);
  const [askLock, setAskLock] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [picked, setPicked] = useState<VGEvent | null>(null);
  const ident = q.data;
  const lockable = !!ident && LOCKABLE.includes(ident.kind);
  const sugg = useQuery({
    queryKey: ["identity-suggestions", id, 3],
    queryFn: () => api.identitySuggestions(id, 3),
    enabled: lockable && !ident?.name,
  });
  const topSugg = sugg.data?.find((x) => x.likely);
  useEffect(() => { if (ident) { setName(ident.name ?? ""); setNotes(ident.notes ?? ""); } }, [ident]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["identities"] });
    qc.invalidateQueries({ queryKey: ["identity", id] });
    qc.invalidateQueries({ queryKey: ["events"] });
    qc.invalidateQueries({ queryKey: ["identity-suggestions"] });
  };
  const save = useMutation({
    mutationFn: () => api.updateIdentity(id, { name: name.trim() || null, notes }),
    onSuccess: (r) => {
      toast.success("Gespeichert", r?.display_name);
      setEditing(false);
      // Erstes Benennen: Sperre vorschlagen, damit die Person nicht durch Fehltreffer „verwässert“
      if (ident && !ident.name && r?.name && LOCKABLE.includes(r.kind) && !r.learning_locked) setAskLock(true);
      invalidate();
    },
    onError: (e: Error) => toast.error("Speichern fehlgeschlagen", e.message),
  });
  const lock = useMutation({
    mutationFn: (v: boolean) => api.updateIdentity(id, { learning_locked: v }),
    onSuccess: (r) => { toast.success(r.learning_locked ? "Lernen gesperrt" : "Lernen freigegeben", r.display_name); setAskLock(false); invalidate(); },
    onError: (e: Error) => toast.error("Ändern fehlgeschlagen", e.message),
  });
  const delSample = useMutation({
    mutationFn: (sid: number) => api.deleteSample(id, sid),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["samples", id] }); invalidate(); },
    onError: (e: Error) => toast.error("Sample nicht gelöscht", e.message),
  });
  const onMerged = (sourceDeleted: boolean, target: Identity) => {
    invalidate();
    qc.invalidateQueries({ queryKey: ["samples"] });
    qc.invalidateQueries({ queryKey: ["identity", target.id] });
    qc.invalidateQueries({ queryKey: ["identity-events"] });
    setMergeOpen(false);
    if (sourceDeleted) onClose();
  };
  const del = useMutation({
    mutationFn: () => api.deleteIdentity(id),
    onSuccess: () => { toast.success("Identität gelöscht"); invalidate(); onClose(); },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });


  return (
    <Dialog size="xl" onClose={onClose} icon={ident && <KindIcon kind={ident.kind} size={18} />}
      title={ident?.display_name ?? "Identität"}
      footer={<>
        <button className="btn btn-danger" onClick={() => setConfirmDel(true)} disabled={!ident}><Trash2 size={16} />Löschen</button>
        <button className="btn" onClick={() => setMergeOpen({})} disabled={!ident}><Combine size={16} />Zusammenführen …</button>
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
                    {ident.learning_locked && <span className="badge outline" title="Lernen gesperrt"><Lock size={12} />gesperrt</span>}
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
                  {lockable && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <Switch checked={ident.learning_locked} disabled={lock.isPending} onChange={(v) => lock.mutate(v)}
                        label={<span className="row" style={{ gap: 6 }}><Lock size={14} />Lernen gesperrt</span>} />
                      <span className="muted" style={{ fontSize: 12 }}>
                        Wird weiter erkannt und Ereignissen zugeordnet, speichert aber keine neuen Bilder automatisch.
                        Neue Bilder kommen dann nur noch über „Zusammenführen“ hinzu.
                      </span>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {topSugg && (
            <div className="row" style={{ padding: "10px 14px", borderRadius: 10, border: "1px solid rgba(45,212,191,.35)", background: "var(--accent-soft)" }}>
              <Lightbulb size={16} />
              <Img src={topSugg.cover_url} className="avatar" />
              <span>Ähnlich wie <b>{topSugg.display_name}</b> ({fmtScore(topSugg.score)})</span>
              <span className="spacer" />
              <button className="btn btn-sm btn-primary" onClick={() => setMergeOpen({ target: topSugg.identity_id })}><Combine size={14} />Zusammenführen …</button>
            </div>
          )}

          <section>
            <div className="section-title">Samples ({samples.data?.length ?? 0})</div>
            {samples.isLoading ? <Loading /> : samples.data?.length ? (
              <div className="samples">
                {samples.data.map((s) => (
                  <div className="sample" key={s.id} title={`${fmtDateTime(s.created_at)}${s.manual ? " · manuell übernommen" : ""}`}>
                    <Img src={s.image_url} />
                    {s.manual && <span className="badge info" style={{ position: "absolute", bottom: 4, left: 4, fontSize: 10, background: "rgba(10,15,23,.85)" }} title="manuell übernommen – wird beim Lernen nicht verdrängt"><Hand size={10} />manuell</span>}
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
        <MergeDialog ident={ident} initialTarget={mergeOpen.target} onClose={() => setMergeOpen(false)} onDone={onMerged} />
      )}
      {askLock && ident && (
        <ConfirmDialog title="Jetzt Lernen sperren?" danger={false} confirmLabel="Sperren" busy={lock.isPending}
          text={<>„{ident.name ?? ident.display_name}“ ist jetzt benannt. Mit gesperrtem Lernen wird die Person weiter erkannt,
            aber keine neuen Bilder werden automatisch gespeichert – so schleichen sich keine Fehltreffer ein.</>}
          onConfirm={() => lock.mutate(true)} onClose={() => setAskLock(false)} />
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
  const [faceView, setFaceView] = useState<"unknown" | "known">((sessionStorage.getItem("vg.idface") as "unknown" | "known") ?? "unknown");
  useEffect(() => sessionStorage.setItem("vg.idface", faceView), [faceView]);
  const [search, setSearch] = useState("");
  useEffect(() => sessionStorage.setItem("vg.idkind", kind), [kind]);
  const all = useQuery({ queryKey: ["identities", "all", "sugg"], queryFn: () => api.identities({ with_suggestion: 1 }) });
  const faces = useMemo(() => (all.data ?? []).filter((i) => i.kind === "face"), [all.data]);
  const list = useMemo(() => {
    let l = (all.data ?? []).filter((i) => i.kind === kind);
    if (kind === "face") l = l.filter((i) => !!i.name === (faceView === "known"));
    else if (named) l = l.filter((i) => !!i.name === (named === "true"));
    if (search) l = l.filter((i) => `${i.display_name} ${i.plate_text ?? ""} ${i.notes}`.toLowerCase().includes(search.toLowerCase()));
    return [...l].sort((a, b) => ((a.last_seen ?? "") < (b.last_seen ?? "") ? 1 : -1));
  }, [all.data, kind, named, faceView, search]);
  const countOf = (k: IdentityKind) => all.data?.filter((i) => i.kind === k).length ?? 0;

  // ---- Paket A: Mehrfachauswahl
  const [selecting, setSelecting] = useState(false);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const anchor = useRef<number | null>(null);
  const [bulk, setBulk] = useState<null | "merge" | "delete">(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const nightlyPairs = useNightlyPairs(selecting);
  const byId = useMemo(() => new Map((all.data ?? []).map((i) => [i.id, i])), [all.data]);
  // Auswahl gilt je Art; verschwundene Identitäten (gelöscht/zusammengeführt) fallen heraus
  useEffect(() => { setSel(new Set()); anchor.current = null; }, [kind]);
  useEffect(() => {
    setSel((prev) => {
      const next = new Set([...prev].filter((x) => byId.get(x)?.kind === kind));
      return next.size === prev.size ? prev : next;
    });
  }, [byId, kind]);
  const selected = useMemo(() => [...sel].map((x) => byId.get(x)).filter((x): x is Identity => !!x), [sel, byId]);
  const visibleIds = useMemo(() => new Set(list.map((i) => i.id)), [list]);
  const hiddenSel = selected.filter((i) => !visibleIds.has(i.id)).length;
  const allVisibleSelected = list.length > 0 && list.every((i) => sel.has(i.id));
  const stopSelecting = useCallback(() => { setSelecting(false); setSel(new Set()); anchor.current = null; }, []);

  const toggleSel = (id: number, shift: boolean) => {
    setSelecting(true);
    setSel((prev) => {
      const next = new Set(prev);
      const idx = list.findIndex((i) => i.id === id);
      const from = anchor.current != null ? list.findIndex((i) => i.id === anchor.current) : -1;
      if (shift && from >= 0 && idx >= 0) {
        // Bereich: alle zwischen Anker und Klick auf den Zustand des Ankers setzen (Standard: auswählen)
        const on = prev.has(anchor.current!);
        const [a, b] = from < idx ? [from, idx] : [idx, from];
        for (const i of list.slice(a, b + 1)) { if (on) next.add(i.id); else next.delete(i.id); }
      } else {
        if (next.has(id)) next.delete(id); else next.add(id);
        anchor.current = id;
      }
      return next;
    });
  };
  const selectAll = () => setSel((prev) => new Set([...prev, ...list.map((i) => i.id)]));
  const selectSimilar = () => {
    const add = similarIds(all.data ?? [], selected, nightlyPairs.data ?? []);
    const fresh = add.filter((x) => !sel.has(x));
    if (!fresh.length) { toast.info("Keine weiteren ähnlichen gefunden", "Vorschläge gibt es nur für unbenannte Gesichter und Tiere, die einer benannten Identität ähneln."); return; }
    setSel((prev) => new Set([...prev, ...fresh]));
    toast.success(`${fresh.length} ähnliche hinzugefügt`, fresh.some((x) => !visibleIds.has(x)) ? "Teilweise in der anderen Ansicht (Unbekannt/Bekannt)" : undefined);
  };
  const similarPossible = selected.some((i) => (i.kind === "face" || i.kind === "pet") && (!!i.name || !!i.suggestion));
  const mergeBlock = selected.length === 0 ? "Mindestens eine Identität auswählen"
    : new Set(selected.map((i) => i.kind)).size > 1 ? "Nur Identitäten gleicher Art" : null;
  useEffect(() => {
    if (!selecting) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !bulk && !historyOpen && !identityId) stopSelecting();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selecting, bulk, historyOpen, identityId, stopSelecting]);

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Identitäten</h1><div className="sub">Wiedererkannte Personen, Fahrzeuge und Tiere – benennen, zusammenführen, bereinigen.</div></div>
        <div className="row-wrap">
          <button className={`btn btn-sm ${selecting ? "btn-primary" : ""}`} onClick={() => (selecting ? stopSelecting() : setSelecting(true))}
            aria-pressed={selecting} title="Mehrere Identitäten auswählen, um sie zusammenzuführen oder zu löschen">
            <ListChecks size={15} />{selecting ? "Auswahl beenden" : "Auswählen"}
          </button>
          <button className="btn btn-sm btn-ghost" onClick={() => setHistoryOpen(true)} title="Verlauf der Zusammenführungen (zurücksetzen)">
            <History size={15} />Verlauf
          </button>
          <div className="input-group" style={{ width: 240 }}><Search size={15} />
            <input className="input input-sm" placeholder="Suchen …" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          {kind === "face" ? (
            <div className="seg">
              <button className={faceView === "unknown" ? "on" : ""} onClick={() => setFaceView("unknown")}>
                Unbekannt <span className="muted">({faces.filter((i) => !i.name).length})</span>
              </button>
              <button className={faceView === "known" ? "on" : ""} onClick={() => setFaceView("known")}>
                Bekannt <span className="muted">({faces.filter((i) => !!i.name).length})</span>
              </button>
            </div>
          ) : (
            <div className="seg">
              <button className={named === "" ? "on" : ""} onClick={() => setNamed("")}>Alle</button>
              <button className={named === "true" ? "on" : ""} onClick={() => setNamed("true")}>Benannt</button>
              <button className={named === "false" ? "on" : ""} onClick={() => setNamed("false")}>Unbenannt</button>
            </div>
          )}
        </div>
      </div>
      <div className="tabs">
        {KINDS.map(({ k, icon }) => (
          <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{icon}{KIND_PLURAL[k]}<span className="count">{countOf(k)}</span></button>
        ))}
      </div>
      {all.isLoading ? <Loading /> : list.length === 0 ? (
        <Empty icon={<Fingerprint size={44} />} title={kind === "face" ? (faceView === "known" ? "Keine bekannten Personen" : "Keine unbekannten Personen") : `Keine ${KIND_PLURAL[kind]}`} text="Identitäten werden automatisch angelegt, sobald die Erkennung Gesichter, Kennzeichen oder Tiere sieht." />
      ) : (
        <div className="id-grid">
          {list.map((i, n) => <IdCard key={i.id} i={i} delay={Math.min(n, 20) * 20} onClick={() => nav(`/identitaeten/${i.id}`)}
            selecting={selecting} selected={sel.has(i.id)} onSelect={(shift) => toggleSel(i.id, shift)} />)}
        </div>
      )}
      {selecting && (
        <SelectionBar count={sel.size} hidden={hiddenSel} visibleCount={list.length} allVisibleSelected={allVisibleSelected}
          onSelectAll={selectAll} onSimilar={selectSimilar} similarDisabled={!similarPossible}
          onMerge={() => setBulk("merge")} onDelete={() => setBulk("delete")} onClear={() => (sel.size ? setSel(new Set()) : stopSelecting())}
          mergeDisabledReason={mergeBlock} />
      )}
      {bulk === "merge" && selected.length > 0 && (
        <BulkMergeDialog selected={selected} all={all.data ?? []} onClose={() => setBulk(null)}
          onDone={() => { setBulk(null); setSel(new Set()); }} />
      )}
      {bulk === "delete" && selected.length > 0 && (
        <BulkDeleteDialog selected={selected} onClose={() => setBulk(null)} onDone={() => { setBulk(null); setSel(new Set()); }} />
      )}
      {historyOpen && <MergeHistoryDialog kind={kind} onClose={() => setHistoryOpen(false)} />}
      {identityId && <IdentityDetail key={identityId} id={Number(identityId)} onClose={() => nav("/identitaeten")} />}
    </div>
  );
}
