# 7gs-editor

A save-game editor for [**7 Grand Steps: What Ancients Begat**](https://store.steampowered.com/app/238930/7_Grand_Steps_What_Ancients_Begat/) — a 2013 lineage-progression board game by Mousechief. The original is a delightful, slow-burn game; this tool exists for anyone who wants to peek behind the curtain, fix a stuck save, or "see how it ends" without playing every age.

> Unofficial fan tool. Not affiliated with or endorsed by Mousechief. 7 Grand Steps is © Mousechief — buy it on [Steam](https://store.steampowered.com/app/238930/7_Grand_Steps_What_Ancients_Begat/) if you don't own it yet.

## What it does

- **Load and inspect** any `.fam` / `.rsv` save file. Saves are sequences of cPickle records; the tool parses all 12 records (ages, family, spaces, wheel, people, scoring, pieces, tales, tutorial, familyPC, peers, graveyard) without needing the game running.
- **Surgical CLI edits** that won't break the game's invariants:
  - Score (`scoreTotal`, goal points, nudge-to-next-age)
  - Powers (add/remove tags like `personalHero`)
  - Per-skill values on the head/heir or mate — synced across both the `family` and `familyPC` records the game writes in parallel
  - `lovesSpouse` toggle
  - `familyPC.tempTokens` list add/remove
  - Generic dotted-path `get` / `set` for fields not yet covered by a named command
- **Local web viewer** with inline edit. Tree view of the entire save, type-coloured badges, fuzzy filter, click-to-copy dotted paths, click-to-edit on the supported fields (`goal.points`, per-skill values, `lovesSpouse`, `tempTokens` add/remove), dirty indicator, save/discard buttons. Editing `goal.points` automatically applies the same delta to `score` and `scoreTotal` to keep the three counters consistent.
- **SSH-native paths.** Anywhere a local path is accepted (`some/save.fam`), the same command takes `host:/path` or `user@host:/path` and routes I/O through your system `scp`/`ssh` — no extra dependencies. Useful on Steam Deck, where the game lives under a Proton prefix and you don't want to copy files around manually.
- **Round-trip safe.** The writer emits protocol-1 cPickle output with `SHORT_BINSTRING`, matching what the game itself produces (Python 2.6 `cPickle.dump(obj, f, True)`). Strings come back as `str` in Python 2, not `unicode`.
- **Backup on every write.** A `.bak` lands next to the original before any change is committed (use `--no-backup` to skip).

## Requirements

- Python **3.10+** (uses `match`-style typing and `|` unions)
- OpenSSH client (`scp`, `ssh`) on `PATH` — only needed for remote save paths

No third-party Python packages required to run the editor itself. `uncompyle6` is only needed if you want to regenerate the decompiled game sources for reference (see *Reading the game's source* below).

## Install

The project runs from source — there's no PyPI package yet. Clone the repo and invoke the package directly:

```bash
git clone git@github.com:lemehmet/7gs-editor.git
cd 7gs-editor
python3 -m sevengs_save --help
```

## Usage

All commands take a save-file path as the first argument. Paths can be local or `[user@]host:/remote/path` (SSH).

### Inspect

```bash
# Print the whole save (depth-limited)
python3 -m sevengs_save dump   path/to/save.fam --depth 2

# Read one dotted-path value
python3 -m sevengs_save get    path/to/save.fam family.theFamily.scoreTotal

# Generic set (Python literal-evaled)
python3 -m sevengs_save set    path/to/save.fam family.theFamily.scoreTotal 9999
```

### Score / age progression

```bash
python3 -m sevengs_save score path/to/save.fam              # report current
python3 -m sevengs_save score path/to/save.fam --add 200    # add to goal + totals
python3 -m sevengs_save score path/to/save.fam --set 95     # set goal.points
python3 -m sevengs_save score path/to/save.fam --nudge      # one tick from next age
```

### Skills (synced to both records)

```bash
python3 -m sevengs_save skills path/to/save.fam --who head --list
python3 -m sevengs_save skills path/to/save.fam --who head --set CopperT1=999 --set IronT1=42
python3 -m sevengs_save skills path/to/save.fam --who mate --to 80 --only CopperN0 CopperN1
python3 -m sevengs_save skills path/to/save.fam --who head --remove IronT1
```

### `lovesSpouse`

```bash
python3 -m sevengs_save loves path/to/save.fam --who mate --toggle
python3 -m sevengs_save loves path/to/save.fam --who head --set false
```

### `familyPC.tempTokens`

The `x` separator is used for counts so shells (like fish) don't glob-expand `*`:

```bash
python3 -m sevengs_save tokens path/to/save.fam                          # just list
python3 -m sevengs_save tokens path/to/save.fam --add IronT2x3 --remove BronzeT0
```

### Powers

```bash
python3 -m sevengs_save powers path/to/save.fam --add personalHero
python3 -m sevengs_save powers path/to/save.fam --remove BronzeL2
```

### Local web viewer + editor

```bash
python3 -m sevengs_save serve path/to/save.fam
# opens http://127.0.0.1:8765/ in your browser
```

In the viewer:

- Click any row to copy its dotted path
- `/` focuses the filter; `Esc` clears it
- For supported fields, click the value to edit (`goal.points`, skills, `lovesSpouse`), or use the `×` / `+` buttons on `tempTokens` items / container
- Editing `family.theFamily.goal.points` (the "X / 100" bar in-game) also applies the same delta to `family.theFamily.score` and `scoreTotal`, mirroring what `family.AddScore` does in-game so all three stay coherent. Negative deltas pass through (your running totals can go negative if you roll the value down).
- The `unsaved` badge lights up after any edit; **save** writes to disk (with `.bak`), **discard** reloads from disk
- Cmd/Ctrl-S also saves

Common flags on every write command: `--dry-run` (don't touch the file), `--no-backup` (skip the `.bak`).

### Steam Deck (or any remote host)

Set up an SSH alias once in `~/.ssh/config`:

```
Host deck
  HostName steamdeck.local
  User deck
  IdentityFile ~/.ssh/id_ed25519
```

Then point any command at the remote path:

```bash
# Find your save folder under the Proton prefix
ssh deck 'find ~/.steam/steam/steamapps/compatdata -name "*.fam" 2>/dev/null'

# Browse / edit interactively
python3 -m sevengs_save serve  deck:/home/deck/.../Step_1/family.fam

# Or one-shot from the CLI
python3 -m sevengs_save score  deck:/home/deck/.../Step_1/family.fam --nudge
python3 -m sevengs_save skills deck:/home/deck/.../Step_1/family.fam --who head --to 99
```

Backups are made on the remote side (via `ssh "cp -p file file.bak"`), so the file never needs to round-trip the network twice.

## Save-file format (short version)

A `.fam` file is a concatenation of **12 cPickle protocol-1 records**, written in saver-id order. The game registers savers in each module's `sgInit()`:

| ID | Module      | What it contains                                                  |
| -: | ----------- | ----------------------------------------------------------------- |
|  1 | `ages`      | Current age, name pools, chapter traits, obsolete tech list       |
|  2 | `family`    | Player family: score, goal, powers, head, mate, story archives    |
|  3 | `spaces`    | Track symbols + visibility per track (L/N/R/T)                    |
|  4 | `wheel`     | Wheel rotation                                                    |
|  5 | `people`    | Live people + corpse list                                         |
|  6 | `scoring`   | Discoveries per generation                                        |
|  7 | `pieces`    | Beads on the board                                                |
|  8 | `tales`     | Tale archive                                                      |
|  9 | `tutorial`  | Tutorial flags                                                    |
| 13 | `familyPC`  | head/mate (5-tuples), children, `tempTokens` symbol list          |
| 14 | `peers`     | Rival families + AI state                                         |
| 15 | `graveyard` | Ancestors                                                         |

Note the head/heir and mate are saved **twice** — once in record 2 (`family.theFamily.head/mate` as a person dict) and again in record 13 (`familyPC.head/mate` as a 5-tuple wrapping the same person dict). Both copies are produced from the same `Person.Save()` call at write time, so they must match on disk. All the editor's head/mate operations mirror to both.

## Reading the game's source

The decompiled `.pyo` sources from `library.zip` are excluded from the repo (they're derivative work of copyrighted bytecode). To regenerate them locally for reference:

```bash
python3 -m venv /tmp/decomp_venv
/tmp/decomp_venv/bin/pip install uncompyle6
mkdir -p /tmp/7gs_lib && cd /tmp/7gs_lib
unzip -o /path/to/7GS/library.zip 'saveGame.pyo' 'family.pyo' 'familyPC.pyo' \
   'people.pyo' 'goals.pyo' 'tokTray.pyo' 'ages.pyo' '<...>.pyo'
/tmp/decomp_venv/bin/uncompyle6 -o ./decompiled *.pyo
```

The most useful modules for save-editing are `saveGame.py`, `family.py`, `familyPC.py`, `people.py`, `goals.py`, and `tokTray.py`.

## Caveats

- **Quit the game before editing.** Steam/Proton may cache the save file; an in-game save after you've edited can clobber your changes (and a tool save while the game is running may not be visible to the running session).
- **Single in-memory snapshot per `serve` session.** If you save in-game while the web viewer is running, click **discard** to reload from disk (or restart the server).
- **Tested against version 1.0 of the game** (the bytecode timestamp is 2013-06-19). The format hasn't changed in any version I've seen, but if you have a save the loader can't parse, please open an issue with the `.fam` file size and the first ~64 bytes (hex).
- **Cross-record edits.** Anything more invasive than the supported surgical edits (moving a person between tracks, changing generation count, etc.) needs careful bookkeeping across multiple records. The library's dotted-path `set` will happily write those, but the game may not load the result cleanly.

## Layout

```
sevengs_save/
├── __init__.py        public API: load, SaveFile, SAVER_NAMES, SAVER_ORDER
├── __main__.py        `python3 -m sevengs_save …`
├── cli.py             argparse front-end
├── savefile.py        SaveFile + load(); dotted-path get/assign; write()
├── stubs.py           Pickle-compatible stand-ins for game classes (goals.Goal, etc.)
├── writer.py          Protocol-1 + SHORT_BINSTRING pickler matching the game's cPickle output
├── remote.py          Transparent SSH/SCP wrapping for local-or-remote paths
├── cheats.py          High-level edit primitives (score, skills, powers, tokens, …)
├── jsonview.py        Save → tagged JSON for the viewer
├── serve.py           stdlib HTTP server (viewer + edit endpoints)
└── static/viewer.html Single-file SPA: tree view, filter, inline edit, dirty/save/discard
```

## Acknowledgements

- **Mousechief** for [7 Grand Steps](https://store.steampowered.com/app/238930/7_Grand_Steps_What_Ancients_Begat/) — a genuinely original game; please support the original.
- **Claude Code (Anthropic)** — most of this codebase was written in collaboration with Claude Opus 4.7.
- `uncompyle6` for making the Python 2.6 bytecode legible.

## License

MIT — see [LICENSE](LICENSE).
