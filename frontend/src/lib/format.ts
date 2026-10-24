const dtf = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const tf = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const df = new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

export function parseTs(v: string | number | null | undefined): Date | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return new Date(v * 1000);
  // ISO ohne Zone als UTC behandeln
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : `${v}Z`;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

export const fmtDateTime = (v?: string | null) => { const d = parseTs(v); return d ? dtf.format(d) : "–"; };
export const fmtTime = (v?: string | null) => { const d = parseTs(v); return d ? tf.format(d) : "–"; };
export const fmtShort = (v?: string | null) => { const d = parseTs(v); return d ? df.format(d) : "–"; };

export function fmtAgo(v?: string | null): string {
  const d = parseTs(v);
  if (!d) return "nie";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 10) return "gerade eben";
  if (s < 60) return `vor ${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `vor ${m} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `vor ${h} h`;
  return `vor ${Math.round(h / 24)} Tagen`;
}

export function fmtDuration(a?: string | null, b?: string | null): string {
  const da = parseTs(a);
  const db = parseTs(b) ?? new Date();
  if (!da) return "–";
  const s = Math.max(0, Math.round((db.getTime() - da.getTime()) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export const pct = (v: number) => `${Math.round(v * 100)} %`;

/** datetime-local (lokale Zeit) -> ISO UTC */
export function localToIso(v: string): string | undefined {
  if (!v) return undefined;
  const d = new Date(v);
  return isNaN(d.getTime()) ? undefined : d.toISOString();
}

export function randomId(prefix = "z"): string {
  return prefix + Math.random().toString(36).slice(2, 9);
}
