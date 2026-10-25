"""C4-VLM-Modus (Welle 4): Vorräte per Bildmodell messen – Fragen bauen und Antworten in Zahlen übersetzen.

Ohne Abhängigkeiten (nur ``re``), damit es auch im Messwerkzeug (``scripts/vlm_supply_bench.py``) läuft.

Messarten (``params.measure`` eines Sensors der Art ``vlm``; bei ``verify_vlm`` von ``inventory_count``/``fill``
folgt sie aus der Art):

- ``yesno`` – bisheriges Verhalten (Ja/Nein → 1/0, binär).
- ``count`` – „How many … ?“ → Anzahl (ganze Zahl ≥ 0). Zustände wie ``inventory_count``.
- ``fill``  – „How full is … ?“ → Füllstand 0..1 (Wörter full/half/low/empty oder Prozent). Zustände wie ``fill``.

Die Fragen sind englisch (Moondream versteht Englisch, messungen.md §6); Antworten von Cloud-Modellen dürfen auch
deutsch sein. Unklare Antworten → ``None`` (der Sensor wertet die Messung dann nicht).
"""
from __future__ import annotations

import re
from typing import Optional

MEASURES = ("yesno", "count", "fill")

COUNT_TEMPLATE = "How many {item} are there? Answer with a number only."
FILL_TEMPLATE = "How full is the {item}? Answer with one word: full, half, low or empty."

_NUM_WORDS = {
    # englisch
    "zero": 0, "no": 0, "none": 0, "nothing": 0, "one": 1, "a": 1, "an": 1, "single": 1, "two": 2, "couple": 2,
    "pair": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "dozen": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    # deutsch (Cloud-Modelle antworten gelegentlich deutsch)
    "null": 0, "kein": 0, "keine": 0, "keinen": 0, "eins": 1, "ein": 1, "eine": 1, "einen": 1, "zwei": 2,
    "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
    "elf": 11, "zwölf": 12, "zwoelf": 12, "dutzend": 12,
}
# Diese Wörter zählen nur am Satzanfang bzw. nach „there is/are“, sonst sind sie Artikel/Füllwörter
_WEAK = {"a", "an", "no", "ein", "eine", "einen", "kein", "keine", "keinen", "single", "couple", "pair"}
_VAGUE = ("several", "many", "multiple", "some", "few", "lots", "a lot", "numerous", "mehrere", "viele", "einige")


def build_question(measure: str, item: str = "", question: str = "") -> str:
    """Eigene Frage hat Vorrang; sonst Vorlage mit dem (englischen) Artikelnamen."""
    q = " ".join((question or "").split())
    if q:
        return q[:300]
    it = " ".join((item or "").split())[:80]
    if not it or measure not in ("count", "fill"):
        return ""
    return (COUNT_TEMPLATE if measure == "count" else FILL_TEMPLATE).format(item=it)


def _clean(answer: str) -> str:
    a = (answer or "").strip().lower()
    a = a.replace("’", "'").replace("–", "-")
    return re.sub(r"[*_`\"]", "", a)


def parse_count(answer: str, max_count: int = 999) -> Optional[int]:
    """„3“, „There are three bottles.“, „No bottles“, „Drei Flaschen“ → Zahl; „several“/leer → None.

    Regeln: erste Ziffernfolge gewinnt (außer Prozentangaben und Jahreszahlen-artige Zahlen > max_count);
    sonst das erste Zahlwort. „a/an/ein“ zählen nur als 1, wenn sie die Antwort beginnen bzw. direkt auf
    „there is/there's“ folgen („There is a bottle“), „no/kein“ nur am Anfang bzw. nach „there are“."""
    a = _clean(answer)
    if not a:
        return None
    for m in re.finditer(r"(?<![\d.,])(\d{1,4})(?![\d.,]*\s*%)(?!\d)", a):
        n = int(m.group(1))
        if n <= max_count:
            return n
    words = re.findall(r"[a-zäöüß]+", a)
    if not words:
        return None
    lead = {0}
    for i in range(len(words) - 1):
        if words[i] in ("there", "es", "it") and words[i + 1] in ("is", "are", "gibt", "sind", "s"):
            lead.add(i + 2)
        if words[i] in ("i", "ich") and words[i + 1] in ("see", "sehe", "count", "zähle"):
            lead.add(i + 2)
    for i, w in enumerate(words):
        if w not in _NUM_WORDS:
            continue
        if w in _WEAK and i not in lead:
            continue
        if w in ("a", "an") and i + 1 < len(words) and words[i + 1] in ("few", "couple", "lot", "number", "dozen",
                                                                          "pair"):
            if words[i + 1] == "couple" or words[i + 1] == "pair":
                return 2
            if words[i + 1] == "dozen":
                return 12
            return None             # „a few“, „a lot“, „a number of“
        return _NUM_WORDS[w]
    if any(v in a for v in _VAGUE):
        return None
    return None


# Reihenfolge wichtig: zusammengesetzte Ausdrücke vor den einzelnen Wörtern
_FILL_PATTERNS: tuple[tuple[str, float], ...] = (
    (r"\b(completely|totally|entirely) empty\b|\bvöllig leer\b|\bganz leer\b|\bkomplett leer\b", 0.0),
    (r"\b(almost|nearly|mostly|virtually|practically) empty\b|\bfast leer\b|\bbeinahe leer\b|\bfast nichts\b", 0.15),
    (r"\b(almost|nearly|mostly) full\b|\bfast voll\b|\bziemlich voll\b|\bwell stocked\b|\bgut gefüllt\b"
     r"|\bthree[- ]quarters?\b|\bdreiviertel\b", 0.8),
    (r"\b(completely|totally|entirely|fully) (full|stocked|filled)\b|\bvöllig voll\b|\bganz voll\b"
     r"|\bkomplett voll\b|\bfull to the\b", 1.0),
    (r"\bhalf\b|\bhalb\b|\bhalfway\b|\bmedium\b|\bmoderately\b|\bpartially\b|\bpartly\b|\bteilweise\b", 0.5),
    (r"\b(a )?quarter\b|\bviertel\b", 0.25),
    (r"\blow\b|\blittle\b|\bfew\b|\bnot much\b|\bsparse(ly)?\b|\bwenig\b|\bniedrig\b|\bknapp\b|\brunning out\b", 0.2),
    (r"\bempty\b|\bleer\b|\bnothing\b|\bnone\b|\bno (items|food|water|fruit|bottles?)\b", 0.0),
    (r"\bfull\b|\bvoll\b|\bfilled\b|\bstocked\b|\bplenty\b|\blots\b|\bmany\b", 1.0),
)


def parse_fill(answer: str) -> Optional[float]:
    """„full“ → 1, „half“ → 0,5, „almost empty“ → 0,15, „empty“ → 0, „about 30%“ → 0,3; unklar → None."""
    a = _clean(answer)
    if not a:
        return None
    m = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*(%|percent|prozent)", a)
    if m:
        v = float(m.group(1).replace(",", "."))
        if 0 <= v <= 100:
            return round(v / 100.0, 3)
    for pat, val in _FILL_PATTERNS:
        if re.search(pat, a):
            return val
    return None


def parse(measure: str, answer: str) -> Optional[float]:
    """Antwort → Messwert (count: Anzahl, fill: 0..1) oder None."""
    if measure == "count":
        n = parse_count(answer)
        return None if n is None else float(n)
    if measure == "fill":
        return parse_fill(answer)
    return None


def level_state(measure: str, value: float, params: dict, state_set: str = "level") -> str:
    """Rohzustand aus einem Messwert – gleiche Schwellen wie inventory_count (count) bzw. fill (fill)."""
    if measure == "count":
        mn = int(params.get("min_count", 1) or 1)
        if state_set == "level":
            return "ok" if value >= mn else ("out" if value <= 0 else "low")
        return "on" if value >= mn else "off"
    if value < float(params.get("out_below", 0.15)):
        return "out"
    return "low" if value < float(params.get("low_below", 0.4)) else "ok"
