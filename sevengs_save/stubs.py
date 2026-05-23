"""Stub classes for unpickling 7 Grand Steps save files in Python 3.

The game is Python 2.6. Save records reference classes from modules we don't
have (goals, people, soap, peers, pieces, etc.). We don't need the real
classes — only something pickle can construct and populate, and that the
re-pickler can serialize back with the original module/name so the game can
restore it later.
"""

import pickle
import sys
import types


_class_cache: dict[tuple[str, str], type] = {}
_fake_modules: dict[str, types.ModuleType] = {}


def _register_in_fake_module(klass: type) -> None:
    """Make a stub class re-importable so pickle.dump's class lookup passes.

    The game's modules (goals, people, soap, ...) don't exist in our Python
    3 environment. Pickling a stub instance triggers `pickle.save_type`,
    which does `getattr(import_module(modname), name)` to verify the class
    is reachable. We satisfy that check by installing a synthetic module
    object into `sys.modules` and hanging the stub class off it.
    """
    mod_name = klass.__module__
    mod = _fake_modules.get(mod_name)
    if mod is None and mod_name not in sys.modules:
        mod = types.ModuleType(mod_name)
        mod.__sevengs_stub__ = True  # marker so we know we own it
        _fake_modules[mod_name] = mod
        sys.modules[mod_name] = mod
    elif mod is None:
        # A real module with this name already exists; don't shadow it.
        mod = sys.modules[mod_name]
    setattr(mod, klass.__name__, klass)


class Stub:
    """Pickle-compatible stand-in for any game class.

    - `__init__` accepts whatever args the original constructor was given (we
      ignore them; pickle's BUILD opcode sets attributes via `__setstate__`
      or `__dict__` directly after construction).
    - `__reduce_ex__` writes back the original (module, name) so the game's
      Python 2.6 unpickler sees the right class on load.
    """

    def __init__(self, *args, **kwargs):
        # Stash construction args for the rare case __setstate__ isn't called.
        if args or kwargs:
            self.__dict__["__init_args__"] = (args, kwargs)

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, tuple) and len(state) == 2 and isinstance(state[0], dict):
            # (dict_state, slot_state) — we only have __dict__, no slots
            self.__dict__.update(state[0])
        else:
            self.__dict__["__state__"] = state

    def __getstate__(self):
        # Drop the synthetic key we may have added in __init__.
        d = {k: v for k, v in self.__dict__.items() if k != "__init_args__"}
        return d

    def __reduce_ex__(self, protocol):
        # Mirror what the original objects did: (cls, ()) + state via BUILD.
        return (self.__class__, (), self.__getstate__())

    def __repr__(self):
        mod = type(self).__module__
        name = type(self).__name__
        payload = {k: v for k, v in self.__dict__.items() if not k.startswith("__")}
        return f"<{mod}.{name} {payload!r}>"


def make_stub(module: str, name: str) -> type:
    """Return (and cache) a Stub subclass that pickles back as module.name."""
    key = (module, name)
    klass = _class_cache.get(key)
    if klass is None:
        klass = type(name, (Stub,), {})
        klass.__module__ = module
        klass.__qualname__ = name
        _class_cache[key] = klass
        _register_in_fake_module(klass)
    return klass


class StubUnpickler(pickle.Unpickler):
    """Unpickler that substitutes Stub subclasses for unknown game classes."""

    # Modules we never want to resolve to real Python 3 classes even if they
    # happen to import — the game's versions are semantically different.
    _force_stub_modules = frozenset({
        "goals", "people", "soap", "peers", "pieces", "spaces",
        "ages", "tales", "tutorial", "wheel", "graveyard", "family",
        "familyPC", "scoring", "tokens", "tokTray", "features",
        "extraFeatures", "info", "rite",
    })

    def find_class(self, module, name):
        if module == "__builtin__":
            module = "builtins"
        elif module == "copy_reg":
            module = "copyreg"
        if module in self._force_stub_modules:
            return make_stub(module, name)
        try:
            return super().find_class(module, name)
        except (ImportError, AttributeError):
            return make_stub(module, name)
