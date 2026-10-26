/** Kopfzeile und Hauptnavigation (components/Layout.tsx). */
const nav = {
  mainNav: "Main navigation",
  live: "Live",
  events: "Events",
  identities: "Identities",
  items: "Items",
  notifications: "Notifications",
  presence: "Presence",
  pets: "Pets",
  supplies: "Supplies",
  reports: "Reports",
  timeMachine: "Time machine",
  stats: "Statistics",
  admin: "Administration",
  more: "More",
  morePages: "More pages",
  menu: "Menu",
  pleaseConfirm: "Please confirm",
  openQuestions: (n: number) => (n === 1 ? "1 open question" : `${n} open questions`),
  wsOpen: "Live connected",
  wsConnecting: "Connecting …",
  wsClosed: "Disconnected",
  wsTitle: "WebSocket connection",
  alertsOff: "Turn alarm notifications off",
  alertsOn: "Turn alarm notifications on",
  logout: "Log out",
  language: "Language",
};

export type NavMessages = typeof nav;
export default nav;
