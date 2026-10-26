/** Einstellungskarte „Language“ (pages/admin/LanguageSettings.tsx). */
const language = {
  title: "Language",
  uiLabel: "Interface language (this browser)",
  uiHint: "Stored in this browser only. Also selectable via the globe in the header.",
  systemLabel: "System language",
  systemHint: "Used for texts created without a browser: notifications and push messages, daily report, \"please confirm\" questions, MQTT texts and answers to AI agents (MCP). Existing texts keep their language.",
  addHint: "More languages: copy frontend/src/i18n/locales/en and backend/app/locales/en, translate and register the language (see AGENTS.md).",
};

export type LanguageMessages = typeof language;
export default language;
