"""Just enough of PySide6's QObject/Signal/Property/Slot for code that is
shared with the terminal client, which must run without Qt (gui/vim.py)."""

from __future__ import annotations


class _Bound:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, fn) -> None:
        self._slots.append(fn)

    def disconnect(self, fn=None) -> None:
        self._slots = [] if fn is None else [s for s in self._slots if s != fn]

    def emit(self, *args) -> None:
        for fn in list(self._slots):
            fn(*args)


class Signal:
    def __init__(self, *types, **kw) -> None:
        self._name = ""

    def __set_name__(self, owner, name: str) -> None:
        self._name = "_signal_" + name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        bound = obj.__dict__.get(self._name)
        if bound is None:
            bound = obj.__dict__[self._name] = _Bound()
        return bound


def Property(*_types, **_kw):  # noqa: N802 — mirrors the Qt name
    return property


def Slot(*_types, **_kw):  # noqa: N802
    return lambda fn: fn


class QObject:
    def __init__(self, parent=None) -> None:
        self._parent = parent
