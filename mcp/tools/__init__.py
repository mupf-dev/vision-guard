"""MCP-Tools je Thema. Jedes Modul hat `register(mcp)`; neue Themen hier in MODULES eintragen.

Konvention (CONTRACT.md „Nachträge: Welle 1“, MCP): Datei `tools/<thema>.py`, Helfer aus `tools/common.py`,
Rückgabe `ok(text, structured)` bzw. `fail(meldung)`, Medien-URLs signiert (`signed=1`). Bis W2 nur lesende Tools
(`annotations=READ_ONLY`); ab W2 auch Tools mit Wirkung (`annotations=WRITE`, zuerst `await require_write(ctx)`).
"""

from __future__ import annotations

import importlib

from mcp.server.mcpserver import MCPServer

MODULES = (
    "inventory",       # find_object, list_visible_objects, get_object_history
    "cameras",         # list_cameras, get_camera_snapshot
    "people",          # who_is_where
    "notifications",   # W1 A1: list_notifications
    "changes",         # W1 B2: what_changed
    "health",          # W1 C5: get_camera_health
    "alerts",          # W2 C1: list_alerts, acknowledge_alert (write)
    "review",          # W2 D2: list_open_questions, answer_question (write)
    "pets",            # W2 C3: get_pet_diary
    "reports",         # W2 B3: get_daily_report
    "ask",             # W3 A2: ask_camera
    "sensors",         # W3 A3+C4: list_sensors, get_sensor, get_shopping_list, add_to_shopping_list (write)
    "history",         # W3 B1: search_history
    "ptz",             # PTZ: ptz_status, ptz_move_to (write)
)


def register_all(mcp: MCPServer) -> None:
    for name in MODULES:
        importlib.import_module(f"{__name__}.{name}").register(mcp)
