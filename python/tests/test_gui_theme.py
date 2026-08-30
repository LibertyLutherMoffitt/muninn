"""Guards on the QML layer that do not need a running Qt scene.

These are cheap static checks for the two classes of mistake that actually
happened while building this UI, both of which failed *silently* — no QML
warning, just the wrong pixels:

  * a colour literal in a .qml file, which cannot be restyled and drifts from
    whatever the rest of the app does;
  * a property named `state`, which collides with the built-in Item.state and
    binds the wrong value.
"""

import re
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[1] / "src" / "muninn" / "gui"
QML_DIR = GUI / "qml"
QML_FILES = sorted(QML_DIR.glob("*.qml"))

# Hex colours written directly in QML. "transparent" and "white" are fine.
HEX_COLOUR = re.compile(r'"#[0-9a-fA-F]{3,8}"')

# Reserved on Item; shadowing it is legal but silently wrong.
RESERVED_PROPERTIES = ("state",)


def theme_keys() -> set[str]:
    """Keys of the _THEME dict in gui/main.py, read without importing Qt."""
    src = (GUI / "main.py").read_text()
    body = src.split("_THEME = {", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r'"(\w+)":', body))


def test_there_are_qml_files_to_check():
    assert QML_FILES, "no QML found — the glob or the layout changed"


@pytest.mark.parametrize("qml", QML_FILES, ids=lambda p: p.name)
def test_no_hardcoded_colours(qml):
    offenders = [
        f"{qml.name}:{n}: {line.strip()}"
        for n, line in enumerate(qml.read_text().splitlines(), 1)
        if HEX_COLOUR.search(line)
    ]
    assert not offenders, "use a Theme token instead:\n" + "\n".join(offenders)


@pytest.mark.parametrize("qml", QML_FILES, ids=lambda p: p.name)
def test_no_property_shadows_a_reserved_item_property(qml):
    pattern = re.compile(
        r"^\s*(?:readonly\s+)?property\s+\w+\s+(" + "|".join(RESERVED_PROPERTIES) + r")\b"
    )
    offenders = [
        f"{qml.name}:{n}: {line.strip()}"
        for n, line in enumerate(qml.read_text().splitlines(), 1)
        if pattern.match(line)
    ]
    assert not offenders, (
        "this shadows a built-in Item property and binds the wrong value:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("qml", QML_FILES, ids=lambda p: p.name)
def test_every_theme_token_used_actually_exists(qml):
    """A typo'd token evaluates to undefined and renders as transparent."""
    known = theme_keys()
    used = set(re.findall(r"\bTheme\.(\w+)", qml.read_text()))
    missing = sorted(used - known)
    assert not missing, f"{qml.name} uses undefined Theme tokens: {missing}"


def test_the_theme_defines_a_colour_for_every_presence_state():
    """PresenceDot maps presence states to colours; a missing one renders
    as the offline fallback, which is indistinguishable from a real bug."""
    dot = (QML_DIR / "PresenceDot.qml").read_text()
    for state in ("direct", "relay", "unreachable", "nearby", "group"):
        assert f'"{state}"' in dot, f"PresenceDot has no branch for {state!r}"


def test_shared_components_are_actually_shared():
    """Extracting a component only pays off if the call sites use it."""
    users = {
        "PresenceDot": {"PeerList.qml", "ChatView.qml"},
        "PillButton": {"Main.qml"},
        "EmptyState": {"ChatView.qml", "PeerList.qml"},
    }
    for component, expected in users.items():
        found = {
            q.name
            for q in QML_FILES
            if q.name != f"{component}.qml" and re.search(rf"\b{component}\s*\{{", q.read_text())
        }
        assert expected <= found, (
            f"{component} is no longer used by {sorted(expected - found)}"
        )
