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


# ---- children (single-record list under familyPC) ----------------------
#
# Unlike head/mate, children are saved only in record 13 (familyPC.children).
# `family.theFamily.children` is rebuilt at restore time from this list
# (familyPC.py:370), so editing the familyPC copy is sufficient — no sync.

def _child_dict(sf: SaveFile, index: int) -> dict:
    children = sf["familyPC"]["children"]
    if not 0 <= index < len(children):
        raise ValueError(f"child index {index} out of range (have {len(children)})")
    c = children[index]
    if not isinstance(c, dict):
        raise TypeError(f"children[{index}] is {type(c).__name__}, expected dict")
    return c


def list_children_skills(sf: SaveFile, index: int) -> list[tuple[str, int]]:
    return _skills_as_pairs(_child_dict(sf, index)["skills"])


def set_child_skill(sf: SaveFile, index: int, symbol: str, value: int) -> int | None:
    """Set one skill on familyPC.children[index]. Adds a new entry if missing.

    Returns the previous value, or None if newly added.
    """
    return _replace_in_skills(_child_dict(sf, index), symbol, value, allow_add=True)


def remove_child_skill(sf: SaveFile, index: int, symbol: str) -> bool:
    return _remove_from_skills(_child_dict(sf, index), symbol)


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


# ---- situation-tag inputs (family.traits / traitPwrs, ages.*Traits) ----
#
# When a heroic challenge fires (goals.*.Reached → tales.StartHeroicChallenge),
# the game builds a "situation" set from runtime + saved state via
# tales.FigurePersonWords. The fields below feed that set, and they're what
# every tale's REQS/NEXT filter against. Editing them lets you steer which
# tales fit before the next in-game tick. Mapping (FigurePersonWords):
#   family.traits        → tags as-is
#   family.traitPwrs     → each entry contributes its uppercase tag
#   family.powers        → tags derived from each power (already covered by
#                          add_power / remove_power)
#   ages.chptTraits/ageTraits/gameTraits  → tags as-is

def list_traits(sf: SaveFile) -> list[str]:
    return sorted(sf["family"]["theFamily"]["traits"])


def add_trait(sf: SaveFile, trait: str) -> bool:
    """Add a tag to family.theFamily.traits. Returns True if newly added."""
    return _bag_add(sf["family"]["theFamily"]["traits"], trait)


def remove_trait(sf: SaveFile, trait: str) -> bool:
    return _bag_remove(sf["family"]["theFamily"]["traits"], trait)


def list_trait_pwrs(sf: SaveFile) -> list[str]:
    return list(sf["family"]["theFamily"]["traitPwrs"])


def add_trait_pwr(sf: SaveFile, pwr: str) -> bool:
    """Append to family.theFamily.traitPwrs (skipped if already present).

    Each entry contributes its uppercase form to the heroic-tale situation
    set, so e.g. 'commander' → 'COMMANDER' becomes available as a REQ.
    """
    tp = sf["family"]["theFamily"]["traitPwrs"]
    if pwr in tp:
        return False
    tp.append(pwr)
    return True


def remove_trait_pwr(sf: SaveFile, pwr: str) -> bool:
    tp = sf["family"]["theFamily"]["traitPwrs"]
    if pwr not in tp:
        return False
    tp.remove(pwr)
    return True


_AGE_SCOPES = ("chpt", "age", "game")


def _age_scope_field(scope: str) -> str:
    if scope == "chpt":
        return "chptTraits"
    if scope == "age":
        return "ageTraits"
    if scope == "game":
        return "gameTraits"
    raise ValueError(f"scope must be one of {_AGE_SCOPES}, not {scope!r}")


def list_age_traits(sf: SaveFile, scope: str) -> list[str]:
    return sorted(sf["ages"][_age_scope_field(scope)])


def add_age_trait(sf: SaveFile, trait: str, scope: str) -> bool:
    return _bag_add(sf["ages"][_age_scope_field(scope)], trait)


def remove_age_trait(sf: SaveFile, trait: str, scope: str) -> bool:
    return _bag_remove(sf["ages"][_age_scope_field(scope)], trait)


# ---- tales.heroicsTold (4th element of the tales 5-tuple) --------------
#
# Marking a heroic tale ID here makes StartHeroicChallenge's *no-arg* scan
# skip it. (When Reached() calls StartHeroicChallenge with a specific tale
# ID — the chapter-entry "challengeRule3" path — heroicsTold is NOT
# consulted, so this isn't a complete bypass for the CotA challenge; see
# prep_ruling_class for that workaround.)

def list_heroics_told(sf: SaveFile) -> list[str]:
    return list(sf["tales"][3])


def mark_heroic_told(sf: SaveFile, tale_id: str) -> bool:
    ht = sf["tales"][3]
    if tale_id in ht:
        return False
    ht.append(tale_id)
    return True


def unmark_heroic_told(sf: SaveFile, tale_id: str) -> bool:
    ht = sf["tales"][3]
    if tale_id not in ht:
        return False
    ht.remove(tale_id)
    return True


def _bag_add(bag, item) -> bool:
    """Add item to a set-or-list bag. Returns True if newly added."""
    if isinstance(bag, set):
        if item in bag:
            return False
        bag.add(item)
        return True
    if item in bag:
        return False
    bag.append(item)
    return True


def _bag_remove(bag, item) -> bool:
    if item not in bag:
        return False
    if isinstance(bag, set):
        bag.discard(item)
    else:
        bag.remove(item)
    return True


# ---- presets -----------------------------------------------------------

_AGE_CHALLENGE_DIGIT = {"Copper": "3", "Bronze": "4", "Iron": "5"}


def prep_ruling_class(sf: SaveFile, *,
                      age: str | None = None,
                      trait_pwr: str = "commander",
                      bump_skill_to: int = 80,
                      add_tokens: int = 6,
                      mark_challenge_told: bool = True,
                      grant_legend: bool = True) -> dict:
    """Pre-load Ruling-class state so post-CotA play is more reliable.

    Designed for use after pushing a save into the Ruling chapter so that the
    next in-game heroic challenge (and the rest of the chapter's tales) has
    enough tags in its situation set to fit. Each step is opt-out via the
    keyword args.

      - `trait_pwr`: appended to family.traitPwrs; its uppercase form lands
        in the situation set, so e.g. 'commander' → 'COMMANDER' (unlocks the
        'commander' tale branch's filtered NEXT chains).
      - `mark_challenge_told`: marks 'challengeRule<digit>' in tales.heroicsTold
        for the inferred or supplied `age`. The chapter-entry challenge that
        Reached() fires uses an explicit tale ID and bypasses heroicsTold,
        but downstream NewAge-style scans do honour it, so this still helps
        avoid re-triggering the same CotA tale on a second pass.
      - `bump_skill_to`: head's `<age>R*` skills bumped up to at least this
        value (won't lower existing higher values).
      - `add_tokens`: drops N copies of `<age>R0` into familyPC.tempTokens.
      - `grant_legend`: ensures ('U', 'rulingCaste') is in family.legends if
        it isn't already.

    If `age` is None, infers from `ages.theAge` in the save.
    """
    if age is None:
        age = sf["ages"].get("theAge") if isinstance(sf["ages"], dict) else None
    if not age:
        raise ValueError("could not infer age from save; pass age= explicitly")
    if age not in _AGE_CHALLENGE_DIGIT:
        raise ValueError(f"unknown age {age!r}; expected one of {tuple(_AGE_CHALLENGE_DIGIT)}")

    result: dict = {"age": age, "changes": []}
    log = result["changes"].append

    if add_trait_pwr(sf, trait_pwr):
        log(f"family.traitPwrs += {trait_pwr!r}")

    if mark_challenge_told:
        tid = "challengeRule" + _AGE_CHALLENGE_DIGIT[age]
        if mark_heroic_told(sf, tid):
            log(f"tales.heroicsTold += {tid!r}")

    if grant_legend:
        fam = sf["family"]["theFamily"]
        legends = fam.setdefault("legends", [])
        if not any(isinstance(leg, tuple) and leg[-1] == "rulingCaste" for leg in legends):
            legends.append(("U", "rulingCaste"))
            log("family.legends += ('U', 'rulingCaste')")

    if bump_skill_to > 0:
        prefix = age + "R"
        for sym, val in list_skills(sf, "head"):
            if sym.startswith(prefix) and val < bump_skill_to:
                set_skill(sf, "head", sym, bump_skill_to)
                log(f"head.skills[{sym}] {val} -> {bump_skill_to}")

    if add_tokens > 0:
        sym = age + "R0"
        tt = sf["familyPC"]["tempTokens"]
        existing = sum(1 for t in tt if t == sym)
        need = max(0, add_tokens - existing)
        if need > 0:
            new_len = add_temp_token(sf, sym, need)
            log(f"familyPC.tempTokens += {sym}x{need} (have {existing}, target {add_tokens}, len now {new_len})")

    return result


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
