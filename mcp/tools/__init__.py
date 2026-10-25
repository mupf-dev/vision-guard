"""MCP-Tools je Thema. Jedes Modul hat `register(mcp)`; neue Themen hier in MODULES eintragen.

Konvention (CONTRACT.md „Nachträge: Welle 1“, MCP): Datei `tools/<thema>.py`, Helfer aus `tools/common.py`,
Rückgabe `ok(text, structured)` bzw. `fail(meldung)`, Medien-URLs signiert (`signed=1`). Bis W2 nur lesende Tools
(`annotations=READ_ONLY`).
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
)


def register_all(mcp: MCPServer) -> None:
    for name in MODULES:
        importlib.import_module(f"{__name__}.{name}").register(mcp)
