import type { LoginMessages } from "../en/login";

/** Namensbereich „login“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const login = {
  subtitle: "KI-Kameraüberwachung · Anmeldung",
  username: "Benutzername",
  password: "Passwort",
  signIn: "Anmelden",
  wrongCredentials: "Benutzername oder Passwort falsch.",
  failed: (msg: string) => `Anmeldung fehlgeschlagen: ${msg}`,
} satisfies LoginMessages;

export default login;
