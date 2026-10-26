/**
 * Sprachmodul (CONTRACT „Nachträge: Sprachen“). Ohne Zusatzpaket.
 *
 * - Texte liegen je Sprache in `locales/<code>/<namensbereich>.ts`; Englisch (`en`) ist die Quelle und der Rückfall.
 * - Zugriff typsicher über Eigenschaften: `const m = useMsgs(); m.events.title`, mit Parametern als Funktion:
 *   `m.events.count(n)`. Außerhalb von Komponenten: `msgs()`.
 * - Neue Sprache: Ordner `locales/en` kopieren, übersetzen, in `locales/index.ts` eintragen. Fehlende Schlüssel
 *   kommen aus `en` (nur `de` muss vollständig sein, das prüft der Typecheck).
 * - Gewählte Sprache: `localStorage["vg.lang"]`, sonst Browsersprache, sonst Englisch. Ein Wechsel baut die App neu
 *   auf (Schlüssel am Wurzelelement), Formatierung über `intlLocale()`.
 */
import { useSyncExternalStore } from "react";
import { LOCALES, type LocaleDef } from "./locales";
import en from "./locales/en";

export type Messages = typeof en;
export type { LocaleDef };

const STORAGE_KEY = "vg.lang";
const listeners = new Set<() => void>();

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Englisch als Grundlage, die Sprache darüber (fehlende Schlüssel → Englisch). */
function merge<T>(base: T, over: unknown): T {
  if (!isObj(base) || !isObj(over)) return (over === undefined ? base : over) as T;
  const out: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(over)) out[k] = isObj(v) && isObj(out[k]) ? merge(out[k], v) : v;
  return out as T;
}

const cache = new Map<string, Messages>();
function messagesFor(code: string): Messages {
  let m = cache.get(code);
  if (!m) {
    const def = LOCALES.find((l) => l.code === code) ?? LOCALES[0];
    m = def.code === "en" ? en : merge(en, def.messages);
    cache.set(code, m);
  }
  return m;
}

function detect(): string {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && LOCALES.some((l) => l.code === saved)) return saved;
  } catch { /* privater Modus */ }
  for (const tag of navigator.languages ?? [navigator.language]) {
    const code = (tag || "").toLowerCase().split("-")[0];
    if (LOCALES.some((l) => l.code === code)) return code;
  }
  return "en";
}

let current = detect();
document.documentElement.lang = current;

export const languages = (): LocaleDef[] => LOCALES;
export const lang = (): string => current;
/** BCP-47-Tag für Intl (Datum, Zahlen), z. B. "en-GB" (24-h-Uhr) oder "de-DE". */
export const intlLocale = (): string => (LOCALES.find((l) => l.code === current) ?? LOCALES[0]).intl;
/** Texte der aktuellen Sprache (auch außerhalb von Komponenten). */
export const msgs = (): Messages => messagesFor(current);

export function setLang(code: string): void {
  if (!LOCALES.some((l) => l.code === code) || code === current) return;
  current = code;
  try { localStorage.setItem(STORAGE_KEY, code); } catch { /* ignorieren */ }
  document.documentElement.lang = code;
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** Aktueller Sprachcode, rendert bei Wechsel neu. */
export const useLang = (): string => useSyncExternalStore(subscribe, lang, lang);
/** Texte der aktuellen Sprache, rendert bei Wechsel neu. */
export function useMsgs(): Messages {
  useLang();
  return msgs();
}

/** Zahl in der aktuellen Sprache, z. B. 0.75 → „0,75“ bzw. "0.75". */
export const fmtNum = (v: number, digits?: number): string =>
  new Intl.NumberFormat(intlLocale(), digits === undefined ? undefined : { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);
