"""Emoji shortcodes (`:thumbsup:` → 👍), shared by the CLI and GUI.

Offline, no dependencies. Data lives in assets/emoji.json (shared with the
Android app). The GUI picker groups them by category.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DATA = json.loads((Path(__file__).parent / "assets" / "emoji.json").read_text("utf-8"))

CATEGORIES: dict[str, list[tuple[str, str]]] = {
    k: [(c, e) for c, e in v] for k, v in _DATA["categories"].items()
}

SHORTCODES: dict[str, str] = {}
for _items in CATEGORIES.values():
    for _code, _emo in _items:
        SHORTCODES.setdefault(_code, _emo)

# Classic emoticons converted only when they stand alone as a word.
EMOTICONS: dict[str, str] = _DATA["emoticons"]

_CODE_RE = re.compile(r":([a-z0-9_+\-]+):")


def expand(text: str) -> str:
    """Replace :shortcodes: (and stand-alone emoticons) with emoji."""
    text = _CODE_RE.sub(lambda m: SHORTCODES.get(m.group(1), m.group(0)), text)
    words = text.split(" ")
    return " ".join(EMOTICONS.get(w, w) for w in words)


def search(prefix: str, limit: int = 20) -> list[tuple[str, str]]:
    prefix = prefix.lower().strip(":")
    out = [(c, e) for c, e in SHORTCODES.items() if c.startswith(prefix)]
    out += [
        (c, e)
        for c, e in SHORTCODES.items()
        if prefix in c and not c.startswith(prefix)
    ]
    return out[:limit]


QUICK_REACTIONS: list[str] = _DATA["quick"]
