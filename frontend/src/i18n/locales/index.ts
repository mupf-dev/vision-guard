import type en from "./en";
import enMessages from "./en";
import deMessages from "./de";

type DeepPartial<T> = T extends (...a: never[]) => unknown ? T : T extends object ? { [K in keyof T]?: DeepPartial<T[K]> } : T;

export interface LocaleDef {
  /** Sprachcode (ISO 639-1), z. B. "en" */
  code: string;
  /** Name in der eigenen Sprache, z. B. "Deutsch" */
  name: string;
  /** BCP-47-Tag für Datum/Zahlen */
  intl: string;
  /** Texte; fehlende Schlüssel kommen aus Englisch */
  messages: DeepPartial<typeof en>;
}

/** Verfügbare Sprachen – Englisch zuerst (Rückfall). Neue Sprache: Ordner anlegen und hier eintragen. */
export const LOCALES: LocaleDef[] = [
  { code: "en", name: "English", intl: "en-GB", messages: enMessages },
  { code: "de", name: "Deutsch", intl: "de-DE", messages: deMessages },
];
