"""B3 Tagesbericht – Text erzeugen.

Zwei Wege, Ergebnis jeweils `text` + einfaches `html`:
- `template` (Standard, ohne KI): feste deutsche Satzbausteine je Abschnitt.
- `llm`: kompakte Fakten (ohne Bilder, URLs, IDs) an den gemeinsamen LLM-Client (`llm_client.complete`).
  **Plausibilitätsprüfung:** Uhrzeiten (auch „halb elf“, „viertel nach 7“, „10 Uhr“), Zahlen (auch Zahlwörter wie
  „zwei“, „dreimal“), Datumsangaben, Kennzeichen, „Person #N“ und bekannte Namen im Text müssen in den Fakten
  stehen; sonst Rückfall auf Satzbausteine (`generator = "template (Fallback)"`).
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .. import llm_client, settings_store
from ..config import config
from ..inventory import local_time_text
from .facts import PURPOSE_DE, SOURCE_DE, WEEKDAYS, label_name

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
        parts.append(f"{more} weitere")
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " und " + parts[-1]


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
    head = f"{WEEKDAYS[loc.weekday()]}, {loc.strftime('%d.%m.')}"
    end_txt = "24:00" if ref != (_dt(p.get("end")) or ref) else _t(p.get("end"), ref)
    return head, f"{_t(p.get('start'), ref)} – {end_txt}"


# ---------------------------------------------------------------- Satzbausteine


def sections(facts: dict[str, Any]) -> list[tuple[str, str, str]]:
    """[(key, Titel, Satz)] – Grundlage für Text, HTML und die aufklappbaren Abschnitte der Oberfläche."""
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
            parts.append("bekannt: " + _join(named))
        uf, nf = int(pe.get("unknown_faces") or 0), int(pe.get("new_faces") or 0)
        if uf:
            parts.append(f"{uf} unbekannte{'s Gesicht' if uf == 1 else ' Gesichter'}"
                         + (f", davon {nf} neu" if nf else ""))
        wf = int(pe.get("without_face") or 0)
        if wf:
            parts.append(f"{_times(wf)} ohne erkanntes Gesicht")
        out.append(("people", "Personen", f"{_times(n_person)} erkannt" + (" – " + "; ".join(parts) if parts else "")
                    + "."))
    else:
        out.append(("people", "Personen", "keine erkannt."))

    # Fahrzeuge
    ve = facts.get("vehicles") or {}
    if int(ve.get("total") or 0):
        by = [f"{int(e['count'])} {label_name(e['label'], int(e['count']))} (zuletzt {_t(e.get('last'), end)})"
              for e in ve.get("by_label") or []]
        s = _join(by)
        plates = []
        for p in ve.get("plates") or []:
            c = int(p.get("count") or 0)
            nm = p["display_name"] if not p.get("name") else f"{p['name']} ({p.get('plate_text') or ''})".replace(" ()", "")
            plates.append(f"{nm} {_t(p.get('first'), end)}" if c == 1 else f"{nm} {_times(c)}")
        if plates:
            more = int(ve.get("plates_total") or 0) - len(plates)
            s += "; Kennzeichen: " + _join(plates, more)
        out.append(("vehicles", "Fahrzeuge", s + "."))

    # Tiere
    an = facts.get("animals") or {}
    if int(an.get("total") or 0) or an.get("visits"):
        parts = []
        for p in an.get("named") or []:
            c = int(p.get("count") or 0)
            where = f", {p['cameras'][0]}" if p.get("cameras") else ""
            parts.append(f"{p['display_name']} {_t(p.get('first'), end)}{where}" if c == 1
                         else f"{p['display_name']} {_times(c)} (zuletzt {_t(p.get('last'), end)}{where})")
        s = _join(parts)
        named_n = sum(int(p.get("count") or 0) for p in an.get("named") or [])
        if int(an.get("total") or 0) > named_n:   # nur wenn es mehr gab als die benannten Tiere
            rest = [f"{int(e['count'])}× {label_name(e['label'])}" for e in an.get("by_label") or []]
            s = (s + "; insgesamt " if s else "") + _join(rest)
        visits = []
        for v in an.get("visits") or []:
            who = v.get("pet") or label_name(v.get("label") or "")
            visits.append(f"{who} {v.get('purpose_de') or PURPOSE_DE.get(v.get('purpose'), v.get('purpose'))} "
                          f"{_times(int(v.get('count') or 0))} (zuletzt {_t(v.get('last'), end)})")
        if visits:
            s += ("; " if s else "") + "Näpfe/Klo: " + _join(visits)
        out.append(("animals", "Tiere", s + "."))

    # Gegenstände (B2)
    inv = facts.get("inventory")
    if inv and sum(int(v or 0) for k, v in (inv.get("counts") or {}).items() if k != "transient"):
        out.append(("inventory", "Gegenstände", inv.get("summary") or ""))
    elif facts.get("inventory_error") and facts["inventory_error"] != "Zeitraum außerhalb der Aufbewahrung":
        out.append(("inventory", "Gegenstände", f"nicht auswertbar ({facts['inventory_error']})."))

    # Regeln
    rules = facts.get("rules") or []
    if rules:
        parts = []
        for r in rules:
            c = int(r.get("count") or 0)
            parts.append(f"„{r['name']}“ {_t(r.get('last'), end)}" if c == 1
                         else f"„{r['name']}“ {_times(c)} (zuletzt {_t(r.get('last'), end)})")
        out.append(("rules", "Regeln", _join(parts) + "."))

    # Hinweise und System
    no = (facts.get("notifications") or {}).get("by_source") or {}
    sys_ = facts.get("system") or {}
    parts = []
    for src, n in sorted(no.items(), key=lambda kv: -kv[1]):
        parts.append(f"{_times(int(n))} {SOURCE_DE.get(src, src)}")
    s = (_join(parts) + ".") if parts else ""
    off = [c.get("name") for c in sys_.get("offline_cameras") or []]
    if off:
        s += (" " if s else "") + (f"Kamera {off[0]} ist" if len(off) == 1 else f"Kameras {_join(off)} sind") \
            + " aktuell offline."
    if s:
        out.append(("system", "Hinweise", s))

    if n_person == 0 and len(out) == 1:
        out = [("quiet", "Ruhiger Zeitraum", "keine Personen, Fahrzeuge, Tiere, Regeln oder Veränderungen.")]
    return out


def render_template(facts: dict[str, Any], kind: str = "daily") -> tuple[str, str]:
    head, span = period_text(facts)
    title = f"Tagesbericht {head}" if kind == "daily" else f"Bericht {head}"
    secs = sections(facts)
    lines = [f"{title} ({span})"] + [f"{t}: {s}" for _, t, s in secs]
    esc = _html.escape
    html = (f"<div class=\"vg-report\"><p><strong>{esc(title)}</strong> <span>({esc(span)})</span></p>"
            + "".join(f"<p data-section=\"{esc(k)}\"><strong>{esc(t)}:</strong> {esc(s)}</p>" for k, t, s in secs)
            + "</div>")
    return "\n".join(lines), html


# ---------------------------------------------------------------- LLM


SYSTEM_PROMPT = (
    "Du schreibst den kurzen Tagesbericht eines privaten Kamerasystems auf Deutsch. Verwende ausschließlich die "
    "Fakten aus dem JSON der Nutzernachricht. Erfinde nichts: keine zusätzlichen Uhrzeiten, Zahlen, Namen oder "
    "Orte, keine Vermutungen über Absichten. Uhrzeiten genau wie in den Fakten im Format HH:MM, Anzahlen als "
    "Ziffern. Höchstens 120 Wörter, sachlich und freundlich, Fließtext ohne Überschrift, ohne Aufzählungszeichen "
    "und ohne Markdown. Unwichtiges (z. B. Anzahl Hinweise) darfst du weglassen.")


def llm_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Kompakte Fakten für das Sprachmodell: Ortszeiten als Text, ohne Bilder/URLs/IDs/Pfade."""
    end = ref_end(facts)
    head, span = period_text(facts)
    pe = facts.get("people") or {}
    ve = facts.get("vehicles") or {}
    an = facts.get("animals") or {}
    inv = facts.get("inventory") or {}
    out: dict[str, Any] = {
        "tag": head, "zeitraum": span,
        "personen": {
            "erkannt_mal": int(pe.get("events") or 0),
            "bekannte": [{"name": p["display_name"], "mal": p["count"], "zuerst": _t(p.get("first"), end),
                          "zuletzt": _t(p.get("last"), end), "kameras": p.get("cameras") or []}
                         for p in pe.get("named") or []],
            "unbekannte_gesichter": int(pe.get("unknown_faces") or 0),
            "davon_neu": int(pe.get("new_faces") or 0),
            "ohne_gesicht_mal": int(pe.get("without_face") or 0)},
        "fahrzeuge": [{"art": label_name(e["label"], 2), "anzahl": e["count"], "zuerst": _t(e.get("first"), end),
                       "zuletzt": _t(e.get("last"), end)} for e in ve.get("by_label") or []],
        "kennzeichen": [{"name": p["display_name"], "kennzeichen": p.get("plate_text"), "mal": p["count"],
                         "zuerst": _t(p.get("first"), end)} for p in ve.get("plates") or []],
        "tiere": [{"art": label_name(e["label"], 2), "anzahl": e["count"], "zuletzt": _t(e.get("last"), end)}
                  for e in an.get("by_label") or []],
        "tiere_mit_namen": [{"name": p["display_name"], "mal": p["count"], "zuletzt": _t(p.get("last"), end),
                             "kameras": p.get("cameras") or []} for p in an.get("named") or []],
        "naepfe": [{"tier": v.get("pet") or label_name(v.get("label") or ""), "ort": v.get("purpose_de"),
                    "mal": v.get("count"), "zuletzt": _t(v.get("last"), end)} for v in an.get("visits") or []],
        "gegenstaende": {"zusammenfassung": inv.get("summary"),
                         "einzeln": [c.get("text") for c in inv.get("changes") or [] if c.get("text")][:8]}
        if inv else None,
        "regeln": [{"name": r["name"], "mal": r["count"], "zuletzt": _t(r.get("last"), end)}
                   for r in facts.get("rules") or []],
        "hinweise": {k: v for k, v in ((facts.get("notifications") or {}).get("by_source") or {}).items()},
        "kameras": [c.get("name") for c in (facts.get("system") or {}).get("cameras") or [] if c.get("name")],
        "kameras_offline": [c.get("name") for c in (facts.get("system") or {}).get("offline_cameras") or []],
    }
    return out


_NUMWORDS = {
    "null": 0, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15,
    "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19, "zwanzig": 20, "dreißig": 30,
    "vierzig": 40, "fünfzig": 50, "sechzig": 60, "hundert": 100,
}
_HOURWORDS = dict(_NUMWORDS, ein=1, eine=1)
_NUM_ALT = "|".join(sorted((k for k in _HOURWORDS if k not in ("null",)), key=len, reverse=True))
_RE_DATE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{2,4})?")
_RE_TIME = re.compile(r"(?<![\d.:])(\d{1,2})[:.](\d{2})(?![\d])")
_RE_HOUR = re.compile(r"(?<![\d.:,])(\d{1,2})\s*Uhr\b")
_RE_HALB = re.compile(rf"\b(halb|viertel nach|viertel vor|dreiviertel)\s+({_NUM_ALT}|\d{{1,2}})\b", re.I)
_RE_WORD_UHR = re.compile(rf"\b({_NUM_ALT})\s+Uhr\b", re.I)
_RE_MAL = re.compile(rf"\b({'|'.join(k for k in _NUMWORDS if k not in ('null',))})mal\b", re.I)
_RE_NUMWORD = re.compile(rf"\b({'|'.join(k for k in _NUMWORDS if k not in ('null', 'eins'))})\b", re.I)
_RE_PLATE = re.compile(r"\b[A-ZÄÖÜ]{1,3}[- ][A-Z]{1,2}[ -]?\d{1,4}[EH]?\b")
_RE_AUTONAME = re.compile(r"\b(Person|Tier|Gegenstand)\s*#\s*(\d+)\b")


def _hour(v: str) -> Optional[int]:
    v = v.lower()
    if v.isdigit():
        return int(v)
    return _HOURWORDS.get(v)


def check_plausible(text: str, lf: dict[str, Any], known_names: list[str]) -> list[str]:
    """Probleme (deutsch); leere Liste = plausibel. `lf` = llm_facts, `known_names` = alle benannten
    Identitäten (auch solche, die im Zeitraum nicht vorkamen)."""
    problems: list[str] = []
    allowed = json.dumps(lf, ensure_ascii=False)
    rx_t = r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)"
    times = {(int(h) % 24, int(m)) for h, m in re.findall(rx_t, allowed)}
    dates = {(int(d), int(m)) for d, m, _ in _RE_DATE.findall(allowed)}
    # Zahlen ohne die Ziffern aus Uhrzeiten/Datumsangaben – sonst wäre fast jede kleine Zahl „erlaubt“
    numbers = {int(x) for x in re.findall(r"\d+", _RE_DATE.sub(" ", re.sub(rx_t, " ", allowed)))}
    names_in_facts = set(re.findall(r'"(?:name|tier)": "([^"]+)"', allowed))

    def time_ok(h: int, m: int) -> bool:
        return (h % 24, m) in times or ((h + 12) % 24, m) in times

    work = text
    # Erlaubte Namen (inkl. Kennzeichen/„Person #3“) entfernen, damit ihre Ziffern nicht als Zahl zählen
    for nm in sorted(names_in_facts, key=len, reverse=True):
        work = work.replace(nm, " ")
    for m in _RE_AUTONAME.finditer(work):
        problems.append(f"„{m.group(0)}“ steht nicht in den Fakten")
    work = _RE_AUTONAME.sub(" ", work)
    for m in _RE_PLATE.finditer(work):
        if re.sub(r"[\s-]", "", m.group(0)) not in re.sub(r"[\s-]", "", allowed):
            problems.append(f"Kennzeichen „{m.group(0)}“ steht nicht in den Fakten")
    work = _RE_PLATE.sub(" ", work)
    # Datumsangaben 25.10. → Tag/Monat müssen vorkommen
    for m in _RE_DATE.finditer(work):
        d, mo = int(m.group(1)), int(m.group(2))
        if (d, mo) not in dates:
            problems.append(f"Datum „{m.group(0)}“ steht nicht in den Fakten")
    work = _RE_DATE.sub(" ", work)
    # Uhrzeiten in Worten: halb elf, viertel nach sieben, zehn Uhr
    for m in _RE_HALB.finditer(work):
        h = _hour(m.group(2))
        kind = m.group(1).lower()
        if h is None:
            continue
        hh, mm = {"halb": (h - 1, 30), "viertel nach": (h, 15), "viertel vor": (h - 1, 45),
                  "dreiviertel": (h - 1, 45)}[kind]
        if not time_ok(hh, mm):
            problems.append(f"Uhrzeit „{m.group(0)}“ steht nicht in den Fakten")
    work = _RE_HALB.sub(" ", work)
    for m in _RE_WORD_UHR.finditer(work):
        h = _hour(m.group(1))
        if h is not None and not time_ok(h, 0):
            problems.append(f"Uhrzeit „{m.group(0)}“ steht nicht in den Fakten")
    work = _RE_WORD_UHR.sub(" ", work)
    # Uhrzeiten in Ziffern: 10:42, 10.42 Uhr, 10 Uhr
    for m in _RE_TIME.finditer(work):
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 24 and mi < 60 and not time_ok(h, mi):
            problems.append(f"Uhrzeit „{m.group(0)}“ steht nicht in den Fakten")
    work = _RE_TIME.sub(" ", work)
    for m in _RE_HOUR.finditer(work):
        if not time_ok(int(m.group(1)), 0):
            problems.append(f"Uhrzeit „{m.group(0)}“ steht nicht in den Fakten")
    work = _RE_HOUR.sub(" ", work)
    # Zahlen in Ziffern und Worten (zwei, dreimal …)
    for m in re.finditer(r"(?<![\w#])(\d+)(?:[.,]\d+)?", work):
        if int(m.group(1)) not in numbers:
            problems.append(f"Zahl „{m.group(0)}“ steht nicht in den Fakten")
    for rx in (_RE_MAL, _RE_NUMWORD):
        for m in rx.finditer(work):
            n = _NUMWORDS.get(m.group(1).lower())
            if n is not None and n not in numbers:
                problems.append(f"Zahl „{m.group(0)}“ steht nicht in den Fakten")
    # Namen bekannter Personen/Tiere, die im Zeitraum nicht vorkamen
    for nm in known_names:
        if not nm or len(nm) < 2 or nm in names_in_facts:
            continue
        if re.search(rf"(?<!\w){re.escape(nm)}(?!\w)", text):
            problems.append(f"Name „{nm}“ steht nicht in den Fakten")
    if re.search(r"[{}\[\]]|\"\w+\"\s*:|^\s*[-*#]", text, re.M):
        problems.append("kein Fließtext (JSON, Liste oder Markdown)")
    words = len(re.findall(r"\w+", text))
    if words > MAX_WORDS:
        problems.append(f"zu lang ({words} Wörter)")
    # Dubletten entfernen, Reihenfolge behalten
    return list(dict.fromkeys(problems))


def render_llm(facts: dict[str, Any], known_names: list[str], kind: str = "daily") \
        -> tuple[str, str, str, dict[str, Any]]:
    """(text, html, generator, meta). Bei Fehlern oder unplausiblem Text: Satzbausteine mit FALLBACK."""
    meta: dict[str, Any] = {"requested": "llm", "ok": False}
    lf = llm_facts(facts)
    if not llm_client.configured():
        meta["error"] = "Kein Sprachmodell eingerichtet"
    else:
        try:
            st = settings_store.get()
            res = llm_client.complete(
                [{"role": "user", "content": "Fakten (JSON):\n" + json.dumps(lf, ensure_ascii=False)}],
                system=SYSTEM_PROMPT, max_tokens=400, temperature=0.2,
                timeout=float(st.get("llm_timeout_s") or 60))
            meta.update({"model": res.model, "provider": res.provider, "ms": res.ms})
            text = res.text.strip()
            problems = check_plausible(text, lf, known_names)
            if not problems:
                meta["ok"] = True
                head, span = period_text(facts)
                title = f"Tagesbericht {head}" if kind == "daily" else f"Bericht {head}"
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
