"""Command-line interface for the sevengs_save library.

    python -m sevengs_save dump   savefile.fam
    python -m sevengs_save get    savefile.fam family.theFamily.scoreTotal
    python -m sevengs_save set    savefile.fam family.theFamily.scoreTotal 9999
    python -m sevengs_save score  savefile.fam --add 100
    python -m sevengs_save score  savefile.fam --nudge          # one tick from goal
    python -m sevengs_save skills savefile.fam --who head --to 99
    python -m sevengs_save powers savefile.fam --add personalHero
"""

import argparse
import ast
import sys
from collections import Counter

from . import cheats
from .savefile import SAVER_NAMES, SAVER_ORDER, load


def _summarize(obj, depth=0, max_depth=3, indent="  "):
    pad = indent * depth
    t = type(obj).__name__
    mod = type(obj).__module__
    if depth >= max_depth:
        return f"{pad}<{mod}.{t}>"
    if isinstance(obj, dict):
        lines = [f"{pad}dict({len(obj)} keys):"]
        for k in sorted(obj.keys(), key=lambda x: str(x)):
            v = obj[k]
            vt = type(v).__name__
            if isinstance(v, (int, float, str, bool, type(None))):
                lines.append(f"{pad}{indent}{k!r}: {v!r}")
            elif isinstance(v, (list, tuple, set)):
                lines.append(f"{pad}{indent}{k!r}: {vt}(len={len(v)})")
            elif isinstance(v, dict):
                lines.append(f"{pad}{indent}{k!r}:")
                lines.append(_summarize(v, depth + 2, max_depth, indent))
            else:
                lines.append(f"{pad}{indent}{k!r}: <{type(v).__module__}.{vt}>")
                if hasattr(v, "__dict__") and v.__dict__:
                    lines.append(_summarize(v.__dict__, depth + 2, max_depth, indent))
        return "\n".join(lines)
    if hasattr(obj, "__dict__") and obj.__dict__:
        return f"{pad}<{mod}.{t}>:\n" + _summarize(obj.__dict__, depth + 1, max_depth, indent)
    return f"{pad}{obj!r}"


def cmd_dump(args):
    sf = load(args.file)
    for i, rec in enumerate(sf.records):
        sid = SAVER_ORDER[i]
        print(f"=== record {i} (id={sid} = {SAVER_NAMES[sid]}) ===")
        print(_summarize(rec, max_depth=args.depth))
        print()


def cmd_get(args):
    sf = load(args.file)
    print(repr(sf.lookup(args.path)))


def cmd_set(args):
    sf = load(args.file)
    try:
        value = ast.literal_eval(args.value)
    except (ValueError, SyntaxError):
        value = args.value  # treat as raw string
    old = sf.assign(args.path, value)
    print(f"{args.path}: {old!r} -> {value!r}")
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_score(args):
    sf = load(args.file)
    score, total, points, needed = cheats.get_score(sf)
    print(f"before: score={score} scoreTotal={total} goal.points={points}/{needed}")
    if args.add is not None:
        new_pts, _ = cheats.add_points(sf, args.add)
        print(f"  added {args.add}: goal.points -> {new_pts}/{needed}")
    elif args.set is not None:
        old = cheats.set_goal_points(sf, args.set)
        print(f"  goal.points: {old} -> {args.set}")
    elif args.nudge:
        pts, ndd = cheats.nudge_to_goal(sf, margin=args.margin)
        print(f"  nudged: goal.points -> {pts}/{ndd} (margin {args.margin})")
    else:
        return  # report-only
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_skills(args):
    sf = load(args.file)
    if args.list:
        for sym, val in cheats.list_skills(sf, args.who):
            print(f"  {args.who}.skills[{sym}] = {val}")
        return

    changed: dict[str, int | None] = {}
    if args.set:
        for spec in args.set:
            sym, _, raw = spec.partition("=")
            if not raw:
                raise SystemExit(f"--set expects SYMBOL=N, got {spec!r}")
            old = cheats.set_skill(sf, args.who, sym, int(raw))
            changed[sym] = int(raw)
            arrow = f"{old} -> {raw}" if old is not None else f"(added) {raw}"
            print(f"  {args.who}.skills[{sym}] {arrow}")
    if args.remove:
        for sym in args.remove:
            hit = cheats.remove_skill(sf, args.who, sym)
            print(f"  {args.who}.skills[{sym}] {'removed' if hit else '(not present)'}")
    if args.to is not None or args.by is not None:
        bulk = cheats.boost_skills(
            sf, args.who,
            to=args.to, by=args.by,
            only=args.only,
        )
        for sym, val in bulk.items():
            print(f"  {args.who}.skills[{sym}] -> {val}")
        changed.update(bulk)

    if not changed and args.remove is None:
        # Nothing supplied — show current values as a convenience.
        for sym, val in cheats.list_skills(sf, args.who):
            print(f"  {args.who}.skills[{sym}] = {val}")
        return

    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_loves(args):
    sf = load(args.file)
    if args.toggle:
        # Read either copy (synced), flip it.
        person = sf["family"]["theFamily"].get(args.who)
        if person is None:
            person = sf["familyPC"][args.who][0]
        new_val = not bool(person.get("lovesSpouse", True))
    else:
        new_val = args.set
    old = cheats.set_loves_spouse(sf, args.who, new_val)
    print(f"  {args.who}.lovesSpouse: {old} -> {new_val}")
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


_TOKEN_AGES = ("Copper", "Bronze", "Iron")
_TOKEN_CLASSES = ("L", "T", "N", "R")


def _token_sort_key(sym):
    """Sort key for token names shaped <Age><Class><ID>, by Class, Age, ID.

    Unparseable names sort last, in plain alphabetical order.
    """
    for age_idx, age in enumerate(_TOKEN_AGES):
        if sym.startswith(age):
            rest = sym[len(age):]
            if len(rest) == 2 and rest[0] in _TOKEN_CLASSES and rest[1].isdigit():
                return (0, _TOKEN_CLASSES.index(rest[0]), age_idx, int(rest[1]))
    return (1, sym)


def cmd_tokens(args):
    sf = load(args.file)
    tt = cheats.list_temp_tokens(sf)
    counts = Counter(tt)
    print(f"current tempTokens (len={len(tt)}, unique={len(counts)}):")
    if counts:
        width = max(len(s) for s in counts)
        for sym, n in sorted(counts.items(), key=lambda kv: _token_sort_key(kv[0])):
            print(f"  {sym:<{width}}  {n}")
    changed = False
    if args.add:
        for spec in args.add:
            sym, _, n_raw = spec.partition("x")
            count = int(n_raw) if n_raw else 1
            new_len = cheats.add_temp_token(sf, sym, count)
            print(f"  added {sym} x{count}  (new len={new_len})")
            changed = True
    if args.remove:
        for sym in args.remove:
            removed = cheats.remove_temp_token(sf, sym)
            print(f"  removed {sym} x{removed}")
            changed = changed or removed > 0
    if not changed:
        return
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_traits(args):
    sf = load(args.file)
    print(f"current family.traits: {cheats.list_traits(sf)}")
    changed = False
    if args.add:
        for t in args.add:
            if cheats.add_trait(sf, t):
                print(f"  added: {t}"); changed = True
            else:
                print(f"  already present: {t}")
    if args.remove:
        for t in args.remove:
            if cheats.remove_trait(sf, t):
                print(f"  removed: {t}"); changed = True
            else:
                print(f"  not present: {t}")
    if not changed:
        return
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_tpwrs(args):
    sf = load(args.file)
    print(f"current family.traitPwrs: {cheats.list_trait_pwrs(sf)}")
    changed = False
    if args.add:
        for p in args.add:
            if cheats.add_trait_pwr(sf, p):
                print(f"  added: {p}"); changed = True
            else:
                print(f"  already present: {p}")
    if args.remove:
        for p in args.remove:
            if cheats.remove_trait_pwr(sf, p):
                print(f"  removed: {p}"); changed = True
            else:
                print(f"  not present: {p}")
    if not changed:
        return
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_agetraits(args):
    sf = load(args.file)
    print(f"current ages.{args.scope}Traits: {cheats.list_age_traits(sf, args.scope)}")
    changed = False
    if args.add:
        for t in args.add:
            if cheats.add_age_trait(sf, t, args.scope):
                print(f"  added: {t}"); changed = True
            else:
                print(f"  already present: {t}")
    if args.remove:
        for t in args.remove:
            if cheats.remove_age_trait(sf, t, args.scope):
                print(f"  removed: {t}"); changed = True
            else:
                print(f"  not present: {t}")
    if not changed:
        return
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_heroicstold(args):
    sf = load(args.file)
    print(f"current tales.heroicsTold: {cheats.list_heroics_told(sf)}")
    changed = False
    if args.add:
        for tid in args.add:
            if cheats.mark_heroic_told(sf, tid):
                print(f"  marked told: {tid}"); changed = True
            else:
                print(f"  already told: {tid}")
    if args.remove:
        for tid in args.remove:
            if cheats.unmark_heroic_told(sf, tid):
                print(f"  unmarked: {tid}"); changed = True
            else:
                print(f"  not in list: {tid}")
    if not changed:
        return
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_preset(args):
    sf = load(args.file)
    if args.which == "ruling":
        kwargs = {"trait_pwr": args.trait_pwr,
                  "bump_skill_to": args.bump_skill_to,
                  "add_tokens": args.add_tokens,
                  "mark_challenge_told": not args.no_mark_challenge,
                  "grant_legend": not args.no_grant_legend}
        if args.age:
            kwargs["age"] = args.age
        result = cheats.prep_ruling_class(sf, **kwargs)
        print(f"preset ruling (age={result['age']}):")
        for line in result["changes"]:
            print(f"  {line}")
        if not result["changes"]:
            print("  (no changes — already in the target state)")
    else:
        raise SystemExit(f"unknown preset: {args.which!r}")
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def cmd_powers(args):
    sf = load(args.file)
    fam = sf["family"]["theFamily"]
    print(f"current powers: {fam['powers']}")
    if args.add:
        if cheats.add_power(sf, args.add):
            print(f"  added: {args.add}")
        else:
            print(f"  already present: {args.add}")
    elif args.remove:
        if cheats.remove_power(sf, args.remove):
            print(f"  removed: {args.remove}")
        else:
            print(f"  not present: {args.remove}")
    else:
        return
    if not args.dry_run:
        bak = sf.write(args.file, backup=not args.no_backup)
        print(f"wrote {args.file}" + (f" (backup: {bak})" if bak else ""))


def _add_write_flags(p):
    p.add_argument("--dry-run", action="store_true", help="don't write the file")
    p.add_argument("--no-backup", action="store_true", help="skip the .bak copy")


def build_parser():
    p = argparse.ArgumentParser(prog="sevengs_save", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump", help="print all records")
    d.add_argument("file", type=str, help="local path or [user@]host:/remote/path")
    d.add_argument("--depth", type=int, default=3)
    d.set_defaults(func=cmd_dump)

    g = sub.add_parser("get", help="read one dotted-path value")
    g.add_argument("file", type=str)
    g.add_argument("path", help="e.g. family.theFamily.scoreTotal")
    g.set_defaults(func=cmd_get)

    s = sub.add_parser("set", help="set one dotted-path value (literal-evaled)")
    s.add_argument("file", type=str)
    s.add_argument("path")
    s.add_argument("value", help="Python literal (e.g. 9999, 'foo', [1,2])")
    _add_write_flags(s)
    s.set_defaults(func=cmd_set)

    sc = sub.add_parser("score", help="bump or nudge family score")
    sc.add_argument("file", type=str)
    g1 = sc.add_mutually_exclusive_group()
    g1.add_argument("--add", type=int, help="add N points to goal+totals")
    g1.add_argument("--set", type=int, help="set goal.points absolutely")
    g1.add_argument("--nudge", action="store_true", help="set goal.points just under needed")
    sc.add_argument("--margin", type=int, default=1, help="margin for --nudge (default 1)")
    _add_write_flags(sc)
    sc.set_defaults(func=cmd_score)

    sk = sub.add_parser(
        "skills",
        help="list/set head or mate skills (synced to family + familyPC)",
    )
    sk.add_argument("file", type=str)
    sk.add_argument("--who", choices=["head", "mate"], default="head")
    sk.add_argument("--list", action="store_true", help="print current skills and exit")
    sk.add_argument(
        "--set", action="append", metavar="SYMBOL=N",
        help="set one skill (may be repeated; adds symbol if absent)",
    )
    sk.add_argument(
        "--remove", action="append", metavar="SYMBOL",
        help="remove a skill entry (may be repeated)",
    )
    sk.add_argument("--by", type=int, help="add N to every skill")
    sk.add_argument("--to", type=int, help="set every skill to N")
    sk.add_argument(
        "--only", nargs="+", metavar="SYMBOL",
        help="restrict --by/--to to these symbols",
    )
    _add_write_flags(sk)
    sk.set_defaults(func=cmd_skills)

    lv = sub.add_parser("loves", help="toggle or set head/mate lovesSpouse")
    lv.add_argument("file", type=str)
    lv.add_argument("--who", choices=["head", "mate"], default="head")
    lv_g = lv.add_mutually_exclusive_group(required=True)
    lv_g.add_argument(
        "--set", type=lambda x: x.lower() in ("1", "true", "yes", "y", "on"),
        metavar="true|false", help="set lovesSpouse to a literal bool",
    )
    lv_g.add_argument("--toggle", action="store_true", help="flip the current value")
    _add_write_flags(lv)
    lv.set_defaults(func=cmd_loves)

    tk = sub.add_parser(
        "tokens",
        help="list/add/remove items in familyPC.tempTokens",
    )
    tk.add_argument("file", type=str)
    tk.add_argument(
        "--add", action="append", metavar="SYMBOL[xN]",
        help="append SYMBOL (optionally N copies, e.g. CopperT1x3; may be repeated)",
    )
    tk.add_argument(
        "--remove", action="append", metavar="SYMBOL",
        help="remove all occurrences of SYMBOL (may be repeated)",
    )
    _add_write_flags(tk)
    tk.set_defaults(func=cmd_tokens)

    pw = sub.add_parser("powers", help="add or remove a power tag")
    pw.add_argument("file", type=str)
    g3 = pw.add_mutually_exclusive_group()
    g3.add_argument("--add", help="power tag to add (e.g. personalHero)")
    g3.add_argument("--remove", help="power tag to remove")
    _add_write_flags(pw)
    pw.set_defaults(func=cmd_powers)

    tr = sub.add_parser("traits", help="list/add/remove items in family.theFamily.traits")
    tr.add_argument("file", type=str)
    tr.add_argument("--add", action="append", metavar="TRAIT", help="trait to add (may be repeated)")
    tr.add_argument("--remove", action="append", metavar="TRAIT")
    _add_write_flags(tr)
    tr.set_defaults(func=cmd_traits)

    tp = sub.add_parser("tpwrs", help="list/add/remove items in family.theFamily.traitPwrs")
    tp.add_argument("file", type=str)
    tp.add_argument("--add", action="append", metavar="POWER")
    tp.add_argument("--remove", action="append", metavar="POWER")
    _add_write_flags(tp)
    tp.set_defaults(func=cmd_tpwrs)

    at = sub.add_parser("agetraits", help="list/add/remove items in ages.{chpt,age,game}Traits")
    at.add_argument("file", type=str)
    at.add_argument("--scope", choices=["chpt", "age", "game"], default="chpt")
    at.add_argument("--add", action="append", metavar="TRAIT")
    at.add_argument("--remove", action="append", metavar="TRAIT")
    _add_write_flags(at)
    at.set_defaults(func=cmd_agetraits)

    ht = sub.add_parser("heroicstold", help="list/add/remove tale IDs in tales.heroicsTold")
    ht.add_argument("file", type=str)
    ht.add_argument("--add", action="append", metavar="TALE_ID")
    ht.add_argument("--remove", action="append", metavar="TALE_ID")
    _add_write_flags(ht)
    ht.set_defaults(func=cmd_heroicstold)

    pr = sub.add_parser("preset", help="apply a curated multi-field preset")
    pr.add_argument("file", type=str)
    pr.add_argument("which", choices=["ruling"], help="preset to apply")
    pr.add_argument("--age", choices=["Copper", "Bronze", "Iron"],
                    help="override age (default: infer from save)")
    pr.add_argument("--trait-pwr", default="commander",
                    help="trait power to grant (default: commander)")
    pr.add_argument("--bump-skill-to", type=int, default=80,
                    help="bump head's <age>R* skills to at least this value (default 80)")
    pr.add_argument("--add-tokens", type=int, default=6,
                    help="copies of <age>R0 to drop into tempTokens (default 6)")
    pr.add_argument("--no-mark-challenge", action="store_true",
                    help="don't mark the chapter-entry challenge as told")
    pr.add_argument("--no-grant-legend", action="store_true",
                    help="don't ensure ('U','rulingCaste') in family.legends")
    _add_write_flags(pr)
    pr.set_defaults(func=cmd_preset)

    sv = sub.add_parser("serve", help="open a local web viewer for the save")
    sv.add_argument("file", type=str)
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    sv.set_defaults(func=cmd_serve)

    return p


def cmd_serve(args):
    from . import serve as serve_mod
    rc = serve_mod.serve(args.file, port=args.port, open_browser=not args.no_browser)
    if rc:
        sys.exit(rc)


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
