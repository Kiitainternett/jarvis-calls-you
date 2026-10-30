"""Tools the AI can use during a call.

Built-ins: hang_up_call, get_current_time, take_note, schedule_callback.
Your own tools: drop a .py file in ./tools and decorate functions with @tool, e.g.

    from callme.tools import tool

    @tool("Check if my website is up", url={"type": "string", "description": "page to check"})
    def check_site(url: str) -> str:
        ...

Tools run in a worker thread, so slow ones never freeze the call audio.
"""
from __future__ import annotations

import importlib.util
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

REGISTRY: dict[str, dict] = {}


def tool(description: str, name: str | None = None, required: list | None = None, **params):
    """Register a function as a call tool. params = JSON-schema properties."""
    def deco(fn: Callable):
        n = name or fn.__name__
        REGISTRY[n] = {
            "decl": {"name": n, "description": description,
                     "parameters": {"type": "object", "properties": params,
                                    **({"required": required} if required else {})}},
            "fn": fn,
        }
        return fn
    return deco


def load_plugins(folder: Path) -> list[str]:
    loaded = []
    if not folder.exists():
        return loaded
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"callme_plugin_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            loaded.append(path.stem)
        except Exception:  # noqa: BLE001 — a broken plugin must not kill the service
            logging.exception("plugin %s failed to load", path.name)
    return loaded


# Built-ins that need the live call (hang up, notes, callbacks) are handled by the bridge.
HANG_UP = {"name": "hang_up_call",
           "description": "End the phone call. Call this only after you have said goodbye.",
           "parameters": {"type": "object", "properties": {}}}
TAKE_NOTE = {"name": "take_note",
             "description": "Save a note or to-do the user asked you to remember. It is sent to the user as a "
                            "Telegram message after the call.",
             "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}
CALLBACK = {"name": "schedule_callback",
            "description": "Schedule another phone call to the user later, e.g. 'call me back in 20 minutes' or "
                           "'wake me up at 6:30'.",
            "parameters": {"type": "object", "properties": {
                "when": {"type": "string", "description": "e.g. 'in 20 minutes' or '06:30'"},
                "reason": {"type": "string", "description": "what the call is about"}},
                "required": ["when", "reason"]}}


@tool("Get the current local date and time.")
def get_current_time() -> str:
    return datetime.now().strftime("%A %d %B %Y, %H:%M")


def declarations() -> list[dict]:
    return [HANG_UP, TAKE_NOTE, CALLBACK] + [t["decl"] for t in REGISTRY.values()]


def run(name: str, args: dict) -> str:
    t = REGISTRY.get(name)
    if not t:
        return f"Unknown tool {name}"
    try:
        return str(t["fn"](**args))[:4000]
    except Exception as e:  # noqa: BLE001
        logging.exception("tool %s failed", name)
        return f"Tool error: {e}"
