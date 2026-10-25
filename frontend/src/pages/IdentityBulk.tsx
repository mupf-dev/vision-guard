/* Identitäten 07 (Paket A): Mehrfachauswahl – Sammel-Zusammenführen, Sammel-Löschen, Verlauf mit „Zurücksetzen“. */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { CheckCheck, Combine, History, Lock, Sparkles, Trash2, Undo2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Identity, IdentityKind, IdentityMerge, MergeActor } from "../lib/types";
import { KIND_NAME, KIND_PLURAL } from "../lib/labels";
import { fmtDateTime } from "../lib/format";
import { ConfirmDialog, Dialog, Empty, Img, Loading, SearchSelect } from "../components/ui";
import { toast } from "../lib/store";

const nf = (n: number) => n.toLocaleString("de-DE");
const plural = (n: number, one: string, many: string) => `${nf(n)} ${n === 1 ? one : many}`;

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
  return (
    <div className="sel-bar" role="toolbar" aria-label="Auswahl">
      <span className="sel-count"><b>{nf(count)}</b> ausgewählt{hidden > 0 && <span className="muted"> ({nf(hidden)} nicht in dieser Ansicht)</span>}</span>
      <button className="btn btn-sm btn-ghost" onClick={onSelectAll} disabled={visibleCount === 0 || allVisibleSelected}
        title="Alle Karten der aktuellen Ansicht (inkl. Suche/Filter) auswählen">
        <CheckCheck size={14} />Alle ({nf(visibleCount)}) auswählen
      </button>
      <button className="btn btn-sm btn-ghost" onClick={onSimilar} disabled={similarDisabled}
        title="Unbekannte hinzufügen, die den ausgewählten Personen ähneln (Vorschläge und letzter Nachtlauf)">
        <Sparkles size={14} />Ähnliche auswählen
      </button>
      <span className="spacer" />
      <button className="btn btn-sm" onClick={onMerge} disabled={!!mergeDisabledReason} title={mergeDisabledReason ?? "Ausgewählte zu einer Identität zusammenführen"}>
        <Combine size={14} />Zusammenführen …
      </button>
      <button className="btn btn-sm btn-danger" onClick={onDelete} disabled={count === 0}><Trash2 size={14} />Löschen …</button>
      <button className="btn btn-sm btn-ghost btn-icon" onClick={onClear} title="Auswahl aufheben (Esc)" aria-label="Auswahl aufheben"><X size={14} /></button>
    </div>
  );
}

/* ---------- Zusammenführen ---------- */

export function BulkMergeDialog({ selected, all, onClose, onDone }: {
  selected: Identity[]; all: Identity[]; onClose: () => void; onDone: (target: Identity) => void;
}) {
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
  const limit = target?.kind === "face" && (target.name || namedSources.length) ? 40 : 20;
  const samplesAfter = (target?.sample_count ?? 0) + sumSamples;

  const otherOptions = useMemo(() => all
    .filter((i) => i.kind === kind && !selIds.has(i.id))
    .sort((a, b) => Number(!!b.name) - Number(!!a.name) || b.event_count - a.event_count)
    .map((i) => ({ value: i.id, label: i.display_name, image: i.cover_url, sub: `${i.name ? "" : "unbenannt · "}${i.sample_count} Bilder · ${i.event_count} Ereignisse` })),
  [all, kind, selIds]);

  const run = useMutation({
    mutationFn: () => api.bulkMergeIdentities(sources.map((i) => i.id), targetId!),
    onSuccess: (r) => {
      toast.success(`${plural(r.merged.length, "Identität", "Identitäten")} zusammengeführt`, `→ ${r.target.display_name} · im Verlauf rücksetzbar`);
      invalidateIdentities(qc);
      onDone(r.target);
    },
    onError: (e: Error) => toast.error("Zusammenführen fehlgeschlagen", e.message),
  });

  const pickOther = (v: number[]) => { setOther(v); if (v[0]) setTargetId(v[0]); };
  const ordered = useMemo(() => [...selected].sort((a, b) => (a.id === suggested?.id ? -1 : b.id === suggested?.id ? 1 : 0)), [selected, suggested]);

  return (
    <Dialog size="lg" title={`${plural(selected.length, "Identität", "Identitäten")} zusammenführen`} icon={<Combine size={18} />} onClose={onClose}
      footer={<>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
        <button className="btn btn-primary" disabled={!target || sources.length === 0 || run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? <span className="spinner" /> : <Combine size={16} />}Zusammenführen
        </button>
      </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="merge-preview" data-testid="merge-preview">
          {target ? <>
            <Img src={target.cover_url} className="avatar" />
            <span><b>{plural(sources.length, "Identität", "Identitäten")}, {plural(sumSamples, "Bild", "Bilder")}, {plural(sumEvents, "Ereignis", "Ereignisse")}</b> → <b>{target.display_name}</b></span>
          </> : <span className="muted">Bitte ein Ziel wählen.</span>}
        </div>
        <section>
          <div className="section-title">Ziel</div>
          <div className="target-list">
            {ordered.map((i) => (
              <label key={i.id} className={`target-opt ${targetId === i.id ? "on" : ""}`}>
                <input type="radio" name="bulk-target" checked={targetId === i.id} onChange={() => { setTargetId(i.id); setOther([]); }} />
                <Img src={i.cover_url} className="avatar" />
                <span className="t-name">{i.display_name}</span>
                {!i.name && <span className="badge warn">unbenannt</span>}
                {i.learning_locked && <span className="badge outline" title="Lernen gesperrt"><Lock size={11} /></span>}
                {i.id === suggested?.id && <span className="badge accent">Vorschlag</span>}
                <span className="spacer" />
                <span className="muted t-sub">{i.sample_count} Bilder · {i.event_count} Ereignisse</span>
              </label>
            ))}
          </div>
          <div className="field" style={{ marginTop: 10 }}>
            <label>… oder eine andere Identität ({KIND_NAME[kind]}) als Ziel</label>
            <SearchSelect inline options={otherOptions} value={other} onChange={pickOther} placeholder="Identität suchen …" />
          </div>
        </section>
        {target && (
          <div className="dim" style={{ fontSize: 13, padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg-1)", display: "flex", flexDirection: "column", gap: 6 }}>
            <span>Alle Bilder und Ereignisse wandern zu <b>{target.display_name}</b>, die übrigen {plural(sources.length, "Identität wird", "Identitäten werden")} gelöscht.
              {" "}{target.display_name} hat danach {plural(target.event_count + sumEvents, "Ereignis", "Ereignisse")}
              {samplesAfter > limit ? <> und max. {limit} Bilder (die Galerie wird gekürzt, manuelle Bilder bleiben).</> : <> und {plural(samplesAfter, "Bild", "Bilder")}.</>}</span>
            {namedSources.length > 0 && <span>Benannte Quellen ({namedSources.map((i) => i.name).join(", ")}) {target.name ? <>gehen als „früher: …“ in die Notizen.</> : <>– der Name „{namedSources[0].name}“ wird übernommen.</>}</span>}
            {target.learning_locked && <span><Lock size={12} /> {target.display_name} ist für das Lernen gesperrt – das Zusammenführen ist trotzdem möglich.</span>}
            <span className="muted">Jede Zusammenführung steht im Verlauf und lässt sich dort zurücksetzen.</span>
          </div>
        )}
      </div>
    </Dialog>
  );
}

/* ---------- Löschen ---------- */

export function BulkDeleteDialog({ selected, onClose, onDone }: { selected: Identity[]; onClose: () => void; onDone: () => void }) {
  const qc = useQueryClient();
  const samples = selected.reduce((n, i) => n + i.sample_count, 0);
  const events = selected.reduce((n, i) => n + i.event_count, 0);
  const named = selected.filter((i) => !!i.name);
  const run = useMutation({
    mutationFn: () => api.bulkDeleteIdentities(selected.map((i) => i.id)),
    onSuccess: (r) => {
      toast.success(`${plural(r.deleted.length, "Identität", "Identitäten")} gelöscht`);
      invalidateIdentities(qc);
      onDone();
    },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });
  const shown = selected.slice(0, 8).map((i) => i.display_name).join(", ");
  return (
    <ConfirmDialog title={`${plural(selected.length, "Identität", "Identitäten")} löschen?`} busy={run.isPending}
      confirmLabel={`${plural(selected.length, "Identität", "Identitäten")} löschen`}
      text={<div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span>{shown}{selected.length > 8 ? ` und ${nf(selected.length - 8)} weitere` : ""}.</span>
        <span>{plural(samples, "Bild wird", "Bilder werden")} endgültig gelöscht. <b>Die {plural(events, "Ereignis bleibt", "Ereignisse bleiben")} erhalten</b>, aber ohne Person.</span>
        {named.length > 0 && <span className="badge warn" style={{ alignSelf: "flex-start" }}>Darunter {plural(named.length, "benannte Identität", "benannte Identitäten")}: {named.slice(0, 5).map((i) => i.name).join(", ")}</span>}
        <span className="muted">Löschen lässt sich nicht rückgängig machen.</span>
      </div>}
      onConfirm={() => run.mutate()} onClose={onClose} />
  );
}

/* ---------- Verlauf ---------- */

const ACTOR: Record<MergeActor, string> = { user: "manuell", bulk: "Mehrfachauswahl", nightly: "Nachtlauf", review: "Rückfrage" };

export function MergeHistoryDialog({ kind, onClose }: { kind?: IdentityKind; onClose: () => void }) {
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
      toast.success("Zurückgesetzt", `${r.restored.display_name} ist wieder eigenständig`);
      invalidateIdentities(qc);
    },
    onError: (e: Error) => toast.error("Zurücksetzen fehlgeschlagen", e.message),
  });
  return (
    <Dialog size="lg" title={`Verlauf: Zusammenführungen${kind ? ` (${KIND_PLURAL[kind]})` : ""}`} icon={<History size={18} />} onClose={onClose}
      footer={<><span className="spacer" /><button className="btn btn-primary" onClick={onClose}>Schließen</button></>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="seg">
          {(["", "bulk", "user", "nightly", "review"] as const).map((a) => (
            <button key={a} className={actor === a ? "on" : ""} onClick={() => setActor(a)}>{a ? ACTOR[a] : "Alle"}</button>
          ))}
        </div>
        {q.isLoading ? <Loading /> : items.length === 0 ? (
          <Empty icon={<History size={36} />} title="Noch keine Zusammenführungen" text="Vollständige Zusammenführungen erscheinen hier und lassen sich zurücksetzen." />
        ) : (
          <div className="merge-hist">
            {items.map((m) => (
              <div key={m.id} className={`merge-row ${m.undone_at ? "undone" : ""}`}>
                <div className="m-main">
                  <span><b>{m.src_display_name}</b> → <b>{m.into_display_name ?? <span className="muted">(gelöscht)</span>}</b></span>
                  <span className="muted m-sub">
                    {fmtDateTime(m.ts)} · {ACTOR[m.actor] ?? m.actor} · {plural(m.sample_count, "Bild", "Bilder")}, {plural(m.event_count, "Ereignis", "Ereignisse")}
                    {m.score != null && ` · Ähnlichkeit ${m.score.toLocaleString("de-DE", { maximumFractionDigits: 2 })}`}
                    {m.undone_at && ` · zurückgesetzt ${fmtDateTime(m.undone_at)}`}
                  </span>
                </div>
                {!m.undone_at && (
                  <button className="btn btn-sm" disabled={!m.undoable || undo.isPending}
                    title={m.undoable ? `${m.src_display_name} wiederherstellen` : "Ziel existiert nicht mehr – zuerst die spätere Zusammenführung zurücksetzen"}
                    onClick={() => undo.mutate(m)}><Undo2 size={14} />Zurücksetzen</button>
                )}
              </div>
            ))}
            {(q.data?.total ?? 0) > limit && <button className="btn btn-sm btn-ghost" onClick={() => setLimit((l) => l + 50)}>Mehr laden</button>}
          </div>
        )}
        <span className="muted" style={{ fontSize: 12 }}>
          Zurücksetzen stellt die Quelle mit ihren Bildern und Ereignissen wieder her. Bilder, die beim Kürzen der Galerie entfallen sind, kommen nicht zurück.
        </span>
      </div>
    </Dialog>
  );
}
