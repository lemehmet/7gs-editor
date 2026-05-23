"""Load / mutate / write 7 Grand Steps `.fam` / `.rsv` save files.

A save file is a concatenation of 12 cPickle protocol-2 records, one per
"saver" registered by the game in saveGame.AddSaver(id, ...). The records
are written in sorted-by-id order; we expose them by friendly name.
"""

import io
import pickle
from pathlib import Path
from typing import Any

from . import remote
from .stubs import StubUnpickler
from .writer import dump_py2_compat

# id -> module-name, in the order RestoreGame reads them.
SAVER_NAMES: dict[int, str] = {
    1: "ages",      2: "family",   3: "spaces",   4: "wheel",
    5: "people",    6: "scoring",  7: "pieces",   8: "tales",
    9: "tutorial", 13: "familyPC", 14: "peers",  15: "graveyard",
}
SAVER_ORDER: list[int] = sorted(SAVER_NAMES)
SAVER_ID_BY_NAME: dict[str, int] = {v: k for k, v in SAVER_NAMES.items()}


class SaveFile:
    """In-memory view of a 7 Grand Steps save.

    Records are exposed both as a list (`self.records`) preserving on-disk
    order, and by name (`self["family"]`, `self.get("familyPC")`).
    """

    def __init__(self, records: list[Any], path: str | None = None):
        if len(records) != len(SAVER_ORDER):
            raise ValueError(
                f"expected {len(SAVER_ORDER)} records, got {len(records)}"
            )
        self.records = records
        self.path = path

    # ---- record access ---------------------------------------------------

    def __getitem__(self, key: str | int) -> Any:
        return self.records[self._index(key)]

    def __setitem__(self, key: str | int, value: Any) -> None:
        self.records[self._index(key)] = value

    def get(self, key: str | int) -> Any:
        return self[key]

    @staticmethod
    def _index(key: str | int) -> int:
        if isinstance(key, str):
            if key not in SAVER_ID_BY_NAME:
                raise KeyError(f"unknown saver name {key!r}")
            return SAVER_ORDER.index(SAVER_ID_BY_NAME[key])
        if isinstance(key, int):
            if key not in SAVER_NAMES:
                raise KeyError(f"unknown saver id {key}")
            return SAVER_ORDER.index(key)
        raise TypeError(f"key must be str or int, not {type(key).__name__}")

    # ---- dotted-path get/set --------------------------------------------

    def lookup(self, path: str) -> Any:
        """Resolve a dotted path like 'family.theFamily.scoreTotal'.

        First segment is the record name; subsequent segments index dicts by
        key, lists/tuples by integer, or objects by attribute. Numeric
        segments are tried as int indices first, then as dict keys.
        """
        head, *rest = path.split(".")
        obj = self[head]
        for seg in rest:
            obj = _step(obj, seg)
        return obj

    def assign(self, path: str, value: Any) -> Any:
        """Set the value at a dotted path. Returns the previous value."""
        head, *rest = path.split(".")
        if not rest:
            old = self[head]
            self[head] = value
            return old
        parent = self[head]
        for seg in rest[:-1]:
            parent = _step(parent, seg)
        last = rest[-1]
        old = _step(parent, last)
        _set(parent, last, value)
        return old

    # ---- I/O ------------------------------------------------------------

    def write(self, path: Path | str, *, backup: bool = True) -> str | None:
        """Write the save back. `path` may be local or `[user@]host:remote`.
        Returns the backup path if one was created, else None.
        """
        path_str = str(path)
        buf = io.BytesIO()
        for rec in self.records:
            dump_py2_compat(rec, buf)
        return remote.write_bytes(path_str, buf.getvalue(), backup=backup)


def load(path: Path | str) -> SaveFile:
    """Load a save file from a local path or `[user@]host:remote/path`."""
    path_str = str(path)
    data = remote.read_bytes(path_str)
    bio = io.BytesIO(data)
    records = []
    while bio.tell() < len(data):
        up = StubUnpickler(bio, encoding="latin-1")
        records.append(up.load())
    return SaveFile(records, path=path_str)


# ---- internals ----------------------------------------------------------

def _step(obj: Any, seg: str) -> Any:
    """Walk one segment of a dotted path into obj."""
    if isinstance(obj, dict):
        if seg in obj:
            return obj[seg]
        # Numeric key fallback for dicts with int keys
        if seg.lstrip("-").isdigit() and int(seg) in obj:
            return obj[int(seg)]
        raise KeyError(f"dict has no key {seg!r}; keys: {sorted(map(str, obj.keys()))[:10]}")
    if isinstance(obj, (list, tuple)):
        try:
            return obj[int(seg)]
        except ValueError:
            raise TypeError(f"cannot index {type(obj).__name__} with non-int {seg!r}")
    # object: attribute access (Stub or anything else)
    if hasattr(obj, "__dict__") and seg in obj.__dict__:
        return obj.__dict__[seg]
    if hasattr(obj, seg):
        return getattr(obj, seg)
    raise AttributeError(f"{type(obj).__name__} has no member {seg!r}")


def _set(obj: Any, seg: str, value: Any) -> None:
    if isinstance(obj, dict):
        if seg in obj:
            obj[seg] = value
            return
        if seg.lstrip("-").isdigit() and int(seg) in obj:
            obj[int(seg)] = value
            return
        # Allow creating new dict keys explicitly.
        obj[seg] = value
        return
    if isinstance(obj, list):
        obj[int(seg)] = value
        return
    if isinstance(obj, tuple):
        raise TypeError(f"cannot assign into tuple at {seg!r}; tuples are immutable")
    # object
    if hasattr(obj, "__dict__"):
        obj.__dict__[seg] = value
        return
    setattr(obj, seg, value)
