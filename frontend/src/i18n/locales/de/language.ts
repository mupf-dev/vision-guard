import type { LanguageMessages } from "../en/language";

const language = {
  title: "Sprache",
  uiLabel: "Sprache der Oberfläche (dieser Browser)",
  uiHint: "Wird nur in diesem Browser gespeichert. Auch über den Globus in der Kopfzeile wählbar.",
  systemLabel: "Systemsprache",
  systemHint: "Für Texte, die ohne Browser entstehen: Hinweise und Push-Nachrichten, Tagesbericht, Rückfragen „Bitte bestätigen“, MQTT-Texte und Antworten an KI-Agenten (MCP). Bestehende Texte behalten ihre Sprache.",
  addHint: "Weitere Sprachen: frontend/src/i18n/locales/en und backend/app/locales/en kopieren, übersetzen und die Sprache eintragen (siehe AGENTS.md).",
} satisfies LanguageMessages;

export default language;
