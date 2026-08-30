"""Tests for the GUI list models.

Only the parts that hold logic rather than layout: day grouping, message-run
detection, and the peer row's presence mapping. QML rendering is not asserted
on — see TESTING.md.

PySide6 is an optional extra (`pip install muninn[gui]`), so the whole module
skips when it is absent rather than failing a headless install.
"""

import time

import pytest

pytest.importorskip("PySide6", reason="GUI extra not installed")

from muninn.gui.models import (  # noqa: E402
    RUN_GAP_SECONDS,
    MessageListModel,
    _day_section,
    _starts_run,
)

LOCAL = "AA:AA:AA:AA:AA:AA"
PEER = "BB:BB:BB:BB:BB:BB"
DAY = 86400


def role(model, row: int, name: str):
    from PySide6.QtCore import Qt

    for num, raw in model.roleNames().items():
        if raw.decode() == name:
            return model.data(model.index(row), num)
    raise KeyError(name)


# --- Day sections ---


def test_today_and_yesterday_are_named(monkeypatch):
    now = int(time.time())
    assert _day_section(now) == "Today"
    assert _day_section(now - DAY) == "Yesterday"


def test_the_last_week_uses_weekday_names():
    label = _day_section(int(time.time()) - DAY * 3)
    assert label not in ("Today", "Yesterday")
    assert label in (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    )


def test_older_dates_are_explicit():
    assert any(ch.isdigit() for ch in _day_section(int(time.time()) - DAY * 30))


def test_a_previous_year_includes_the_year():
    label = _day_section(int(time.time()) - DAY * 400)
    assert len(label.split()) == 3  # "26 Jul 2025"


# --- Run detection ---


def _msg(sender=PEER, ts=1_700_000_000, day="Today"):
    return {"senderMac": sender, "timestamp": ts, "daySection": day}


def test_the_first_message_always_starts_a_run():
    assert _starts_run(_msg(), None) is True


def test_a_quick_reply_from_the_same_sender_continues_the_run():
    first = _msg(ts=1000)
    assert _starts_run(_msg(ts=1000 + RUN_GAP_SECONDS - 1), first) is False


def test_a_long_pause_starts_a_new_run():
    first = _msg(ts=1000)
    assert _starts_run(_msg(ts=1000 + RUN_GAP_SECONDS + 1), first) is True


def test_a_different_sender_starts_a_new_run():
    assert _starts_run(_msg(sender=LOCAL), _msg(sender=PEER)) is True


def test_crossing_midnight_starts_a_new_run():
    # Otherwise the first message of a day sits under a divider with no name.
    assert _starts_run(_msg(day="Today"), _msg(day="Yesterday")) is True


# --- Model integration ---


def test_appended_messages_carry_a_day_and_run_flag():
    model = MessageListModel()
    now = int(time.time())
    model.add_message("a" * 32, PEER, "bob", "first", now, is_outbound=False)
    model.add_message("b" * 32, PEER, "bob", "second", now + 5, is_outbound=False)

    assert role(model, 0, "daySection") == "Today"
    assert role(model, 0, "showSender") is True
    assert role(model, 1, "showSender") is False, "a burst repeated the name"


def test_an_interleaved_sender_restarts_the_run():
    model = MessageListModel()
    now = int(time.time())
    model.add_message("a" * 32, PEER, "bob", "hi", now, is_outbound=False)
    model.add_message("b" * 32, LOCAL, "me", "hello", now + 1, is_outbound=True)
    model.add_message("c" * 32, PEER, "bob", "back", now + 2, is_outbound=False)
    assert [role(model, i, "showSender") for i in range(3)] == [True, True, True]


def test_loaded_history_is_grouped_the_same_way_as_live_messages():
    """The append and reload paths must not disagree, or reopening a
    conversation silently regroups it."""
    now = int(time.time())
    rows = [
        (bytes([1]) * 16, PEER, "one", now, "read"),
        (bytes([2]) * 16, PEER, "two", now + 5, "read"),
        (bytes([3]) * 16, LOCAL, "three", now + 10, "sent"),
    ]
    loaded = MessageListModel()
    loaded.load_history(rows, LOCAL, lambda a: "bob" if a == PEER else "me")

    live = MessageListModel()
    for mid, sender, body, ts, _ack in rows:
        live.add_message(mid.hex(), sender, "bob", body, ts, sender == LOCAL)

    assert [role(loaded, i, "showSender") for i in range(3)] == [True, False, True]
    assert [role(loaded, i, "showSender") for i in range(3)] == [
        role(live, i, "showSender") for i in range(3)
    ]
    assert [role(loaded, i, "daySection") for i in range(3)] == ["Today"] * 3


def test_outbound_messages_are_flagged_from_the_local_mac():
    now = int(time.time())
    model = MessageListModel()
    model.load_history(
        [(bytes([9]) * 16, LOCAL, "mine", now, "sent")], LOCAL, lambda a: a
    )
    assert role(model, 0, "isOutbound") is True
