import { intlLocale, msgs } from "../i18n";

// Formatierer je Sprache (Intl, aktuelle Sprache aus dem i18n-Modul)
const fmtCache = new Map<string, Intl.DateTimeFormat>();
function dateFmt(kind: "dt" | "t" | "d"): Intl.DateTimeFormat {
  const loc = intlLocale();
  const key = `${loc}|${kind}`;
  let f = fmtCache.get(key);
  if (!f) {
    const opt: Intl.DateTimeFormatOptions = kind === "dt"
      ? { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : kind === "t" ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
      : { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" };
    f = new Intl.DateTimeFormat(loc, opt);
    fmtCache.set(key, f);
  }
  return f;
}

export function parseTs(v: string | number | null | undefined): Date | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return new Date(v * 1000);
  // ISO ohne Zone als UTC behandeln
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : `${v}Z`;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

export const fmtDateTime = (v?: string | null) => { const d = parseTs(v); return d ? dateFmt("dt").format(d) : "–"; };
export const fmtTime = (v?: string | null) => { const d = parseTs(v); return d ? dateFmt("t").format(d) : "–"; };
export const fmtShort = (v?: string | null) => { const d = parseTs(v); return d ? dateFmt("d").format(d) : "–"; };

/** „vor 5 min“ / "5 min ago" (Intl.RelativeTimeFormat der aktuellen Sprache) */
export function fmtAgo(v?: string | null): string {
  const d = parseTs(v);
  if (!d) return msgs().common.never;
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 10) return msgs().common.justNow;
  const rtf = new Intl.RelativeTimeFormat(intlLocale(), { numeric: "always", style: "short" });
  if (s < 60) return rtf.format(-s, "second");
  const m = Math.round(s / 60);
  if (m < 60) return rtf.format(-m, "minute");
  const h = Math.round(m / 60);
  if (h < 48) return rtf.format(-h, "hour");
  return rtf.format(-Math.round(h / 24), "day");
}

export function fmtDuration(a?: string | null, b?: string | null): string {
  const da = parseTs(a);
  const db = parseTs(b) ?? new Date();
  if (!da) return "–";
  const s = Math.max(0, Math.round((db.getTime() - da.getTime()) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return msgs().common.durMin(m, s % 60);
  return msgs().common.durHour(Math.floor(m / 60), m % 60);
}

export const pct = (v: number) => new Intl.NumberFormat(intlLocale(), { style: "percent", maximumFractionDigits: 0 }).format(v);

/** datetime-local (lokale Zeit) -> ISO UTC */
export function localToIso(v: string): string | undefined {
  if (!v) return undefined;
  const d = new Date(v);
  return isNaN(d.getTime()) ? undefined : d.toISOString();
}

export function randomId(prefix = "z"): string {
  return prefix + Math.random().toString(36).slice(2, 9);
}
