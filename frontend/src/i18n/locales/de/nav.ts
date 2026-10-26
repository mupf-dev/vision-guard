import type { NavMessages } from "../en/nav";

const nav = {
  mainNav: "Hauptnavigation",
  live: "Live",
  events: "Ereignisse",
  identities: "Identitäten",
  items: "Gegenstände",
  notifications: "Hinweise",
  presence: "Anwesenheit",
  pets: "Tiere",
  supplies: "Vorräte",
  reports: "Berichte",
  timeMachine: "Zeitmaschine",
  stats: "Statistik",
  admin: "Administration",
  more: "Mehr",
  morePages: "Weitere Seiten",
  menu: "Menü",
  pleaseConfirm: "Bitte bestätigen",
  openQuestions: (n: number) => (n === 1 ? "1 offene Rückfrage" : `${n} offene Rückfragen`),
  wsOpen: "Live verbunden",
  wsConnecting: "Verbinde …",
  wsClosed: "Getrennt",
  wsTitle: "WebSocket-Verbindung",
  alertsOff: "Alarm-Benachrichtigungen aus",
  alertsOn: "Alarm-Benachrichtigungen an",
  logout: "Abmelden",
  language: "Sprache",
} satisfies NavMessages;

export default nav;
