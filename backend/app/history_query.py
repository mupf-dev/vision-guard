"""Zeitmaschine (B1): deutsche Suchanfragen zusätzlich ins Englische übertragen.

SigLIP 2 versteht Deutsch, ist mit englischen Anfragen aber messbar besser (messungen.md §5: +0,13 mAP). Ohne
LLM (kostenpflichtig, standardmäßig aus) übersetzt dieses Modul Wort für Wort mit einem kleinen eingebauten
Wörterbuch, dem Inventar-Vokabular (`name_de` → erster englischer Prompt) und den Synonymen (eingebaut +
`inventory_synonyms`). Die Suche rechnet dann mit beiden Fassungen und nimmt je Bild die bessere Ähnlichkeit.

    expand("roter Karton im Flur", vocab, synonyms) → ["roter Karton im Flur", "red box in hallway"]
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .inventory_names import BUILTIN_SYNONYMS, normalize, stem

# Wörter ohne Inhalt (fallen in der englischen Fassung weg)
_DROP = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
    "mein", "meine", "meinen", "meinem", "meiner", "meines", "unser", "unsere", "unseren", "unserem", "unserer",
    "wo", "wann", "war", "waren", "ist", "sind", "wurde", "gibt", "es", "zu", "sehen", "gesehen", "bild", "bilder",
    "zeig", "zeige", "mir", "suche", "finde", "bitte", "irgendwo", "irgendwann", "jemals", "mal",
}

# Präpositionen/Verbindungswörter
_GLUE = {
    "auf": "on", "im": "in the", "in": "in", "ins": "into the", "am": "at the", "an": "at", "unter": "under",
    "neben": "next to", "vor": "in front of", "hinter": "behind", "mit": "with", "ohne": "without",
    "bei": "near", "beim": "near the", "zwischen": "between", "ueber": "above", "und": "and", "oder": "or",
    "von": "of", "vom": "from the", "aus": "out of", "durch": "through", "gegen": "against",
}

# Kleines Szenen-Wörterbuch (normalisierte Grundformen, ä→ae …). Adjektive ohne Endung.
_WORDS = {
    # Farben
    "rot": "red", "blau": "blue", "gruen": "green", "gelb": "yellow", "schwarz": "black", "weiss": "white",
    "grau": "gray", "braun": "brown", "orange": "orange", "lila": "purple", "violett": "purple", "rosa": "pink",
    "pink": "pink", "bunt": "colorful", "silber": "silver", "golden": "golden", "beige": "beige",
    "dunkelblau": "dark blue", "hellblau": "light blue", "dunkelrot": "dark red", "hellgruen": "light green",
    # Eigenschaften
    "gross": "large", "klein": "small", "lang": "long", "kurz": "short", "hell": "bright", "dunkel": "dark",
    "offen": "open", "geoeffnet": "open", "geschlossen": "closed", "leer": "empty",
    "voll": "full", "nass": "wet", "kaputt": "broken", "neu": "new", "alt": "old", "viel": "many",
    "viele": "many", "mehrere": "several", "zwei": "two", "drei": "three", "eingeschaltet": "switched on",
    "ausgeschaltet": "switched off", "unordentlich": "messy", "aufgeraeumt": "tidy",
    # Personen/Tiere
    "person": "person", "personen": "people", "mensch": "person", "menschen": "people", "leute": "people",
    "mann": "man", "maenner": "men", "frau": "woman", "frauen": "women", "kind": "child", "kinder": "children",
    "baby": "baby", "jemand": "someone", "besucher": "visitor", "bote": "delivery man", "postbote": "mailman",
    "paketbote": "delivery man", "hund": "dog", "katze": "cat", "vogel": "bird", "pferd": "horse",
    "haustier": "pet", "tier": "animal",
    # Tätigkeiten/Haltungen
    "liegt": "lying", "liegend": "lying", "sitzt": "sitting", "sitzend": "sitting", "steht": "standing",
    "stehend": "standing", "geht": "walking", "laeuft": "walking", "rennt": "running", "schlaeft": "sleeping",
    "isst": "eating", "kocht": "cooking", "liest": "reading", "traegt": "carrying", "haelt": "holding",
    "spielt": "playing", "putzt": "cleaning", "arbeitet": "working", "telefoniert": "on the phone",
    "winkt": "waving", "gestuerzt": "fallen", "faellt": "falling", "boden": "floor",
    # Räume/Orte
    "flur": "hallway", "gang": "hallway", "diele": "hallway", "kueche": "kitchen", "wohnzimmer": "living room",
    "schlafzimmer": "bedroom", "bad": "bathroom", "badezimmer": "bathroom", "kinderzimmer": "kids room",
    "buero": "office", "arbeitszimmer": "office", "keller": "basement", "garage": "garage", "garten": "garden",
    "terrasse": "terrace", "balkon": "balcony", "eingang": "entrance", "einfahrt": "driveway",
    "haustuer": "front door", "strasse": "street", "gehweg": "sidewalk", "hof": "yard", "treppe": "stairs",
    "raum": "room", "zimmer": "room", "regal": "shelf", "schrank": "cupboard", "kuehlschrank": "fridge",
    "fenster": "window", "tuer": "door", "wand": "wall", "decke": "ceiling", "ecke": "corner",
    "fensterbank": "windowsill", "theke": "counter", "arbeitsplatte": "countertop", "supermarkt": "supermarket",
    # Fahrzeuge
    "auto": "car", "autos": "cars", "wagen": "car", "lkw": "truck", "laster": "truck", "bus": "bus",
    "fahrrad": "bicycle", "rad": "bicycle", "motorrad": "motorcycle", "roller": "scooter",
    "kinderwagen": "stroller", "transporter": "van",
    # Dinge
    "paket": "parcel", "karton": "cardboard box", "kiste": "box", "schachtel": "box", "kasten": "box",
    "tasche": "bag", "tuete": "bag", "einkaufstasche": "shopping bag", "rucksack": "backpack", "koffer": "suitcase",
    "brille": "glasses", "schluessel": "keys", "handy": "phone", "licht": "light", "lampe": "lamp",
    "kerze": "candle", "blumen": "flowers", "pflanze": "plant", "baum": "tree", "schnee": "snow",
    "regen": "rain", "sonne": "sun", "nacht": "night", "tag": "day", "feuer": "fire", "rauch": "smoke",
    "wasser": "water", "muell": "trash", "waesche": "laundry", "kleidung": "clothes", "jacke": "jacket",
    "mantel": "coat", "muetze": "hat", "hut": "hat", "schuhe": "shoes", "spielzeug": "toy", "ball": "ball",
    "buch": "book", "zeitung": "newspaper", "flasche": "bottle", "flaschen": "bottles", "glas": "glass",
    "tasse": "cup", "teller": "plate", "essen": "food", "pizza": "pizza", "obst": "fruit", "brot": "bread",
    "fernseher": "tv", "bildschirm": "screen", "computer": "computer", "laptop": "laptop", "stuhl": "chair",
    "tisch": "table", "sofa": "sofa", "couch": "couch", "bett": "bed", "teppich": "rug", "kissen": "pillow",
    "vorhang": "curtain", "gardine": "curtain", "uhr": "clock",
    "staubsauger": "vacuum cleaner", "leiter": "ladder", "werkzeug": "tools", "regenschirm": "umbrella",
    "schirm": "umbrella", "einkauf": "groceries", "einkaeufe": "groceries", "lieferung": "delivery",
    "post": "mail", "brief": "letter", "briefe": "letters",
}

_ADJ_ENDINGS = ("es", "er", "en", "em", "e")


def _vocab_words(vocab: Iterable[dict[str, Any]], custom: Optional[dict[str, list[str]]]) -> dict[str, str]:
    """Normalisierte deutsche Namen/Synonyme → englischer Prompt aus dem Inventar-Vokabular."""
    out: dict[str, str] = {}
    for e in vocab or []:
        if not isinstance(e, dict):
            continue
        prompts = e.get("prompts") or []
        en = str(prompts[0]) if prompts else str(e.get("label") or "").replace("_", " ")
        if not en:
            continue
        label = str(e.get("label") or "")
        names = [e.get("name_de") or ""] + BUILTIN_SYNONYMS.get(label, []) + list((custom or {}).get(label, []))
        for n in names:
            k = normalize(str(n))
            if k and " " not in k:
                out.setdefault(k, en)
    return out


def _lookup(w: str, vw: dict[str, str]) -> Optional[str]:
    for cand in (w, stem(w)):
        if cand in _WORDS:
            return _WORDS[cand]
        if cand in vw:
            return vw[cand]
    for suf in _ADJ_ENDINGS:                      # „roter“, „rote“, „roten“ → „rot“
        if len(w) > len(suf) + 2 and w.endswith(suf) and w[: -len(suf)] in _WORDS:
            return _WORDS[w[: -len(suf)]]
    return None


def to_english(query: str, vocab: Iterable[dict[str, Any]] = (),
               custom: Optional[dict[str, list[str]]] = None) -> Optional[str]:
    """Englische Fassung oder None (nichts übersetzbar bzw. Anfrage schon englisch)."""
    words = normalize(query).split()
    if not words:
        return None
    vw = _vocab_words(vocab, custom)
    out: list[str] = []
    content = known = 0
    for w in words:
        if w in _DROP:
            continue
        if w in _GLUE:
            out.append(_GLUE[w])
            continue
        content += 1
        en = _lookup(w, vw)
        if en:
            known += 1
            out.append(en)
        else:
            out.append(w)                         # Eigennamen/unbekannte Wörter unverändert
    if not content or known == 0 or known * 2 < content:
        return None
    phrase = " ".join(out).strip()
    if not phrase or phrase == normalize(query):
        return None
    return phrase


def expand(query: str, vocab: Iterable[dict[str, Any]] = (),
           custom: Optional[dict[str, list[str]]] = None) -> list[str]:
    q = " ".join((query or "").split())
    out = [q]
    en = to_english(q, vocab, custom)
    if en and en.lower() != q.lower():
        out.append(en)
    return out
