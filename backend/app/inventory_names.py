"""Deutsche Namen, Synonyme und Suche für Gegenstände (Inventar).

Die Anzeige-Namen kommen aus dem Settings-Vokabular (`inventory_vocabulary[].name_de`); hier stehen eingebaute
Synonyme (zusätzlich zu `inventory_synonyms` aus den Settings) und die Normalisierung/Suche für Fragen wie
„Wo ist meine Fernbedienung?“.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Optional

from rapidfuzz import fuzz

# Eingebaute deutsche Suchwörter je Vokabular-Schlüssel (label). Ergänzt durch Setting `inventory_synonyms`.
BUILTIN_SYNONYMS: dict[str, list[str]] = {
    "keys": ["Schlüsselbund", "Haustürschlüssel", "Autoschlüssel", "Wohnungsschlüssel", "Schlüsselanhänger"],
    "wallet": ["Portemonnaie", "Portmonee", "Geldbeutel", "Brieftasche", "Börse", "Geldtasche"],
    "phone": ["Smartphone", "Mobiltelefon", "Telefon", "iPhone", "Natel", "Mobilteil"],
    "eyeglasses": ["Lesebrille", "Gleitsichtbrille", "Augengläser"],
    "sunglasses": ["Sonnenbrillen"],
    "wristwatch": ["Uhr", "Smartwatch", "Taschenuhr"],
    "headphones": ["Ohrhörer", "Kopfhoerer", "Headset", "Earbuds", "AirPods", "In-Ear"],
    "backpack": ["Ranzen", "Schulranzen", "Tornister"],
    "handbag": ["Tasche", "Umhängetasche", "Damentasche", "Clutch"],
    "umbrella": ["Schirm", "Knirps"],
    "medicine": ["Tabletten", "Pillen", "Medikament", "Arznei", "Pillendose", "Tablettendose", "Medizin"],
    "jacket": ["Mantel", "Anorak", "Regenjacke", "Weste"],
    "remote": ["Fernsteuerung", "TV-Fernbedienung", "Fernbedienungen", "Remote", "Drücker"],
    "laptop": ["Notebook", "Rechner", "Computer", "MacBook", "Klapprechner"],
    "tablet": ["iPad", "Tablett-PC", "E-Reader", "Kindle"],
    "charger": ["Ladegerät", "Netzteil", "Kabel", "USB-Kabel", "Powerbank", "Lader"],
    "game_controller": ["Controller", "Gamepad", "Joystick", "Konsolen-Controller"],
    "tv": ["TV", "Fernsehgerät", "Bildschirm", "Glotze", "Monitor"],
    "flashlight": ["Stirnlampe", "Handlampe", "Taschenleuchte"],
    "mug": ["Becher", "Kaffeetasse", "Kaffeebecher", "Teetasse", "Pott"],
    "glass": ["Trinkglas", "Wasserglas", "Weinglas"],
    "bottle": ["Wasserflasche", "Trinkflasche", "Weinflasche", "Bierflasche"],
    "plate": ["Frühstücksteller", "Suppenteller", "Kuchenteller"],
    "bowl": ["Schale", "Müslischale", "Salatschüssel"],
    "fork": ["Gabeln"],
    "spoon": ["Teelöffel", "Esslöffel"],
    "knife": ["Küchenmesser", "Taschenmesser", "Brotmesser"],
    "pot": ["Kochtopf"],
    "pan": ["Bratpfanne"],
    "cutting_board": ["Brett", "Frühstücksbrett", "Hackbrett"],
    "kettle": ["Wasserkessel", "Teekessel", "Kessel"],
    "table": ["Tischchen", "Couchtisch", "Beistelltisch", "Esstisch", "Schreibtisch", "Nachttisch",
              "Wohnzimmertisch", "Küchentisch", "Sofatisch"],
    "chair": ["Sessel", "Hocker", "Bürostuhl", "Stühle"],
    "sofa": ["Couch", "Kanapee", "Sitzecke", "Liege"],
    "bed": ["Doppelbett", "Kinderbett"],
    "rug": ["Teppichboden", "Läufer", "Vorleger", "Matte", "Fußmatte"],
    "pillow": ["Kopfkissen", "Sofakissen", "Zierkissen", "Polster"],
    "blanket": ["Kuscheldecke", "Wolldecke", "Bettdecke", "Plaid"],
    "lamp": ["Leuchte", "Stehlampe", "Tischlampe", "Nachttischlampe", "Leselampe", "Licht"],
    "potted_plant": ["Pflanze", "Blume", "Topfpflanze", "Blumentopf"],
    "book": ["Bücher", "Roman", "Taschenbuch", "Heft", "Zeitschrift", "Magazin"],
    "pen": ["Kugelschreiber", "Kuli", "Bleistift", "Füller", "Filzstift", "Textmarker"],
    "scissors": ["Bastelschere", "Papierschere"],
    "tool": ["Hammer", "Schraubenzieher", "Schraubendreher", "Zange", "Schraubenschlüssel"],
    "parcel": ["Karton", "Päckchen", "Schachtel", "Kiste", "Lieferung", "Sendung"],
    "toy": ["Kuscheltier", "Stofftier", "Teddy", "Puppe", "Spielsachen"],
    "ball": ["Fußball", "Tennisball", "Spielball"],
    "shoes": ["Schuh", "Turnschuhe", "Sneaker", "Hausschuhe", "Pantoffeln", "Stiefel", "Latschen"],
    "helmet": ["Helm", "Fahrradhelme"],
    "trash_can": ["Mülltonne", "Abfalleimer", "Papierkorb", "Eimer", "Müll"],
}

# Dativ-Formen für Lagebeziehungen („vermutlich auf dem Tisch“)
SURFACE_LABELS = ("table", "sofa", "bed", "chair", "rug")
_DATIVE = {"table": "dem Tisch", "sofa": "dem Sofa", "bed": "dem Bett", "chair": "dem Stuhl", "rug": "dem Teppich"}

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Füllwörter in Fragen („Wo ist meine Fernbedienung?“)
_FILLERS = {
    "wo", "ist", "sind", "liegt", "liegen", "steht", "stehen", "habe", "hab", "hat", "ich", "wir", "man",
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
    "mein", "meine", "meinen", "meinem", "meiner", "meines", "unser", "unsere", "unseren", "unserem", "unserer",
    "dein", "deine", "sein", "seine", "ihr", "ihre",
    "gerade", "denn", "nur", "bitte", "noch", "eigentlich", "zuletzt", "gesehen", "finde", "find", "suche",
    "such", "gefunden", "hingelegt", "gelegt", "gelassen", "verlegt", "kann", "nicht", "mehr", "nochmal",
    "where", "is", "are", "my", "the", "a", "an",
}


def normalize(text: str) -> str:
    """Kleinschreibung, ä→ae/ö→oe/ü→ue/ß→ss, Akzente weg, Satzzeichen → Leerzeichen."""
    s = (text or "").lower().translate(_UMLAUTS)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def strip_fillers(norm: str) -> str:
    words = [w for w in norm.split() if w not in _FILLERS]
    return " ".join(words) if words else norm


def stem(word: str) -> str:
    """Grobe deutsche Pluralreduktion (nur für den Vergleich, nicht für die Anzeige)."""
    w = word
    if len(w) > 5 and w.endswith("ern"):
        return w[:-3]
    for suf in ("en", "er", "n", "e", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def stem_phrase(norm: str) -> str:
    return " ".join(stem(w) for w in norm.split())


def vocab_map(vocab: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["label"]: e for e in vocab if isinstance(e, dict) and e.get("label")}


def display_name(label: str, vocab: Optional[dict[str, dict[str, Any]]] = None) -> str:
    e = (vocab or {}).get(label)
    if e and e.get("name_de"):
        return str(e["name_de"])
    return label.replace("_", " ")


def synonyms_for(label: str, custom: Optional[dict[str, list[str]]] = None) -> list[str]:
    out: list[str] = []
    for s in list(BUILTIN_SYNONYMS.get(label, [])) + list((custom or {}).get(label) or []):
        s = str(s).strip()
        if s and s not in out:
            out.append(s)
    return out


def relation_text(rel_type: str, surface_label: str, surface_name: str) -> str:
    target = _DATIVE.get(surface_label) or f"„{surface_name}“"
    if rel_type == "on":
        return f"vermutlich auf {target}"
    return f"vermutlich neben {target}"


# ---------------------------------------------------------------- Suche


def _terms(label: str, entry: Optional[dict[str, Any]], custom: Optional[dict[str, list[str]]]):
    """(normierter Begriff, via) je Label."""
    out: list[tuple[str, str]] = [(normalize(label.replace("_", " ")), "label")]
    if entry:
        if entry.get("name_de"):
            out.append((normalize(entry["name_de"]), "name"))
        for p in entry.get("prompts") or []:
            out.append((normalize(p), "prompt"))
    for s in synonyms_for(label, custom):
        out.append((normalize(s), "synonym"))
    return [(t, v) for t, v in out if t]


def match_labels(query: str, vocab: list[dict[str, Any]], custom: Optional[dict[str, list[str]]] = None,
                 extra_labels: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Ordnet eine Suchanfrage Vokabular-Labels zu.

    Ergebnis: [{label, score 0..100, via}] absteigend nach score. `extra_labels` = Labels aus der DB, die nicht
    (mehr) im Vokabular stehen.
    """
    norm = strip_fillers(normalize(query))
    if not norm:
        return []
    q_stem = stem_phrase(norm)
    q_tokens = q_stem.split()
    vmap = vocab_map(vocab)
    labels = list(vmap.keys()) + [lb for lb in extra_labels if lb not in vmap]
    best: dict[str, tuple[float, str]] = {}

    def offer(label: str, score: float, via: str) -> None:
        cur = best.get(label)
        if cur is None or score > cur[0]:
            best[label] = (score, via)

    for label in labels:
        for term, via in _terms(label, vmap.get(label), custom):
            t_stem = stem_phrase(term)
            if term == norm or t_stem == q_stem:
                offer(label, 100.0, via)
                continue
            t_tokens = t_stem.split()
            # ganzer Begriff als Wortfolge in der Anfrage („die blaue tasse“) oder umgekehrt
            if len(t_stem) >= 3 and re.search(rf"(^| ){re.escape(t_stem)}( |$)", q_stem):
                offer(label, 92.0 if len(t_tokens) > 1 else 90.0, via)
                continue
            if len(q_stem) >= 4 and re.search(rf"(^| ){re.escape(q_stem)}( |$)", t_stem):
                offer(label, 85.0, via)
                continue
            # Komposita: „autoschluessel“ endet auf „schluessel“ (Grundwort steht hinten)
            if len(t_stem) >= 4 and any(len(qt) > len(t_stem) and qt.endswith(t_stem) for qt in q_tokens):
                offer(label, 84.0, via)
                continue
            # unscharf (Tippfehler): ganze Anfrage und einzelne Wörter
            sc = fuzz.ratio(q_stem, t_stem)
            if len(q_tokens) > 1:
                sc = max(sc, max(fuzz.ratio(qt, t_stem) for qt in q_tokens if len(qt) >= 3) if any(
                    len(qt) >= 3 for qt in q_tokens) else 0)
            if sc >= 80:
                offer(label, round(float(sc) * 0.9, 1), "fuzzy")
    res = [{"label": lb, "score": sc, "via": via} for lb, (sc, via) in best.items()]
    res.sort(key=lambda r: -r["score"])
    if res and res[0]["via"] != "fuzzy" and res[0]["score"] >= 84:
        # direkte Treffer nicht mit Tippfehler-Funden verwässern („Schlüssel“ ≠ „Schüssel“)
        res = [r for r in res if r["via"] != "fuzzy"]
    return res
