"""Surgical edits on a SaveFile.

Each function takes a SaveFile and mutates it in place. CLI commands wrap
these; programs using the library can call them directly.
"""

from typing import Iterable

from .savefile import SaveFile


# ---- score / goal ------------------------------------------------------

def get_score(sf: SaveFile) -> tuple[int, int, int, int]:
    """Return (score, scoreTotal, goal.points, goal.needed)."""
    fam = sf["family"]["theFamily"]
    goal = fam["goal"]
    points = _attr(goal, "points")
    needed = _attr(goal, "needed")
    return fam["score"], fam["scoreTotal"], points, needed


def add_points(sf: SaveFile, n: int) -> tuple[int, int]:
    """Add n to scoreTotal and goal.points. Returns (new_points, needed)."""
    fam = sf["family"]["theFamily"]
    fam["scoreTotal"] = fam["scoreTotal"] + n
    fam["score"] = fam["score"] + n
    goal = fam["goal"]
    new_points = _attr(goal, "points") + n
    _set_attr(goal, "points", new_points)
    return new_points, _attr(goal, "needed")


def set_goal_points(sf: SaveFile, value: int) -> int:
    """Set goal.points to value directly. Returns old value.

    Low-level: does NOT touch score / scoreTotal. Use
    `set_goal_points_synced` if you want the three counters to stay
    consistent with what the in-game `family.AddScore` would have done.
    """
    goal = sf["family"]["theFamily"]["goal"]
    old = _attr(goal, "points")
    _set_attr(goal, "points", value)
    return old


def set_goal_points_synced(sf: SaveFile, value: int) -> dict:
    """Set goal.points and apply the same delta to score + scoreTotal.

    Mirrors `family.AddScore(delta)` (family.py:55): each bead award is
    one call that bumps goal.points, the per-generation `score`, and the
    lifetime `scoreTotal` by the same amount. Editing goal.points alone
    leaves the running totals stale; this keeps them aligned.

    Negative deltas pass through unchanged (the user is rolling the save
    state backwards; score/scoreTotal can go negative). Caller can use
    the lower-level `set_goal_points` if they want to skip the sync.

    Returns a dict with old/new values for all three fields.
    """
    fam = sf["family"]["theFamily"]
    goal = fam["goal"]
    old_points = _attr(goal, "points")
    delta = value - old_points

    old_score = fam["score"]
    old_total = fam["scoreTotal"]
    _set_attr(goal, "points", value)
    fam["score"] = old_score + delta
    fam["scoreTotal"] = old_total + delta
    return {
        "delta": delta,
        "goal_points": {"old": old_points, "new": value},
        "score": {"old": old_score, "new": fam["score"]},
        "scoreTotal": {"old": old_total, "new": fam["scoreTotal"]},
        "needed": _attr(goal, "needed"),
    }


def nudge_to_goal(sf: SaveFile, margin: int = 1) -> tuple[int, int]:
    """Set goal.points to (needed - margin) so the next scoring tick triggers
    Goal.Reached() and the game advances. Returns (points, needed)."""
    goal = sf["family"]["theFamily"]["goal"]
    needed = _attr(goal, "needed")
    target = max(0, needed - margin)
    _set_attr(goal, "points", target)
    return target, needed


# ---- head / mate edits (synced across two records) ---------------------
#
# Person.Save() (people.py:95) is called twice per save: once by
# family.Saver to populate `family.theFamily['head']/['mate']`, and again by
# familyPC.Saver to populate `familyPC['head']/['mate'][0]`. Both copies
# must match on disk — the game loads them independently. Every edit on a
# head/mate field must therefore mirror to both dicts.

_WHO = ("head", "mate")


def _person_dicts(sf: SaveFile, who: str) -> list[dict]:
    """Return [family_copy, familyPC_copy] for head or mate.

    Either copy may legitimately be None (no head/mate slotted); we raise
    only if BOTH are missing, since then there's nothing to edit.
    """
    if who not in _WHO:
        raise ValueError(f"who must be one of {_WHO}, not {who!r}")
    fam_copy = sf["family"]["theFamily"].get(who)
    pc_entry = sf["familyPC"].get(who)
    pc_copy = pc_entry[0] if isinstance(pc_entry, tuple) else pc_entry
    found = [d for d in (fam_copy, pc_copy) if isinstance(d, dict)]
    if not found:
        raise ValueError(f"no {who} present in this save")
    return found


def list_skills(sf: SaveFile, who: str = "head") -> list[tuple[str, int]]:
    """Return the (symbol, value) pairs from the canonical familyPC copy."""
    return _skills_as_pairs(_person_dicts(sf, who)[-1]["skills"])


def set_skill(sf: SaveFile, who: str, symbol: str, value: int) -> int | None:
    """Set one skill on head/mate. Adds a new entry if `symbol` isn't present.

    Returns the previous value, or None if newly added. Mirrors to both records.
    """
    old = None
    for person in _person_dicts(sf, who):
        old = _replace_in_skills(person, symbol, value, allow_add=True)
    return old


def remove_skill(sf: SaveFile, who: str, symbol: str) -> bool:
    """Remove a skill entry. Returns True if removed from at least one copy."""
    hit = False
    for person in _person_dicts(sf, who):
        if _remove_from_skills(person, symbol):
            hit = True
    return hit


def boost_skills(sf: SaveFile, who: str = "head", *, by: int | None = None,
                 to: int | None = None, only: Iterable[str] | None = None) -> dict[str, int]:
    """Bulk-update head/mate skills.

    Pass exactly one of `by` (add to each) or `to` (set each to value).
    `only` restricts the change to specific symbols. Mirrors to both records.
    """
    if (by is None) == (to is None):
        raise ValueError("pass exactly one of by= or to=")
    only_set = set(only) if only else None

    def _new(old: int) -> int:
        return to if to is not None else old + by

    changed: dict[str, int] = {}
    persons = _person_dicts(sf, who)
    # Use the first copy as the source-of-truth for the symbol set.
    for sym, val in _skills_as_pairs(persons[0]["skills"]):
        if only_set and sym not in only_set:
            continue
        new_val = _new(val)
        for p in persons:
            _replace_in_skills(p, sym, new_val, allow_add=False)
        changed[sym] = new_val
    return changed


def set_loves_spouse(sf: SaveFile, who: str, value: bool) -> bool:
    """Set lovesSpouse on head/mate. Returns the previous value."""
    persons = _person_dicts(sf, who)
    old = bool(persons[0].get("lovesSpouse", True))
    for p in persons:
        p["lovesSpouse"] = bool(value)
    return old


# ---- familyPC.tempTokens (single-record list) --------------------------

def list_temp_tokens(sf: SaveFile) -> list[str]:
    return list(sf["familyPC"]["tempTokens"])


def add_temp_token(sf: SaveFile, symbol: str, count: int = 1) -> int:
    """Append `count` copies of `symbol` to tempTokens. Returns new length."""
    tt = sf["familyPC"]["tempTokens"]
    for _ in range(count):
        tt.append(symbol)
    return len(tt)


def remove_temp_token(sf: SaveFile, symbol: str, count: int | None = None) -> int:
    """Remove up to `count` occurrences of `symbol` (default: all).
    Returns how many were removed.
    """
    tt = sf["familyPC"]["tempTokens"]
    removed = 0
    limit = count if count is not None else len(tt)
    i = 0
    while i < len(tt) and removed < limit:
        if tt[i] == symbol:
            del tt[i]
            removed += 1
        else:
            i += 1
    return removed


# ---- skill-list helpers ------------------------------------------------
#
# On disk skills are stored as list[(sym, val)]; in the live game they're
# a dict. Tuples vs lists are both seen depending on how the file was
# written. We accept all three shapes and write back as a list of 2-tuples
# (matching what cPickle round-tripped on the original save).

def _skills_as_pairs(skills) -> list[tuple[str, int]]:
    if isinstance(skills, dict):
        return list(skills.items())
    return [(item[0], item[1]) for item in skills]


def _replace_in_skills(person: dict, symbol: str, value: int, *, allow_add: bool):
    """Mutate person['skills'] in place. Returns old value or None."""
    skills = person["skills"]
    if isinstance(skills, dict):
        old = skills.get(symbol)
        if old is None and not allow_add:
            return None
        skills[symbol] = value
        return old
    # list of pairs
    for i, item in enumerate(skills):
        if item[0] == symbol:
            old = item[1]
            skills[i] = (symbol, value)
            return old
    if allow_add:
        skills.append((symbol, value))
    return None


def _remove_from_skills(person: dict, symbol: str) -> bool:
    skills = person["skills"]
    if isinstance(skills, dict):
        if symbol in skills:
            del skills[symbol]
            return True
        return False
    for i, item in enumerate(skills):
        if item[0] == symbol:
            del skills[i]
            return True
    return False


# ---- powers ------------------------------------------------------------

def add_power(sf: SaveFile, tag: str) -> bool:
    """Append a power tag to theFamily.powers (skipped if already present).

    Returns True if added."""
    fam = sf["family"]["theFamily"]
    powers = fam["powers"]
    if tag in powers:
        return False
    powers.append(tag)
    # The game's AddPower() also appends to legends so the power shows in the
    # archive. Mirror that.
    legend_kind = "H" if "HERO" in tag.upper() else "D"
    fam.setdefault("legends", []).append((legend_kind, tag))
    return True


def remove_power(sf: SaveFile, tag: str) -> bool:
    fam = sf["family"]["theFamily"]
    powers = fam["powers"]
    if tag not in powers:
        return False
    powers.remove(tag)
    legends = fam.get("legends") or []
    fam["legends"] = [leg for leg in legends if tag not in leg]
    return True


# ---- helpers ------------------------------------------------------------

def _attr(obj, name):
    if isinstance(obj, dict):
        return obj[name]
    if hasattr(obj, "__dict__") and name in obj.__dict__:
        return obj.__dict__[name]
    return getattr(obj, name)


def _set_attr(obj, name, value):
    if isinstance(obj, dict):
        obj[name] = value
        return
    if hasattr(obj, "__dict__"):
        obj.__dict__[name] = value
        return
    setattr(obj, name, value)
