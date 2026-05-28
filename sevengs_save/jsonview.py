"""Convert a SaveFile's in-memory tree to viewer-friendly JSON.

Plain int/float/str/bool/None go through unwrapped. Anything that JSON
can't natively represent — tuple, set, frozenset, bytes, Stub instances,
dicts with non-string keys — gets wrapped as `{"t": "...", "v": ...}` so
the frontend can render type-aware badges and reconstruct the right shape.

Cycles and pathologically deep graphs are handled — pickle preserves object
identity through its memo, so any cyclic Stub graph in the original save
(seen at higher game levels in records like `peers` / `graveyard`) is
faithfully rebuilt as a cycle in memory. We detect cycles via an ancestor
set of `id()`s and cap recursion depth as a safety net.
"""

from typing import Any

from .savefile import SAVER_NAMES, SAVER_ORDER, SaveFile
from .stubs import Stub

MAX_STR_PREVIEW = 4096  # safety cap; saves shouldn't have huge strings
MAX_DEPTH = 200  # safety net against runaway graphs that aren't true cycles


def encode(obj: Any) -> Any:
    """Recursively convert obj to a JSON-serialisable structure."""
    return _encode(obj, set(), 0)


def _encode(obj: Any, ancestors: set[int], depth: int) -> Any:
    if obj is None:
        return {"t": "null"}
    if isinstance(obj, bool):
        return {"t": "bool", "v": obj}
    if isinstance(obj, int):
        return {"t": "int", "v": obj}
    if isinstance(obj, float):
        return {"t": "float", "v": obj}
    if isinstance(obj, str):
        truncated = len(obj) > MAX_STR_PREVIEW
        return {"t": "str", "v": obj[:MAX_STR_PREVIEW], "len": len(obj),
                **({"truncated": True} if truncated else {})}
    if isinstance(obj, bytes):
        return {"t": "bytes", "v": obj[:MAX_STR_PREVIEW].hex(), "len": len(obj),
                **({"truncated": True} if len(obj) > MAX_STR_PREVIEW else {})}

    # Containers and Stubs — these are the types where cycles are possible.
    # Check the ancestor path: if this object is already being encoded
    # higher up the stack, emit a sentinel instead of recursing into it
    # again. Note: re-encountering the same object via a *different* path
    # (shared, not cyclic) is fine — we add to ancestors on entry and
    # remove on exit, so siblings don't trip the check.
    oid = id(obj)
    if oid in ancestors:
        return {"t": "cycle", "cls": _typename(obj)}
    if depth >= MAX_DEPTH:
        return {"t": "deep", "cls": _typename(obj), "depth": depth}

    ancestors.add(oid)
    try:
        if isinstance(obj, dict):
            items = []
            non_string_keys = False
            for k, v in obj.items():
                if not isinstance(k, str):
                    non_string_keys = True
                items.append([
                    _encode(k, ancestors, depth + 1) if non_string_keys else str(k),
                    _encode(v, ancestors, depth + 1),
                ])
            if non_string_keys:
                return {"t": "dict_kv", "v": items}
            return {"t": "dict", "v": {k: ev for k, ev in items}}
        if isinstance(obj, tuple):
            return {"t": "tuple", "v": [_encode(x, ancestors, depth + 1) for x in obj]}
        if isinstance(obj, list):
            return {"t": "list", "v": [_encode(x, ancestors, depth + 1) for x in obj]}
        if isinstance(obj, (set, frozenset)):
            return {"t": "set", "v": [_encode(x, ancestors, depth + 1) for x in obj]}
        if isinstance(obj, Stub):
            cls = f"{type(obj).__module__}.{type(obj).__name__}"
            attrs = {k: _encode(v, ancestors, depth + 1)
                     for k, v in obj.__dict__.items()
                     if not k.startswith("__")}
            return {"t": "obj", "cls": cls, "v": attrs}
        # Unknown: fall back to repr.
        return {"t": "repr", "cls": type(obj).__name__, "v": repr(obj)}
    finally:
        ancestors.discard(oid)


def _typename(obj: Any) -> str:
    t = type(obj)
    mod = getattr(t, "__module__", "")
    name = getattr(t, "__name__", repr(t))
    return f"{mod}.{name}" if mod and mod not in ("builtins", "__builtin__") else name


def encode_savefile(sf: SaveFile, source_path: str) -> dict:
    """Encode a SaveFile as the payload returned by /api/save."""
    records = []
    for idx, rec in enumerate(sf.records):
        sid = SAVER_ORDER[idx]
        records.append({
            "id": sid,
            "name": SAVER_NAMES[sid],
            "data": encode(rec),
        })
    return {"source": source_path, "records": records}
