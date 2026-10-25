"""Auswertung der Regel-Bedingungen (`rules.conditions`) – Gerüst aus K1, Umsetzung: A1.

Die Bedingungs-*Typen* und ihre Schemas sind in `rules.py` registriert (`rules.CONDITIONS`). Dieses Modul reicht
die Evaluatoren nach:

    rules.register_condition("inventory", CondInventory, evaluate=_inventory)

Signatur: `evaluate(cond: dict, ctx: rules.CondContext) -> rules.CondResult`. Ohne Evaluator gilt eine Bedingung
als NICHT erfüllt. Das Modul wird beim Start von `main.py` importiert.
"""
from __future__ import annotations

import logging

log = logging.getLogger("vg.rule_conditions")

# A1: Evaluatoren für inventory, inventory_untouched, weather, track_came_from hier registrieren.
