import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search, X, Bike, Bird, Bus, Car, Cat, Dog, PawPrint, Truck, User, CircleHelp, Box } from "lucide-react";
import { labelColor, labelName } from "../lib/labels";

/* ---------- Dialog ---------- */
export function Dialog({
  title, icon, onClose, children, footer, size,
}: { title: ReactNode; icon?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; size?: "lg" | "xl" }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const downOnBackdrop = useRef(false);
  return createPortal(
    <div
      className="backdrop"
      onMouseDown={(e) => (downOnBackdrop.current = e.target === e.currentTarget)}
      onMouseUp={(e) => { if (downOnBackdrop.current && e.target === e.currentTarget) onClose(); }}
    >
      <div className={`dialog ${size ?? ""}`} role="dialog" aria-modal="true">
        <div className="dialog-head">
          <h2>{icon}{title}</h2>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={onClose} aria-label="Schließen"><X size={18} /></button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

/* ---------- Confirm ---------- */
export function ConfirmDialog({
  title, text, confirmLabel = "Löschen", danger = true, busy, onConfirm, onClose,
}: { title: string; text: ReactNode; confirmLabel?: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onClose: () => void }) {
  return (
    <Dialog title={title} onClose={onClose} footer={<>
      <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
      <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={onConfirm} disabled={busy}>
        {busy && <span className="spinner" />}{confirmLabel}
      </button>
    </>}>
      <div className="dim">{text}</div>
    </Dialog>
  );
}

/* ---------- Switch ---------- */
export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <label className={`switch ${disabled ? "disabled" : ""}`} onClick={(e) => e.stopPropagation()}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
      {label && <span>{label}</span>}
    </label>
  );
}

/* ---------- Range ---------- */
export function Range({ value, onChange, min = 0, max = 1, step = 0.01, format }: { value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; format?: (v: number) => string }) {
  return (
    <div className="range">
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <output>{format ? format(value) : value}</output>
    </div>
  );
}

/* ---------- Chips multi select ---------- */
export function ChipSelect<T extends string | number>({
  options, value, onChange, color, render,
}: { options: T[]; value: T[]; onChange: (v: T[]) => void; color?: (o: T) => string | undefined; render?: (o: T) => ReactNode }) {
  return (
    <div className="chips">
      {options.map((o) => {
        const on = value.includes(o);
        return (
          <button type="button" key={String(o)} className={`chip ${on ? "on" : ""}`} style={{ ["--chip-c" as string]: color?.(o) }}
            onClick={() => onChange(on ? value.filter((v) => v !== o) : [...value, o])}>
            {color && <span className="dot" />}
            {render ? render(o) : String(o)}
            {on && <Check size={13} />}
          </button>
        );
      })}
    </div>
  );
}

/* ---------- Searchable select (single / multi) ---------- */
export interface SSOption { value: number; label: string; image?: string | null; sub?: string }

export function SearchSelect({
  options, value, onChange, placeholder = "Suchen …", multi, inline,
}: { options: SSOption[]; value: number[]; onChange: (v: number[]) => void; placeholder?: string; multi?: boolean; inline?: boolean }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hl, setHl] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  const filtered = useMemo(
    () => options.filter((o) => `${o.label} ${o.sub ?? ""}`.toLowerCase().includes(q.toLowerCase())),
    [options, q],
  );
  const pick = (o: SSOption) => {
    if (multi) onChange(value.includes(o.value) ? value.filter((v) => v !== o.value) : [...value, o.value]);
    else { onChange([o.value]); setOpen(false); }
    setQ("");
  };
  const selected = options.filter((o) => value.includes(o.value));
  return (
    <div className="ss" ref={ref}>
      {multi ? (
        <div className="ms-tags" onClick={() => setOpen(true)}>
          {selected.map((s) => (
            <span className="ms-tag" key={s.value}>
              {s.label}
              <button type="button" onClick={(e) => { e.stopPropagation(); pick(s); }}><X size={12} /></button>
            </span>
          ))}
          <input value={q} placeholder={selected.length ? "" : placeholder} onFocus={() => setOpen(true)}
            onChange={(e) => { setQ(e.target.value); setOpen(true); setHl(0); }}
            onKeyDown={(e) => onKeys(e)} />
        </div>
      ) : (
        <div className="input-group">
          <Search size={15} />
          <input className="input" value={open ? q : selected[0]?.label ?? q} placeholder={placeholder}
            onFocus={() => { setOpen(true); setQ(""); }} onChange={(e) => { setQ(e.target.value); setOpen(true); setHl(0); }}
            onKeyDown={(e) => onKeys(e)} />
          <ChevronDown size={15} style={{ position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)", color: "var(--text-3)" }} />
        </div>
      )}
      {open && (
        <div className={`ss-list ${inline ? "inline" : ""}`}>
          {filtered.length === 0 && <div className="ss-opt muted">Keine Treffer</div>}
          {filtered.map((o, i) => (
            <div key={o.value} className={`ss-opt ${i === hl ? "hl" : ""} ${value.includes(o.value) ? "sel" : ""}`}
              onMouseEnter={() => setHl(i)} onMouseDown={(e) => { e.preventDefault(); pick(o); }}>
              {o.image !== undefined && (o.image ? <img src={o.image} alt="" /> : <span className="avatar" style={{ width: 28, height: 28 }} />)}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div>{o.label}</div>
                {o.sub && <div className="muted" style={{ fontSize: 12 }}>{o.sub}</div>}
              </div>
              {value.includes(o.value) && <Check size={15} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
  function onKeys(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setHl((h) => Math.min(h + 1, filtered.length - 1)); setOpen(true); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHl((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); if (filtered[hl]) pick(filtered[hl]); }
    else if (e.key === "Escape") setOpen(false);
    else if (e.key === "Backspace" && multi && !q && value.length) onChange(value.slice(0, -1));
  }
}

/* ---------- Label pill + icon ---------- */
export function LabelIcon({ label, size = 14 }: { label: string; size?: number }) {
  const p = { size };
  switch (label) {
    case "person": return <User {...p} />;
    case "car": return <Car {...p} />;
    case "truck": return <Truck {...p} />;
    case "bus": return <Bus {...p} />;
    case "bicycle": case "motorcycle": return <Bike {...p} />;
    case "dog": return <Dog {...p} />;
    case "cat": return <Cat {...p} />;
    case "bird": return <Bird {...p} />;
    case "horse": case "sheep": case "cow": return <PawPrint {...p} />;
    case "": return <CircleHelp {...p} />;
    default: return <Box {...p} />;
  }
}

export function LabelPill({ label, score }: { label: string; score?: number }) {
  return (
    <span className="label-pill" style={{ ["--lc" as string]: labelColor(label) }}>
      <LabelIcon label={label} />
      {labelName(label)}
      {score !== undefined && <span style={{ opacity: 0.7, fontWeight: 500 }}>{Math.round(score * 100)}%</span>}
    </span>
  );
}

export function Empty({ icon, title, text, children }: { icon?: ReactNode; title: string; text?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <h3>{title}</h3>
      {text && <div>{text}</div>}
      {children}
    </div>
  );
}

export function Loading({ text = "Lade …" }: { text?: string }) {
  return <div className="center-load"><div className="row"><span className="spinner" />{text}</div></div>;
}

export function Img({ src, alt = "", className, fallback }: { src?: string | null; alt?: string; className?: string; fallback?: ReactNode }) {
  const [err, setErr] = useState(false);
  useEffect(() => setErr(false), [src]);
  if (!src || err) return <>{fallback ?? <div className={className} style={{ background: "var(--bg-4)" }} />}</>;
  return <img src={src} alt={alt} className={className} loading="lazy" onError={() => setErr(true)} />;
}
