/** Namensbereich „login“ (Englisch = Quelle). */
const login = {
  subtitle: "AI camera surveillance · Sign in",
  username: "Username",
  password: "Password",
  signIn: "Sign in",
  wrongCredentials: "Wrong username or password.",
  failed: (msg: string) => `Sign-in failed: ${msg}`,
};

export type LoginMessages = typeof login;
export default login;
