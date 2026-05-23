"""Transparent SSH/SCP support for save-file I/O.

A path string of the form ``[user@]host:/abs/or/rel/path`` is treated as a
remote location. We shell out to system ``scp`` and ``ssh``, so anything in
your ``~/.ssh/config`` (Host aliases, IdentityFile, ForwardAgent, …) is
honoured automatically.

Steam Deck example:
    deck@steamdeck.local:/home/deck/.steam/steam/steamapps/compatdata/<id>/pfx/drive_c/users/steamuser/Application\\ Data/Mousechief/7GS/Step_1/family.fam

Backups for remote paths are performed *on the remote* with ``cp -p`` to
avoid round-tripping the file twice over the network.
"""

import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

# user@host:path or host:path. The host part cannot contain '/', which keeps
# us from mis-classifying Windows-style 'C:/foo' or local 'a/b:c' paths.
_REMOTE_RE = re.compile(r"^(?P<host>[^/:\s]+(?:@[^/:\s]+)?):(?P<path>.+)$")


def parse(path: str) -> tuple[str, str] | None:
    """Return (host, remote_path) for an SSH path, or None for a local one."""
    m = _REMOTE_RE.match(path)
    if not m:
        return None
    return m.group("host"), m.group("path")


def is_remote(path: str) -> bool:
    return parse(path) is not None


def read_bytes(path: str) -> bytes:
    """Read a file. Local Path or SSH `host:path` both work."""
    parsed = parse(path)
    if parsed is None:
        return Path(path).read_bytes()
    host, remote_path = parsed
    remote_spec = f"{host}:{shlex.quote(remote_path)}"
    with tempfile.NamedTemporaryFile(delete=False, prefix="7gs_fetch_") as tmp:
        local = Path(tmp.name)
    try:
        _run(["scp", "-q", remote_spec, str(local)])
        return local.read_bytes()
    finally:
        local.unlink(missing_ok=True)


def write_bytes(path: str, data: bytes, *, backup: bool = True) -> str | None:
    """Write a file. For SSH paths, the backup happens remotely.

    Returns the backup path if one was made, else None.
    """
    parsed = parse(path)
    if parsed is None:
        local_path = Path(path)
        bak = None
        if backup and local_path.exists():
            bak = local_path.with_suffix(local_path.suffix + ".bak")
            shutil.copy2(local_path, bak)
        local_path.write_bytes(data)
        return str(bak) if bak else None

    host, remote_path = parsed
    quoted = shlex.quote(remote_path)
    bak_path = remote_path + ".bak"
    bak_quoted = shlex.quote(bak_path)
    backup_made = False
    if backup:
        # If the file exists, copy it to .bak (preserving metadata); if not,
        # do nothing. We capture stdout to detect whether the backup ran so
        # we can report it.
        result = _run(
            [
                "ssh",
                host,
                f"if [ -f {quoted} ]; then cp -p {quoted} {bak_quoted} && echo BACKED_UP; fi",
            ],
            capture_output=True,
        )
        backup_made = b"BACKED_UP" in result.stdout

    with tempfile.NamedTemporaryFile(delete=False, prefix="7gs_push_") as tmp:
        tmp.write(data)
        local = Path(tmp.name)
    try:
        _run(["scp", "-q", str(local), f"{host}:{quoted}"])
    finally:
        local.unlink(missing_ok=True)
    return f"{host}:{bak_path}" if backup_made else None


def _run(cmd: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, capture_output=capture_output)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(
            f"{cmd[0]} failed ({e.returncode}): {' '.join(cmd)}\n  {stderr}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(
            f"{cmd[0]} not found on PATH — install OpenSSH client to use SSH paths"
        ) from e
