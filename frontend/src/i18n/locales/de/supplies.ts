import type { SuppliesMessages } from "../en/supplies";

/** Namensbereich „supplies“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const supplies = {
  checkedAgo: (ago: string) => `geprüft ${ago}`,
  sourceAuto: "automatisch (Vorrat knapp/leer)",
  sourceMcp: "vom KI-Assistenten (MCP)",
  notAdded: "Nicht hinzugefügt",
  notSaved: "Nicht gespeichert",
  notDeleted: "Nicht gelöscht",
  title: "Vorräte",
  subtitle: "Vorräte im Blick und die Einkaufsliste dazu",
  shoppingList: "Einkaufsliste",
  openCount: (n: number) => `${n} offen`,
  showDone: "erledigte zeigen",
  addPlaceholder: "Artikel hinzufügen",
  add: "Hinzufügen",
  listEmpty: "Die Liste ist leer.",
  reopen: "wieder offen",
  markDone: "erledigt",
  remove: "Entfernen",
  autoOff: "Automatische Einträge sind ausgeschaltet (Einstellungen → Sensoren und Vorräte).",
  noSupplies: "Noch keine Vorräte",
  emptyBefore: "Legen Sie unter ",
  emptyLink: "Administration → Sensoren",
  emptyAfter: " einen Sensor mit „Vorrat“ an – z. B. Flaschen im Regal zählen, den Füllstand einer Obstschale mit Referenzbildern „voll/halb/leer“ vergleichen oder das Bildmodell kleine Mengen zählen lassen („How many eggs?“). Knappe Vorräte landen dann automatisch auf der Einkaufsliste.",
  estimateNote: "Die Messung ist eine Schätzung: verdeckte hintere Reihen, Spiegelungen und unbekannte Verpackungen können das Ergebnis verfälschen. Steht eine Person im Bereich, wird nicht gemessen.",
} satisfies SuppliesMessages;

export default supplies;
