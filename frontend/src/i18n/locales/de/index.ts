import type en from "../en";
import common from "./common";
import nav from "./nav";
import language from "./language";
import ui from "./ui";
import live from "./live";
import events from "./events";
import identities from "./identities";
import stats from "./stats";
import presence from "./presence";
import review from "./review";
import login from "./login";
import items from "./items";
import notifications from "./notifications";
import pets from "./pets";
import reports from "./reports";
import supplies from "./supplies";
import history from "./history";
import cameras from "./cameras";
import rules from "./rules";
import sensors from "./sensors";
import system from "./system";
import tokens from "./tokens";
import admin from "./admin";
import settings from "./settings";
import pose from "./pose";

/** Deutsch – vollständig (Typecheck gegen Englisch). */
const de = {
  common,
  nav,
  language,
  ui,
  live,
  events,
  identities,
  stats,
  presence,
  review,
  login,
  items,
  notifications,
  pets,
  reports,
  supplies,
  history,
  cameras,
  rules,
  sensors,
  system,
  tokens,
  admin,
  settings,
  pose,
} satisfies typeof en;

export default de;
