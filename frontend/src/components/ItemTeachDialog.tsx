import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Camera as CamIcon, Check, ImagePlus, Package, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Identity, InventoryItem, Sample } from "../lib/types";
import { Dialog, Img, Loading } from "./ui";
import { toast } from "../lib/store";

/**
 * „Eigenen Gegenstand anlernen“ (W2, B4): Name + Label, Handyfotos (Rechteck je Foto aufziehen) und/oder aktuelle
 * Kamera-Ausschnitte („Von Kamera übernehmen“). Der Browser schneidet und verkleinert die Fotos (JPEG, ≤ 1600 px) –
 * HEIC wird dadurch auf Geräten, die es anzeigen können, ohne Server-Dekoder unterstützt.
 * Bei einem neuen Exemplar ohne ein einziges gespeichertes Bild wird es wieder entfernt (keine halbe Identität).
 */
type Rect = [number, number, number, number];
interface Photo { key: string; file: File; url: string; rect: Rect | null }

const MAX_PX = 1600;
const CROP_MARGIN = 0.25;   // Rand der Inventar-Crops im Worker (je Seite, relativ zur Box)

const clamp = (v: number) => Math.min(1, Math.max(0, v));

/** Foto im Browser ausschneiden (Rechteck + 5 % Rand) und auf ≤ 1600 px verkleinern → JPEG. */
async function prepareImage(file: File, rect: Rect | null): Promise<Blob> {
  let bmp: ImageBitmap;
  try {
    bmp = await createImageBitmap(file);
  } catch {
    throw new Error(`„${file.name}“: Bildformat wird von diesem Browser nicht unterstützt`);
  }
  const [x1, y1, x2, y2] = rect ?? [0, 0, 1, 1];
  const mx = rect ? (x2 - x1) * 0.05 : 0, my = rect ? (y2 - y1) * 0.05 : 0;
  const sx = clamp(x1 - mx) * bmp.width, sy = clamp(y1 - my) * bmp.height;
  const sw = clamp(x2 + mx) * bmp.width - sx, sh = clamp(y2 + my) * bmp.height - sy;
  const scale = Math.min(1, MAX_PX / Math.max(sw, sh));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(sw * scale));
  canvas.height = Math.max(1, Math.round(sh * scale));
  canvas.getContext("2d")!.drawImage(bmp, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  bmp.close();
  return new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error("Bild konnte nicht erzeugt werden"))), "image/jpeg", 0.9));
}

/** Bild mit aufziehbarem Rechteck (Maus/Touch); ohne Rechteck zählt das ganze Foto. */
function RectPicker({ src, rect, onChange }: { src: string; rect: Rect | null; onChange: (r: Rect | null) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const start = useRef<[number, number] | null>(null);
  const pos = (e: React.PointerEvent): [number, number] => {
    const r = ref.current!.getBoundingClientRect();
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  };
  return (
    <div ref={ref} style={{ position: "relative", lineHeight: 0, touchAction: "none", cursor: "crosshair", userSelect: "none", borderRadius: 8, overflow: "hidden", background: "var(--bg-4)" }}
      onPointerDown={(e) => { (e.target as Element).setPointerCapture?.(e.pointerId); start.current = pos(e); const p = start.current; onChange([p[0], p[1], p[0], p[1]]); }}
      onPointerMove={(e) => {
        if (!start.current) return;
        const [ax, ay] = start.current; const [bx, by] = pos(e);
        onChange([Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)]);
      }}
      onPointerUp={() => {
        start.current = null;
        if (rect && (rect[2] - rect[0] < 0.03 || rect[3] - rect[1] < 0.03)) onChange(null);
      }}>
      <img src={src} alt="" draggable={false} style={{ width: "100%", height: "auto", display: "block", pointerEvents: "none" }} />
      {rect && (
        <div style={{
          position: "absolute", left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${(rect[2] - rect[0]) * 100}%`,
          height: `${(rect[3] - rect[1]) * 100}%`, border: "2px solid var(--accent)", borderRadius: 4, boxShadow: "0 0 0 9999px rgba(0,0,0,.4)",
          pointerEvents: "none",
        }} />
      )}
    </div>
  );
}

/** Kamera-Ausschnitt zur Auswahl; misst die Größe des Gegenstands im Bild (Crop enthält 25 % Rand je Seite). */
function CameraPick({ it, on, minPx, onToggle }: { it: InventoryItem; on: boolean; minPx: number; onToggle: () => void }) {
  const [px, setPx] = useState<number | null>(null);
  const small = px !== null && px < minPx;
  return (
    <button type="button" className={`chip ${on ? "on" : ""}`} onClick={onToggle}
      style={{ flexDirection: "column", alignItems: "stretch", height: "auto", padding: 6, gap: 4, width: 150 }}>
      <div style={{ height: 100, borderRadius: 6, overflow: "hidden", background: "var(--bg-4)" }}>
        {it.crop_url && <img src={it.crop_url} alt="" style={{ width: "100%", height: "100%", objectFit: "contain" }}
          onLoad={(e) => {
            const im = e.currentTarget;
            setPx(Math.round(Math.min(im.naturalWidth, im.naturalHeight) / (1 + 2 * CROP_MARGIN)));
          }} />}
      </div>
      <span style={{ fontSize: 12, textAlign: "left" }}>{it.camera_name}{it.relation ? ` · ${it.relation.text}` : ""}</span>
      <span className="row" style={{ gap: 4, fontSize: 11 }}>
        {on && <Check size={12} />}
        {small ? <span style={{ color: "#fbbf24" }}>sehr klein ({px} px)</span> : px !== null ? <span className="muted">{px} px</span> : null}
      </span>
    </button>
  );
}

export function ItemTeachDialog({ identity, fromItem, onClose }: {
  /** vorhandenes Exemplar: nur Bilder hinzufügen/verwalten */
  identity?: Identity | null;
  /** „Als eigenen Gegenstand merken“ aus der Gegenstands-Ansicht */
  fromItem?: InventoryItem | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const vocab = settings.data?.inventory_vocabulary ?? [];
  const minPx = settings.data?.item_min_px ?? 90;
  const [label, setLabel] = useState(identity?.item_label ?? fromItem?.label ?? "");
  const [name, setName] = useState(identity?.name ?? "");
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [picked, setPicked] = useState<number[]>(fromItem ? [fromItem.id] : []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const cands = useQuery({
    queryKey: ["inventory", "teach", label],
    enabled: !!label,
    queryFn: () => api.inventory({ label, limit: 50 }),
  });
  const samples = useQuery({ queryKey: ["samples", identity?.id], enabled: !!identity, queryFn: () => api.samples(identity!.id) });
  useEffect(() => () => photos.forEach((p) => URL.revokeObjectURL(p.url)), []); // eslint-disable-line react-hooks/exhaustive-deps

  const camItems = useMemo(() => {
    const l = (cands.data?.items ?? []).filter((i) => i.crop_url);
    if (fromItem && !l.some((i) => i.id === fromItem.id)) l.unshift(fromItem);
    return l.sort((a, b) => Number(b.visible) - Number(a.visible));
  }, [cands.data, fromItem]);

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const next = [...files].filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name))
      .map((f) => ({ key: `${f.name}-${f.size}-${Math.random()}`, file: f, url: URL.createObjectURL(f), rect: null }));
    setPhotos((p) => [...p, ...next].slice(0, 20));
  };
  const total = photos.length + picked.length;
  const canSave = !busy && !!label && (identity ? total > 0 : name.trim().length > 0 && total > 0);

  const removeSample = async (s: Sample) => {
    if (!identity) return;
    try { await api.deleteSample(identity.id, s.id); qc.invalidateQueries({ queryKey: ["samples", identity.id] }); qc.invalidateQueries({ queryKey: ["identities"] }); }
    catch (e) { toast.error("Bild nicht entfernt", (e as Error).message); }
  };

  const save = async () => {
    setBusy(true); setErr(null);
    let ident = identity ?? null;
    let created = false;
    try {
      if (!ident) {
        ident = await api.createItemIdentity({ kind: "item", item_label: label, name: name.trim() });
        created = true;
      }
      let ok = 0; const errors: string[] = [];
      for (const p of photos) {
        try { await api.uploadSample(ident.id, await prepareImage(p.file, p.rect)); ok++; }
        catch (e) { errors.push((e as Error).message); }
      }
      for (const id of picked) {
        try { await api.sampleFromInventory(ident.id, id); ok++; }
        catch (e) { errors.push((e as Error).message); }
      }
      if (created && ok === 0) {
        await api.deleteIdentity(ident.id).catch(() => undefined);
        throw new Error(errors[0] ?? "Kein Bild gespeichert");
      }
      if (errors.length) toast.error(`${errors.length} Bild(er) nicht gespeichert`, errors[0]);
      toast.success(created ? "Gegenstand angelegt" : "Bilder gespeichert", `${ident.display_name}: ${ok} Bild(er)`);
      qc.invalidateQueries({ queryKey: ["identities"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog size="xl" onClose={onClose} icon={<Package size={18} />}
      title={identity ? `Bilder – ${identity.display_name}` : "Eigenen Gegenstand anlernen"}
      footer={<>
        <span className="muted" style={{ fontSize: 12 }}>{total} neue Bild(er)</span>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
        <button className="btn btn-primary" disabled={!canSave} onClick={save}>{busy && <span className="spinner" />}{identity ? "Bilder speichern" : "Anlegen"}</button>
      </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {!identity && (
          <div className="form-grid">
            <div className="field"><label>Name *</label>
              <input className="input" autoFocus value={name} maxLength={200} placeholder="z. B. Annas Rucksack" onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field"><label>Art des Gegenstands *</label>
              <select className="select" value={label} onChange={(e) => { setLabel(e.target.value); setPicked([]); }}>
                <option value="">– bitte wählen –</option>
                {vocab.map((v) => <option key={v.label} value={v.label}>{v.name_de}{v.group ? ` (${v.group})` : ""}</option>)}
              </select>
              <span className="hint">Erkannt wird zuerst die Art (z. B. Rucksack); danach unterscheidet VisionGuard Ihr Exemplar von anderen.</span>
            </div>
          </div>
        )}
        <div className="test-result" style={{ background: "var(--bg-1)", border: "1px solid var(--line)", alignItems: "flex-start" }}>
          <AlertTriangle size={16} color="#fbbf24" style={{ flex: "none", marginTop: 2 }} />
          <span style={{ fontSize: 13 }}>
            Funktioniert gut bei größeren Dingen (Rucksack, Tasche, Jacke, Lampe, Gerät), die im Kamerabild mindestens
            etwa {minPx} px groß sind. Schlüssel, Brillen und ähnlich kleine Dinge lassen sich meist nicht von gleichartigen
            unterscheiden. Mehrere Fotos aus verschiedenen Blickwinkeln helfen; unsichere Treffer fragt VisionGuard unter
            „Bitte bestätigen“ nach.
          </span>
        </div>

        {identity && (
          <div>
            <div className="section-title">Gespeicherte Bilder ({samples.data?.length ?? identity.sample_count})</div>
            {samples.isLoading ? <Loading /> : (
              <div className="chips">
                {(samples.data ?? []).map((s) => (
                  <div key={s.id} style={{ position: "relative", width: 84, height: 84, borderRadius: 6, overflow: "hidden", background: "var(--bg-4)" }}
                    title={s.negative ? "Gegenbeispiel (nicht dieses Exemplar)" : s.source === "upload" ? "Foto/Übernahme" : s.source === "review" ? "aus Rückfrage" : "automatisch gelernt"}>
                    <Img src={s.image_url} className="fill-img" />
                    {s.negative && <span className="badge bad" style={{ position: "absolute", left: 2, bottom: 2, fontSize: 10 }}>nein</span>}
                    <button type="button" className="btn btn-ghost btn-icon btn-sm" style={{ position: "absolute", right: 0, top: 0 }} onClick={() => removeSample(s)} aria-label="Bild entfernen"><X size={13} /></button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div>
          <div className="row" style={{ marginBottom: 8 }}>
            <div className="section-title" style={{ margin: 0, flex: 1 }}>Fotos (Handy)</div>
            <button type="button" className="btn btn-sm" onClick={() => fileRef.current?.click()}><ImagePlus size={14} />Fotos wählen</button>
            <input ref={fileRef} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
          </div>
          {photos.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>Noch keine Fotos. Tipp: Gegenstand formatfüllend fotografieren; im Foto ein Rechteck um den Gegenstand ziehen.</div> : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
              {photos.map((p) => (
                <div key={p.key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <RectPicker src={p.url} rect={p.rect} onChange={(r) => setPhotos((l) => l.map((x) => (x.key === p.key ? { ...x, rect: r } : x)))} />
                  <div className="row" style={{ fontSize: 12 }}>
                    <span className="muted" style={{ flex: 1 }}>{p.rect ? "Ausschnitt gewählt" : "ganzes Foto"}</span>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => { URL.revokeObjectURL(p.url); setPhotos((l) => l.filter((x) => x.key !== p.key)); }}><Trash2 size={13} /></button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="section-title"><CamIcon size={12} style={{ verticalAlign: -1, marginRight: 6 }} />Von Kamera übernehmen</div>
          {!label ? <div className="muted" style={{ fontSize: 13 }}>Zuerst die Art des Gegenstands wählen.</div>
            : cands.isLoading ? <Loading /> : camItems.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>Keine Kamera hat diese Art bisher erkannt.</div> : (
              <div className="chips">
                {camItems.map((it) => (
                  <CameraPick key={it.id} it={it} minPx={minPx} on={picked.includes(it.id)}
                    onToggle={() => setPicked((l) => (l.includes(it.id) ? l.filter((x) => x !== it.id) : [...l, it.id]))} />
                ))}
              </div>
            )}
        </div>
        {err && <div className="test-result bad"><X size={16} />{err}</div>}
      </div>
    </Dialog>
  );
}
