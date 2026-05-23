"""Convert a SaveFile's in-memory tree to viewer-friendly JSON.

Plain int/float/str/bool/None go through unwrapped. Anything that JSON
can't natively represent — tuple, set, frozenset, bytes, Stub instances,
dicts with non-string keys — gets wrapped as `{"t": "...", "v": ...}` so
the frontend can render type-aware badges and reconstruct the right shape.
"""

from typing import Any

from .savefile import SAVER_NAMES, SAVER_ORDER, SaveFile
from .stubs import Stub

MAX_STR_PREVIEW = 4096  # safety cap; saves shouldn't have huge strings


def encode(obj: Any) -> Any:
    """Recursively convert obj to a JSON-serialisable structure."""
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
    if isinstance(obj, dict):
        items = []
        non_string_keys = False
        for k, v in obj.items():
            if not isinstance(k, str):
                non_string_keys = True
            items.append([encode(k) if non_string_keys else str(k), encode(v)])
        if non_string_keys:
            return {"t": "dict_kv", "v": items}
        return {"t": "dict", "v": {k: ev for k, ev in items}}
    if isinstance(obj, tuple):
        return {"t": "tuple", "v": [encode(x) for x in obj]}
    if isinstance(obj, list):
        return {"t": "list", "v": [encode(x) for x in obj]}
    if isinstance(obj, (set, frozenset)):
        return {"t": "set", "v": [encode(x) for x in obj]}
    if isinstance(obj, Stub):
        cls = f"{type(obj).__module__}.{type(obj).__name__}"
        attrs = {k: encode(v) for k, v in obj.__dict__.items()
                 if not k.startswith("__")}
        return {"t": "obj", "cls": cls, "v": attrs}
    # Unknown: fall back to repr.
    return {"t": "repr", "cls": type(obj).__name__, "v": repr(obj)}


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
