/* Identitäten 07 (Paket A): Mehrfachauswahl – Sammel-Zusammenführen, Sammel-Löschen, Verlauf mit „Zurücksetzen“. */
import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { CheckCheck, Combine, History, Lock, Sparkles, Trash2, Undo2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Identity, IdentityKind, IdentityMerge, MergeActor } from "../lib/types";
import { KIND_NAME, KIND_PLURAL } from "../lib/labels";
import { fmtDateTime } from "../lib/format";
import { ConfirmDialog, Dialog, Empty, Img, Loading, SearchSelect } from "../components/ui";
import { toast } from "../lib/store";
import { fmtNum, useMsgs } from "../i18n";

const nf = (n: number) => fmtNum(n);
/** „3 Bilder“ – Zahl in der aktuellen Sprache, Nomen aus `identities.n*` */
export const plural = (n: number, noun: (n: number) => string) => `${nf(n)} ${noun(n)}`;

/** Text mit `**fett**`-Abschnitten aus den Sprachdateien. */
export function Rich({ text }: { text: string }) {
  return <>{text.split("**").map((p, i) => <Fragment key={i}>{i % 2 ? <b>{p}</b> : p}</Fragment>)}</>;
}

/** Ziel-Vorschlag: benannt > meiste Ereignisse > meiste Bilder > älteste (kleinste ID). */
export function suggestTarget(list: Identity[]): Identity | undefined {
  return [...list].sort((a, b) =>
    Number(!!b.name) - Number(!!a.name) || b.event_count - a.event_count || b.sample_count - a.sample_count || a.id - b.id)[0];
}

/**
 * „Ähnliche auswählen“: Unbenannte gleicher Art, die einer gewählten Person ähneln.
 * Quelle: bester Vorschlag je Unbenannter (`with_suggestion=1`) und Merge-/Rückfrage-Aktionen des letzten Nachtlaufs.
 * Benannter Anker → alle Unbenannten mit Vorschlag auf ihn; unbenannter Anker mit Vorschlag → dessen Gruppe samt Ziel.
 */
export function similarIds(all: Identity[], anchors: Identity[], nightlyPairs: [number, number][] = []): number[] {
  const out = new Set<number>();
  const named = new Set<number>();
  for (const a of anchors) {
    if (a.name) named.add(a.id);
    else if (a.suggestion) { named.add(a.suggestion.identity_id); out.add(a.suggestion.identity_id); }
  }
  for (const i of all) {
    if (i.name || !anchors.some((a) => a.kind === i.kind)) continue;
    if (i.suggestion && named.has(i.suggestion.identity_id)) out.add(i.id);
  }
  const anchorIds = new Set([...anchors.map((a) => a.id), ...named]);
  const byId = new Map(all.map((i) => [i.id, i]));
  for (const [src, dst] of nightlyPairs) {
    const s = byId.get(src);
    if (s && !s.name && byId.has(dst) && anchorIds.has(dst)) out.add(src);
    if (s && anchorIds.has(src) && byId.get(dst) && !byId.get(dst)!.name) out.add(dst);
  }
  for (const a of anchors) out.delete(a.id);
  return [...out];
}

/** Paare (Quelle, Ziel) aus dem letzten Nachtlauf-Bericht – best effort, Fehler (z. B. 501) ignorieren. */
export function useNightlyPairs(enabled: boolean) {
  return useQuery({
    queryKey: ["nightly", "pairs"],
    enabled,
    retry: false,
    staleTime: 60_000,
    queryFn: async (): Promise<[number, number][]> => {
      try {
        const st = await api.nightlyStatus();
        if (!st.last || st.last.status !== "done") return [];
        const r = await api.nightlyReport(st.last.id);
        return r.actions.filter((a) => (a.type === "merge" || a.type === "review") && a.src_id && a.into_id)
          .map((a) => [a.src_id!, a.into_id!] as [number, number]);
      } catch { return []; }
    },
  });
}

/** Nach Sammelaktionen alles Identitäts-Bezogene neu laden. */
export function invalidateIdentities(qc: QueryClient) {
  for (const k of ["identities", "identity", "samples", "identity-events", "identity-suggestions", "events", "identity-merges"]) {
    qc.invalidateQueries({ queryKey: [k] });
  }
}

/* ---------- Aktionsleiste ---------- */

export function SelectionBar({ count, hidden, visibleCount, allVisibleSelected, onSelectAll, onSimilar, similarDisabled,
  onMerge, onDelete, onClear, mergeDisabledReason }: {
  count: number; hidden: number; visibleCount: number; allVisibleSelected: boolean; onSelectAll: () => void;
  onSimilar: () => void; similarDisabled: boolean; onMerge: () => void; onDelete: () => void; onClear: () => void;
  mergeDisabledReason: string | null;
}) {
  const m = useMsgs().identities;
  return (
    <div className="sel-bar" role="toolbar" aria-label={m.selection}>
      <span className="sel-count"><b>{nf(count)}</b> {m.selectedWord}{hidden > 0 && <span className="muted">{m.notInView(nf(hidden))}</span>}</span>
      <button className="btn btn-sm btn-ghost" onClick={onSelectAll} disabled={visibleCount === 0 || allVisibleSelected}
        title={m.selectAllTitle}>
        <CheckCheck size={14} />{m.selectAllN(nf(visibleCount))}
      </button>
      <button className="btn btn-sm btn-ghost" onClick={onSimilar} disabled={similarDisabled}
        title={m.similarTitle}>
        <Sparkles size={14} />{m.selectSimilar}
      </button>
      <span className="spacer" />
      <button className="btn btn-sm" onClick={onMerge} disabled={!!mergeDisabledReason} title={mergeDisabledReason ?? m.mergeSelectedTitle}>
        <Combine size={14} />{m.mergeEllipsis}
      </button>
      <button className="btn btn-sm btn-danger" onClick={onDelete} disabled={count === 0}><Trash2 size={14} />{m.deleteEllipsis}</button>
      <button className="btn btn-sm btn-ghost btn-icon" onClick={onClear} title={m.clearTitle} aria-label={m.clearSelection}><X size={14} /></button>
    </div>
  );
}

/* ---------- Zusammenführen ---------- */

export function BulkMergeDialog({ selected, all, onClose, onDone }: {
  selected: Identity[]; all: Identity[]; onClose: () => void; onDone: (target: Identity) => void;
}) {
  const mm = useMsgs();
  const m = mm.identities;
  const qc = useQueryClient();
  const kind = selected[0]?.kind ?? "face";
  const suggested = useMemo(() => suggestTarget(selected), [selected]);
  const [targetId, setTargetId] = useState<number | undefined>(suggested?.id);
  const [other, setOther] = useState<number[]>([]);
  const selIds = useMemo(() => new Set(selected.map((i) => i.id)), [selected]);
  const target = all.find((i) => i.id === targetId);
  const sources = selected.filter((i) => i.id !== targetId);
  const sumSamples = sources.reduce((n, i) => n + i.sample_count, 0);
  const sumEvents = sources.reduce((n, i) => n + i.event_count, 0);
  const namedSources = sources.filter((i) => !!i.name);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  // Bildgrenze wie im Backend (`face_match.sample_limit`): benannte Gesichter `face_max_samples_named`
  const limit = target?.kind === "face" && (target.name || namedSources.length)
    ? (settings.data?.face_max_samples_named ?? 20) : 20;
  const samplesAfter = (target?.sample_count ?? 0) + sumSamples;

  const otherOptions = useMemo(() => all
    .filter((i) => i.kind === kind && !selIds.has(i.id))
    .sort((a, b) => Number(!!b.name) - Number(!!a.name) || b.event_count - a.event_count)
    .map((i) => ({ value: i.id, label: i.display_name, image: i.cover_url, sub: `${i.name ? "" : `${m.unnamed} · `}${plural(i.sample_count, m.nImage)} · ${plural(i.event_count, m.nEvent)}` })),
  [all, kind, selIds, m]);

  const run = useMutation({
    mutationFn: () => api.bulkMergeIdentities(sources.map((i) => i.id), targetId!),
    onSuccess: (r) => {
      toast.success(m.bulkMerged(plural(r.merged.length, m.nIdentity)), m.bulkMergedText(r.target.display_name));
      invalidateIdentities(qc);
      onDone(r.target);
    },
    onError: (e: Error) => toast.error(m.mergeFailed, e.message),
  });

  const pickOther = (v: number[]) => { setOther(v); if (v[0]) setTargetId(v[0]); };
  const ordered = useMemo(() => [...selected].sort((a, b) => (a.id === suggested?.id ? -1 : b.id === suggested?.id ? 1 : 0)), [selected, suggested]);

  return (
    <Dialog size="lg" title={m.bulkMergeTitle(plural(selected.length, m.nIdentity))} icon={<Combine size={18} />} onClose={onClose}
      footer={<>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>{mm.common.cancel}</button>
        <button className="btn btn-primary" disabled={!target || sources.length === 0 || run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? <span className="spinner" /> : <Combine size={16} />}{m.merge}
        </button>
      </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="merge-preview" data-testid="merge-preview">
          {target ? <>
            <Img src={target.cover_url} className="avatar" />
            <span><b>{plural(sources.length, m.nIdentity)}, {plural(sumSamples, m.nImage)}, {plural(sumEvents, m.nEvent)}</b> → <b>{target.display_name}</b></span>
          </> : <span className="muted">{m.pickTarget}</span>}
        </div>
        <section>
          <div className="section-title">{m.target}</div>
          <div className="target-list">
            {ordered.map((i) => (
              <label key={i.id} className={`target-opt ${targetId === i.id ? "on" : ""}`}>
                <input type="radio" name="bulk-target" checked={targetId === i.id} onChange={() => { setTargetId(i.id); setOther([]); }} />
                <Img src={i.cover_url} className="avatar" />
                <span className="t-name">{i.display_name}</span>
                {!i.name && <span className="badge warn">{m.unnamed}</span>}
                {i.learning_locked && <span className="badge outline" title={m.learningLocked}><Lock size={11} /></span>}
                {i.id === suggested?.id && <span className="badge accent">{m.suggestion}</span>}
                <span className="spacer" />
                <span className="muted t-sub">{plural(i.sample_count, m.nImage)} · {plural(i.event_count, m.nEvent)}</span>
              </label>
            ))}
          </div>
          <div className="field" style={{ marginTop: 10 }}>
            <label>{m.otherTarget(KIND_NAME[kind])}</label>
            <SearchSelect inline options={otherOptions} value={other} onChange={pickOther} placeholder={m.searchIdentity} />
          </div>
        </section>
        {target && (
          <div className="dim" style={{ fontSize: 13, padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg-1)", display: "flex", flexDirection: "column", gap: 6 }}>
            <span><Rich text={m.bulkInfo(target.display_name, sources.length, nf(sources.length), plural(target.event_count + sumEvents, m.nEvent))
              + (samplesAfter > limit ? m.bulkInfoCapped(limit) : m.bulkInfoImages(plural(samplesAfter, m.nImage)))} /></span>
            {namedSources.length > 0 && <span>{target.name ? m.namedSourcesNotes(namedSources.map((i) => i.name).join(", "))
              : m.namedSourcesName(namedSources.map((i) => i.name).join(", "), namedSources[0].name ?? "")}</span>}
            {target.learning_locked && <span><Lock size={12} /> {m.bulkTargetLocked(target.display_name)}</span>}
            <span className="muted">{m.bulkHistoryHint}</span>
          </div>
        )}
      </div>
    </Dialog>
  );
}

/* ---------- Löschen ---------- */

export function BulkDeleteDialog({ selected, onClose, onDone }: { selected: Identity[]; onClose: () => void; onDone: () => void }) {
  const m = useMsgs().identities;
  const qc = useQueryClient();
  const samples = selected.reduce((n, i) => n + i.sample_count, 0);
  const events = selected.reduce((n, i) => n + i.event_count, 0);
  const named = selected.filter((i) => !!i.name);
  const run = useMutation({
    mutationFn: () => api.bulkDeleteIdentities(selected.map((i) => i.id)),
    onSuccess: (r) => {
      toast.success(m.bulkDeleted(plural(r.deleted.length, m.nIdentity)));
      invalidateIdentities(qc);
      onDone();
    },
    onError: (e: Error) => toast.error(m.deleteFailed, e.message),
  });
  const shown = selected.slice(0, 8).map((i) => i.display_name).join(", ");
  return (
    <ConfirmDialog title={m.bulkDeleteTitle(plural(selected.length, m.nIdentity))} busy={run.isPending}
      confirmLabel={m.bulkDeleteConfirm(plural(selected.length, m.nIdentity))}
      text={<div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span>{shown}{selected.length > 8 ? m.andMore(nf(selected.length - 8)) : ""}.</span>
        <span><Rich text={m.bulkDeleteInfo(samples, plural(samples, m.nImage), events, plural(events, m.nEvent))} /></span>
        {named.length > 0 && <span className="badge warn" style={{ alignSelf: "flex-start" }}>{m.amongNamed(named.length, nf(named.length), named.slice(0, 5).map((i) => i.name).join(", "))}</span>}
        <span className="muted">{m.cannotUndo}</span>
      </div>}
      onConfirm={() => run.mutate()} onClose={onClose} />
  );
}

/* ---------- Verlauf ---------- */


export function MergeHistoryDialog({ kind, onClose }: { kind?: IdentityKind; onClose: () => void }) {
  const mm = useMsgs();
  const t = mm.identities;
  const ACTOR: Record<MergeActor, string> = t.actor;
  const qc = useQueryClient();
  const [actor, setActor] = useState<MergeActor | "">("");
  const [limit, setLimit] = useState(50);
  const q = useQuery({
    queryKey: ["identity-merges", actor, limit],
    queryFn: () => api.identityMerges({ limit, ...(actor ? { actor } : {}) }),
  });
  const items = (q.data?.items ?? []).filter((m) => !kind || m.kind === kind);
  const undo = useMutation({
    mutationFn: (m: IdentityMerge) => api.undoIdentityMerge(m.id),
    onSuccess: (r) => {
      toast.success(t.undone, t.restored(r.restored.display_name));
      invalidateIdentities(qc);
    },
    onError: (e: Error) => toast.error(t.undoFailed, e.message),
  });
  return (
    <Dialog size="lg" title={t.historyDialogTitle(kind ? KIND_PLURAL[kind] : null)} icon={<History size={18} />} onClose={onClose}
      footer={<><span className="spacer" /><button className="btn btn-primary" onClick={onClose}>{mm.common.close}</button></>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="seg">
          {(["", "bulk", "user", "nightly", "review"] as const).map((a) => (
            <button key={a} className={actor === a ? "on" : ""} onClick={() => setActor(a)}>{a ? ACTOR[a] : mm.common.all}</button>
          ))}
        </div>
        {q.isLoading ? <Loading /> : items.length === 0 ? (
          <Empty icon={<History size={36} />} title={t.noMerges} text={t.noMergesText} />
        ) : (
          <div className="merge-hist">
            {items.map((m) => (
              <div key={m.id} className={`merge-row ${m.undone_at ? "undone" : ""}`}>
                <div className="m-main">
                  <span><b>{m.src_display_name}</b> → <b>{m.into_display_name ?? <span className="muted">{t.deletedTarget}</span>}</b></span>
                  <span className="muted m-sub">
                    {fmtDateTime(m.ts)} · {ACTOR[m.actor] ?? m.actor} · {plural(m.sample_count, t.nImage)}, {plural(m.event_count, t.nEvent)}
                    {m.score != null && ` · ${t.similarity(fmtNum(Math.round(m.score * 100) / 100))}`}
                    {m.undone_at && t.undoneAt(fmtDateTime(m.undone_at))}
                  </span>
                </div>
                {!m.undone_at && (
                  <button className="btn btn-sm" disabled={!m.undoable || undo.isPending}
                    title={m.undoable ? t.restore(m.src_display_name) : t.targetGone}
                    onClick={() => undo.mutate(m)}><Undo2 size={14} />{t.undo}</button>
                )}
              </div>
            ))}
            {(q.data?.total ?? 0) > limit && <button className="btn btn-sm btn-ghost" onClick={() => setLimit((l) => l + 50)}>{t.loadMore}</button>}
          </div>
        )}
        <span className="muted" style={{ fontSize: 12 }}>
          {t.undoHint}
        </span>
      </div>
    </Dialog>
  );
}
