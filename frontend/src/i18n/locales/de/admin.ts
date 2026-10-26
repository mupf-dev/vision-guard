import type { AdminMessages } from "../en/admin";

/** Namensbereich „admin“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const admin = {
  title: "Administration",
  cameras: "Kameras",
  rules: "Regeln",
  sensors: "Sensoren",
  settings: "Einstellungen",
  system: "System",
  tokens: "API-Zugänge",
} satisfies AdminMessages;

export default admin;
