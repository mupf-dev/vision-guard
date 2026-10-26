"""B3 Tagesbericht – Text erzeugen.

Zwei Wege, Ergebnis jeweils `text` + einfaches `html`:
- `template` (Standard, ohne KI): feste Satzbausteine je Abschnitt (`locales/<code>/report.json`, Systemsprache).
- `llm`: kompakte Fakten (ohne Bilder, URLs, IDs) an den gemeinsamen LLM-Client (`llm_client.complete`).
  Das Modell schreibt in der Systemsprache.
  **Plausibilitätsprüfung:** Uhrzeiten (auch „halb elf“, „viertel nach 7“, „10 Uhr“, "half past ten", "7 pm"),
  Zahlen (auch Zahlwörter wie „zwei“, „dreimal“, "two", "twice"), Datumsangaben (25.10. bzw. 25/10), Kennzeichen,
  „Person #N“ und bekannte Namen im Text müssen in den Fakten
  stehen; sonst Rückfall auf Satzbausteine (`generator = "template (Fallback)"`).
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .. import i18n, llm_client, settings_store
from ..config import config
from ..inventory import local_time_text
from .facts import PURPOSE_DE, label_name, purpose_name, source_name, weekday_name

log = logging.getLogger("vg.report")

MAX_WORDS = 160            # Anweisung an das Modell: 120 Wörter; Prüfung mit etwas Spielraum
FALLBACK = "template (Fallback)"


# ---------------------------------------------------------------- Zeit-Helfer


def _dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _t(v: Optional[str], end: datetime) -> str:
    """Ortszeit relativ zum Berichtsende: „14:02“, „gestern 14:02“, „22.10. 14:02“."""
    d = _dt(v)
    return local_time_text(d, end) if d else "?"


def _span(first: Optional[str], last: Optional[str], end: datetime) -> str:
    a, b = _t(first, end), _t(last, end)
    return a if a == b else f"{a}–{b}"


def _times(n: int) -> str:
    return f"{n}×"


def _join(parts: list[str], more: int = 0) -> str:
    parts = [p for p in parts if p]
    if more > 0:
        parts.append(i18n.t("report.more", n=more))
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + f" {i18n.t('report.and')} " + parts[-1]


def ref_end(facts: dict[str, Any]) -> datetime:
    """Bezugszeitpunkt für Texte: Berichtsende; endet der Zeitraum genau um Mitternacht (Kalendertag),
    eine Sekunde davor – sonst hieße der Bericht über Montag „Dienstag“ und alle Zeiten „gestern …“."""
    p = facts.get("period") or {}
    end = _dt(p.get("end")) or datetime.now(timezone.utc)
    start = _dt(p.get("start"))
    loc = end.astimezone(config.tz)
    if loc.hour == 0 and loc.minute == 0 and (start is None or (end - start).total_seconds() >= 3600):
        return end - timedelta(seconds=1)
    return end


def period_text(facts: dict[str, Any]) -> tuple[str, str]:
    """(Überschrift, Zeitraum) – z. B. ("Samstag, 25.10.", "gestern 21:00 – 21:00") bzw. ("Freitag, 24.10.",
    "00:00 – 24:00") für einen Kalendertag."""
    p = facts.get("period") or {}
    ref = ref_end(facts)
    loc = ref.astimezone(config.tz)
    head = f"{weekday_name(loc.weekday())}, {loc.strftime(i18n.t('report.date_head'))}"
    end_txt = "24:00" if ref != (_dt(p.get("end")) or ref) else _t(p.get("end"), ref)
    return head, f"{_t(p.get('start'), ref)} – {end_txt}"


# ---------------------------------------------------------------- Satzbausteine


def sections(facts: dict[str, Any]) -> list[tuple[str, str, str]]:
    """[(key, Titel, Satz)] – Grundlage für Text, HTML und die aufklappbaren Abschnitte der Oberfläche.
    Texte in der aktuellen Sprache (ohne Anfrage: Systemsprache)."""
    T = i18n.t
    end = ref_end(facts)
    out: list[tuple[str, str, str]] = []

    # Personen
    pe = facts.get("people") or {}
    n_person = int(pe.get("events") or 0)
    if n_person:
        named = []
        for p in pe.get("named") or []:
            c = int(p.get("count") or 0)
            named.append(f"{p['display_name']} {_t(p.get('first'), end)}" if c == 1
                         else f"{p['display_name']} {_times(c)} ({_span(p.get('first'), p.get('last'), end)})")
        parts = []
        if named:
            parts.append(T("report.people_known", list=_join(named)))
        uf, nf = int(pe.get("unknown_faces") or 0), int(pe.get("new_faces") or 0)
        if uf:
            parts.append(T("report.people_unknown", n=uf) + (T("report.people_new", n=nf) if nf else ""))
        wf = int(pe.get("without_face") or 0)
        if wf:
            parts.append(T("report.people_no_face", times=_times(wf)))
        out.append(("people", T("report.sec.people"), T("report.people_detected", times=_times(n_person))
                    + (" – " + "; ".join(parts) if parts else "") + "."))
    else:
        out.append(("people", T("report.sec.people"), T("report.people_none")))

    # Fahrzeuge
    ve = facts.get("vehicles") or {}
    if int(ve.get("total") or 0):
        by = [T("report.vehicle_item", n=int(e["count"]), label=label_name(e["label"], int(e["count"])),
                time=_t(e.get("last"), end)) for e in ve.get("by_label") or []]
        s = _join(by)
        plates = []
        for p in ve.get("plates") or []:
            c = int(p.get("count") or 0)
            nm = p["display_name"] if not p.get("name") else f"{p['name']} ({p.get('plate_text') or ''})".replace(" ()", "")
            plates.append(f"{nm} {_t(p.get('first'), end)}" if c == 1 else f"{nm} {_times(c)}")
        if plates:
            more = int(ve.get("plates_total") or 0) - len(plates)
            s += "; " + T("report.plates", list=_join(plates, more))
        out.append(("vehicles", T("report.sec.vehicles"), s + "."))

    # Tiere
    an = facts.get("animals") or {}
    if int(an.get("total") or 0) or an.get("visits"):
        parts = []
        for p in an.get("named") or []:
            c = int(p.get("count") or 0)
            where = f", {p['cameras'][0]}" if p.get("cameras") else ""
            parts.append(f"{p['display_name']} {_t(p.get('first'), end)}{where}" if c == 1
                         else f"{p['display_name']} {_times(c)} ({T('report.last', time=_t(p.get('last'), end) + where)})")
        s = _join(parts)
        named_n = sum(int(p.get("count") or 0) for p in an.get("named") or [])
        if int(an.get("total") or 0) > named_n:   # nur wenn es mehr gab als die benannten Tiere
            rest = [f"{int(e['count'])}× {label_name(e['label'])}" for e in an.get("by_label") or []]
            s = (s + "; " + T("report.animals_total") + " " if s else "") + _join(rest)
        visits = []
        for v in an.get("visits") or []:
            who = v.get("pet") or label_name(v.get("label") or "")
            place = purpose_name(v.get("purpose")) if v.get("purpose") in PURPOSE_DE \
                else (v.get("purpose_de") or v.get("purpose") or "")
            visits.append(T("report.visit", who=who, place=place, times=_times(int(v.get("count") or 0)),
                            time=_t(v.get("last"), end)))
        if visits:
            s += ("; " if s else "") + T("report.bowls", list=_join(visits))
        out.append(("animals", T("report.sec.animals"), s + "."))

    # Gegenstände (B2)
    inv = facts.get("inventory")
    if inv and sum(int(v or 0) for k, v in (inv.get("counts") or {}).items() if k != "transient"):
        out.append(("inventory", T("report.sec.inventory"), inv.get("summary") or ""))
    elif facts.get("inventory_error") and facts.get("inventory_error_code") != "out_of_retention" \
            and facts["inventory_error"] != "Zeitraum außerhalb der Aufbewahrung":   # alte Berichte (deutsch)
        out.append(("inventory", T("report.sec.inventory"), T("report.inv_unavailable", error=facts["inventory_error"])))

    # Regeln
    rules = facts.get("rules") or []
    if rules:
        parts = []
        for r in rules:
            c = int(r.get("count") or 0)
            parts.append(T("report.rule_once", name=r["name"], time=_t(r.get("last"), end)) if c == 1
                         else T("report.rule_many", name=r["name"], times=_times(c), time=_t(r.get("last"), end)))
        out.append(("rules", T("report.sec.rules"), _join(parts) + "."))

    # Hinweise und System
    no = (facts.get("notifications") or {}).get("by_source") or {}
    sys_ = facts.get("system") or {}
    parts = []
    for src, n in sorted(no.items(), key=lambda kv: -kv[1]):
        parts.append(T("report.notif_item", times=_times(int(n)), source=source_name(src)))
    s = (_join(parts) + ".") if parts else ""
    off = [c.get("name") for c in sys_.get("offline_cameras") or []]
    if off:
        s += (" " if s else "") + T("report.offline", n=len(off), names=off[0] if len(off) == 1 else _join(off))
    if s:
        out.append(("system", T("report.sec.system"), s))

    if n_person == 0 and len(out) == 1:
        out = [("quiet", T("report.sec.quiet"), T("report.quiet_text"))]
    return out


def report_title(head: str, kind: str = "daily") -> str:
    """„Tagesbericht Samstag, 25.10.“ bzw. "Daily report Saturday, 25/10" (manuell: „Bericht …“)."""
    return i18n.t("report.title_daily" if kind == "daily" else "report.title_manual", head=head)


def render_template(facts: dict[str, Any], kind: str = "daily") -> tuple[str, str]:
    head, span = period_text(facts)
    title = report_title(head, kind)
    secs = sections(facts)
    lines = [f"{title} ({span})"] + [f"{t}: {s}" for _, t, s in secs]
    esc = _html.escape
    html = (f"<div class=\"vg-report\"><p><strong>{esc(title)}</strong> <span>({esc(span)})</span></p>"
            + "".join(f"<p data-section=\"{esc(k)}\"><strong>{esc(t)}:</strong> {esc(s)}</p>" for k, t, s in secs)
            + "</div>")
    return "\n".join(lines), html


# ---------------------------------------------------------------- LLM


SYSTEM_PROMPT = (
    "You write the short daily report of a private camera system. Write it in {language}. Use only the facts "
    "from the JSON in the user message. Do not invent anything: no additional times, numbers, names or places, no "
    "guesses about intentions. Write times exactly as in the facts (HH:MM, 24-hour clock) and quantities as digits. "
    "At most 120 words, factual and friendly, continuous prose without a heading, without bullet points and "
    "without Markdown. You may leave out unimportant details (e.g. the number of notifications).")


def system_prompt() -> str:
    """Systemanweisung mit der Systemsprache (bzw. der Sprache der Anfrage bei einer Vorschau)."""
    return SYSTEM_PROMPT.format(language=f"{i18n.t('common.language_name')} (language code {i18n.current()})")


def llm_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Kompakte Fakten für das Sprachmodell: Ortszeiten als Text, ohne Bilder/URLs/IDs/Pfade; Bezeichnungen
    (Objektklassen, Orte) in der aktuellen Sprache."""
    end = ref_end(facts)
    head, span = period_text(facts)
    pe = facts.get("people") or {}
    ve = facts.get("vehicles") or {}
    an = facts.get("animals") or {}
    inv = facts.get("inventory") or {}
    out: dict[str, Any] = {
        "day": head, "period": span,
        "people": {
            "detected_times": int(pe.get("events") or 0),
            "known": [{"name": p["display_name"], "times": p["count"], "first": _t(p.get("first"), end),
                       "last": _t(p.get("last"), end), "cameras": p.get("cameras") or []}
                      for p in pe.get("named") or []],
            "unknown_faces": int(pe.get("unknown_faces") or 0),
            "of_which_new": int(pe.get("new_faces") or 0),
            "without_face_times": int(pe.get("without_face") or 0)},
        "vehicles": [{"type": label_name(e["label"], 2), "count": e["count"], "first": _t(e.get("first"), end),
                      "last": _t(e.get("last"), end)} for e in ve.get("by_label") or []],
        "licence_plates": [{"name": p["display_name"], "plate": p.get("plate_text"), "times": p["count"],
                            "first": _t(p.get("first"), end)} for p in ve.get("plates") or []],
        "animals": [{"type": label_name(e["label"], 2), "count": e["count"], "last": _t(e.get("last"), end)}
                    for e in an.get("by_label") or []],
        "named_animals": [{"name": p["display_name"], "times": p["count"], "last": _t(p.get("last"), end),
                           "cameras": p.get("cameras") or []} for p in an.get("named") or []],
        "bowls": [{"pet": v.get("pet") or label_name(v.get("label") or ""),
                   "place": purpose_name(v.get("purpose")) if v.get("purpose") in PURPOSE_DE else v.get("purpose_de"),
                   "times": v.get("count"), "last": _t(v.get("last"), end)} for v in an.get("visits") or []],
        "items": {"summary": inv.get("summary"),
                  "changes": [c.get("text") for c in inv.get("changes") or [] if c.get("text")][:8]}
        if inv else None,
        "rules": [{"name": r["name"], "times": r["count"], "last": _t(r.get("last"), end)}
                  for r in facts.get("rules") or []],
        "notifications": {source_name(k): v
                          for k, v in ((facts.get("notifications") or {}).get("by_source") or {}).items()},
        "cameras": [c.get("name") for c in (facts.get("system") or {}).get("cameras") or [] if c.get("name")],
        "cameras_offline": [c.get("name") for c in (facts.get("system") or {}).get("offline_cameras") or []],
    }
    return out


# Zahlwörter beider Sprachen (die Prüfung kennt immer beide – das Modell mischt gelegentlich)
_NUMWORDS_DE = {
    "null": 0, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15,
    "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19, "zwanzig": 20, "dreißig": 30,
    "vierzig": 40, "fünfzig": 50, "sechzig": 60, "hundert": 100,
}
_NUMWORDS_EN = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "hundred": 100,
}
_NUMWORDS = {**_NUMWORDS_DE, **_NUMWORDS_EN}
_HOURWORDS = dict(_NUMWORDS, ein=1, eine=1)
_NUM_ALT = "|".join(sorted((k for k in _HOURWORDS if k not in ("null", "zero")), key=len, reverse=True))
_RE_DATE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{2,4})?")
_RE_DATE_SLASH = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")
_RE_TIME = re.compile(r"(?<![\d.:])(\d{1,2})[:.](\d{2})(?![\d])")
_RE_HOUR = re.compile(r"(?<![\d.:,])(\d{1,2})\s*(?:Uhr\b|o['’]clock\b)", re.I)
_RE_AMPM = re.compile(r"(?<![\d.:,])(\d{1,2})\s*([ap])(?:\.m\.|m)(?!\w)", re.I)          # nur bei Englisch
_RE_HALB = re.compile(rf"\b(halb|viertel nach|viertel vor|dreiviertel)\s+({_NUM_ALT}|\d{{1,2}})\b", re.I)
_RE_PAST = re.compile(rf"\b(half past|quarter past|quarter to)\s+({_NUM_ALT}|\d{{1,2}})\b", re.I)
_RE_WORD_UHR = re.compile(rf"\b({_NUM_ALT})\s+(?:Uhr|o['’]clock)\b", re.I)
_RE_MAL = re.compile(rf"\b({'|'.join(k for k in _NUMWORDS_DE if k not in ('null',))})mal\b", re.I)
_RE_TWICE = re.compile(r"\b(twice)\b", re.I)
_RE_NUMWORD = re.compile(rf"\b({'|'.join(k for k in _NUMWORDS if k not in ('null', 'eins', 'zero', 'one'))})\b",
                         re.I)
_RE_PLATE = re.compile(r"\b[A-ZÄÖÜ]{1,3}[- ][A-Z]{1,2}[ -]?\d{1,4}[EH]?\b")
# „Person #3“, "Animal #2", „Gegenstand #4“ … – Anzeigenamen unbenannter Identitäten jeder Sprache
_RE_AUTONAME = re.compile(r"\b([A-ZÄÖÜ][\wäöüß]+)\s*#\s*(\d+)\b")


def _hour(v: str) -> Optional[int]:
    v = v.lower()
    if v.isdigit():
        return int(v)
    return _HOURWORDS.get(v)


def check_plausible(text: str, lf: dict[str, Any], known_names: list[str]) -> list[str]:
    """Probleme (in der aktuellen Sprache); leere Liste = plausibel. `lf` = llm_facts, `known_names` = alle
    benannten Identitäten (auch solche, die im Zeitraum nicht vorkamen)."""
    def P(kind: str, txt: str) -> str:
        return i18n.t(f"report.problem.{kind}", text=txt)

    problems: list[str] = []
    allowed = json.dumps(lf, ensure_ascii=False)
    rx_t = r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)"
    times = {(int(h) % 24, int(m)) for h, m in re.findall(rx_t, allowed)}
    dates = {(int(d), int(m)) for d, m, _ in _RE_DATE.findall(allowed)} \
        | {(int(d), int(m)) for d, m, _ in _RE_DATE_SLASH.findall(re.sub(rx_t, " ", allowed))}
    # Zahlen ohne die Ziffern aus Uhrzeiten/Datumsangaben – sonst wäre fast jede kleine Zahl „erlaubt“
    numbers = {int(x) for x in re.findall(
        r"\d+", _RE_DATE_SLASH.sub(" ", _RE_DATE.sub(" ", re.sub(rx_t, " ", allowed))))}
    names_in_facts = set(re.findall(r'"(?:name|pet)": "([^"]+)"', allowed))
    english = i18n.current() != "de"

    def time_ok(h: int, m: int) -> bool:
        return (h % 24, m) in times or ((h + 12) % 24, m) in times

    work = text
    # Erlaubte Namen (inkl. Kennzeichen/„Person #3“) entfernen, damit ihre Ziffern nicht als Zahl zählen
    for nm in sorted(names_in_facts, key=len, reverse=True):
        work = work.replace(nm, " ")
    for m in _RE_AUTONAME.finditer(work):
        problems.append(P("auto_name", m.group(0)))
    work = _RE_AUTONAME.sub(" ", work)
    for m in _RE_PLATE.finditer(work):
        if re.sub(r"[\s-]", "", m.group(0)) not in re.sub(r"[\s-]", "", allowed):
            problems.append(P("plate", m.group(0)))
    work = _RE_PLATE.sub(" ", work)
    # Datumsangaben 25.10. bzw. 25/10 → Tag/Monat müssen vorkommen
    for rx in (_RE_DATE, _RE_DATE_SLASH):
        for m in rx.finditer(work):
            d, mo = int(m.group(1)), int(m.group(2))
            if (d, mo) not in dates:
                problems.append(P("date", m.group(0)))
        work = rx.sub(" ", work)
    # Uhrzeiten in Worten: halb elf, viertel nach sieben, half past ten, quarter to eight
    for m in _RE_HALB.finditer(work):
        h = _hour(m.group(2))
        kind = m.group(1).lower()
        if h is None:
            continue
        hh, mm = {"halb": (h - 1, 30), "viertel nach": (h, 15), "viertel vor": (h - 1, 45),
                  "dreiviertel": (h - 1, 45)}[kind]
        if not time_ok(hh, mm):
            problems.append(P("time", m.group(0)))
    work = _RE_HALB.sub(" ", work)
    for m in _RE_PAST.finditer(work):
        h = _hour(m.group(2))
        kind = m.group(1).lower()
        if h is None:
            continue
        hh, mm = {"half past": (h, 30), "quarter past": (h, 15), "quarter to": (h - 1, 45)}[kind]
        if not time_ok(hh, mm):
            problems.append(P("time", m.group(0)))
    work = _RE_PAST.sub(" ", work)
    for m in _RE_WORD_UHR.finditer(work):
        h = _hour(m.group(1))
        if h is not None and not time_ok(h, 0):
            problems.append(P("time", m.group(0)))
    work = _RE_WORD_UHR.sub(" ", work)
    # Uhrzeiten in Ziffern: 10:42, 10.42 Uhr, 10 Uhr, 10 o'clock, 7 pm
    for m in _RE_TIME.finditer(work):
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 24 and mi < 60 and not time_ok(h, mi):
            problems.append(P("time", m.group(0)))
    work = _RE_TIME.sub(" ", work)
    for m in _RE_HOUR.finditer(work):
        if not time_ok(int(m.group(1)), 0):
            problems.append(P("time", m.group(0)))
    work = _RE_HOUR.sub(" ", work)
    if english:   # „am“ ist im Deutschen eine Präposition („5 am Napf“)
        for m in _RE_AMPM.finditer(work):
            h = int(m.group(1)) % 12 + (12 if m.group(2).lower() == "p" else 0)
            if not time_ok(h, 0):
                problems.append(P("time", m.group(0)))
        work = _RE_AMPM.sub(" ", work)
    # Zahlen in Ziffern und Worten (zwei, dreimal, two, twice …)
    for m in re.finditer(r"(?<![\w#])(\d+)(?:[.,]\d+)?", work):
        if int(m.group(1)) not in numbers:
            problems.append(P("number", m.group(0)))
    for rx in (_RE_MAL, _RE_NUMWORD):
        for m in rx.finditer(work):
            n = _NUMWORDS.get(m.group(1).lower())
            if n is not None and n not in numbers:
                problems.append(P("number", m.group(0)))
    for m in _RE_TWICE.finditer(work):
        if 2 not in numbers:
            problems.append(P("number", m.group(0)))
    # Namen bekannter Personen/Tiere, die im Zeitraum nicht vorkamen
    for nm in known_names:
        if not nm or len(nm) < 2 or nm in names_in_facts:
            continue
        if re.search(rf"(?<!\w){re.escape(nm)}(?!\w)", text):
            problems.append(P("name", nm))
    if re.search(r"[{}\[\]]|\"\w+\"\s*:|^\s*[-*#]", text, re.M):
        problems.append(i18n.t("report.problem.not_prose"))
    words = len(re.findall(r"\w+", text))
    if words > MAX_WORDS:
        problems.append(i18n.t("report.problem.too_long", n=words))
    # Dubletten entfernen, Reihenfolge behalten
    return list(dict.fromkeys(problems))


def render_llm(facts: dict[str, Any], known_names: list[str], kind: str = "daily") \
        -> tuple[str, str, str, dict[str, Any]]:
    """(text, html, generator, meta). Bei Fehlern oder unplausiblem Text: Satzbausteine mit FALLBACK."""
    meta: dict[str, Any] = {"requested": "llm", "ok": False}
    lf = llm_facts(facts)
    if not llm_client.configured():
        meta["error"] = i18n.t("report.no_llm")
    else:
        try:
            st = settings_store.get()
            res = llm_client.complete(
                [{"role": "user", "content": "Facts (JSON):\n" + json.dumps(lf, ensure_ascii=False)}],
                system=system_prompt(), max_tokens=400, temperature=0.2,
                timeout=float(st.get("llm_timeout_s") or 60))
            meta.update({"model": res.model, "provider": res.provider, "ms": res.ms})
            text = res.text.strip()
            problems = check_plausible(text, lf, known_names)
            if not problems:
                meta["ok"] = True
                head, span = period_text(facts)
                title = report_title(head, kind)
                html = (f"<div class=\"vg-report\"><p><strong>{_html.escape(title)}</strong> "
                        f"<span>({_html.escape(span)})</span></p>"
                        + "".join(f"<p>{_html.escape(p)}</p>" for p in text.split("\n") if p.strip()) + "</div>")
                return f"{title} ({span})\n{text}", html, f"llm:{res.model}"[:80], meta
            meta["problems"] = problems[:10]
            meta["rejected_text"] = text[:2000]
            log.info("Bericht: LLM-Text verworfen (%s) – Satzbausteine", "; ".join(problems[:3]))
        except llm_client.LLMError as e:
            meta["error"] = str(e)
            log.warning("Bericht: Sprachmodell nicht nutzbar (%s) – Satzbausteine", e)
    text, html = render_template(facts, kind)
    return text, html, FALLBACK, meta
