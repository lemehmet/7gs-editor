"""Local HTTP viewer + inline editor for a 7 Grand Steps save file.

    python3 -m sevengs_save serve artifacts/data/sunerg.fam
    python3 -m sevengs_save serve deck:/path/to/save.fam --port 9000

The server holds a single SaveFile in memory. Edits mutate it via the
`cheats` module; commit writes to disk (with .bak); discard reloads. The
viewer page polls nothing — every request returns the current state, so
the UI stays in sync via response payloads.
"""

import json
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import cheats, jsonview
from .savefile import load


_STATIC_DIR = Path(__file__).parent / "static"


class _State:
    """In-memory holder for the active SaveFile.

    All mutating operations go through the lock; reads grab it briefly for
    snapshot consistency. The dirty flag flips on any successful edit and
    clears on commit/discard.
    """

    def __init__(self, source_path: str) -> None:
        self.source = source_path
        self.lock = threading.Lock()
        self.savefile = None
        self.dirty = False
        self.reload()

    def reload(self) -> None:
        with self.lock:
            self.savefile = load(self.source)
            self.dirty = False

    def commit(self) -> str | None:
        with self.lock:
            bak = self.savefile.write(self.source, backup=True)
            self.dirty = False
            return bak

    def apply(self, op: str, args: dict) -> Any:
        with self.lock:
            result = _dispatch(self.savefile, op, args)
            self.dirty = True
            return result

    def snapshot(self) -> dict:
        with self.lock:
            payload = jsonview.encode_savefile(self.savefile, self.source)
            payload["dirty"] = self.dirty
            return payload


_state: _State | None = None


def _dispatch(sf, op: str, args: dict) -> Any:
    """Map an edit op name to a cheats.* call. New ops added here."""
    if op == "skill_set":
        return cheats.set_skill(sf, args["who"], args["symbol"], int(args["value"]))
    if op == "skill_remove":
        return cheats.remove_skill(sf, args["who"], args["symbol"])
    if op == "child_skill_set":
        return cheats.set_child_skill(sf, int(args["index"]), args["symbol"], int(args["value"]))
    if op == "child_skill_remove":
        return cheats.remove_child_skill(sf, int(args["index"]), args["symbol"])
    if op == "loves_set":
        return cheats.set_loves_spouse(sf, args["who"], bool(args["value"]))
    if op == "token_add":
        return cheats.add_temp_token(sf, args["symbol"], int(args.get("count", 1)))
    if op == "token_remove":
        # `count` omitted = remove all occurrences
        count = int(args["count"]) if "count" in args and args["count"] is not None else None
        return cheats.remove_temp_token(sf, args["symbol"], count)
    if op == "goal_points_set":
        # Edits goal.points and syncs score + scoreTotal by the delta
        # (mirrors what family.AddScore would have done in-game).
        return cheats.set_goal_points_synced(sf, int(args["value"]))
    raise ValueError(f"unknown op: {op}")


def _build_handler(viewer_html: bytes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter default
            pass

        # ---- routing -----------------------------------------------------

        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, viewer_html, "text/html; charset=utf-8")
            elif self.path == "/api/save":
                self._send_json(HTTPStatus.OK, _state.snapshot())
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            body = self._read_body()
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError as e:
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"invalid json: {e}"})

            if self.path == "/api/edit":
                op = payload.get("op")
                args = payload.get("args") or {}
                if not op:
                    return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing op"})
                try:
                    result = _state.apply(op, args)
                except (KeyError, ValueError, TypeError) as e:
                    return self._send_json(HTTPStatus.BAD_REQUEST,
                                           {"error": f"{type(e).__name__}: {e}"})
                snapshot = _state.snapshot()
                snapshot["result"] = _jsonable(result)
                return self._send_json(HTTPStatus.OK, snapshot)

            if self.path == "/api/commit":
                try:
                    bak = _state.commit()
                except OSError as e:
                    return self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                           {"error": f"write failed: {e}"})
                snapshot = _state.snapshot()
                snapshot["backup"] = bak
                return self._send_json(HTTPStatus.OK, snapshot)

            if self.path == "/api/discard":
                try:
                    _state.reload()
                except OSError as e:
                    return self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                           {"error": f"reload failed: {e}"})
                return self._send_json(HTTPStatus.OK, _state.snapshot())

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        # ---- helpers -----------------------------------------------------

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _send_json(self, status, obj):
            body = json.dumps(obj).encode("utf-8")
            self._send(status, body, "application/json")

        def _send(self, status, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _jsonable(value):
    """Convert cheats return values to something json.dumps will accept."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


def serve(save_path: str, *, port: int = 8765, open_browser: bool = True) -> int:
    global _state
    _state = _State(save_path)
    viewer_html = (_STATIC_DIR / "viewer.html").read_bytes()

    handler_cls = _build_handler(viewer_html)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as e:
        print(f"could not bind 127.0.0.1:{port}: {e}", file=sys.stderr)
        print(f"  try a different port:  --port {port + 1}", file=sys.stderr)
        return 2

    url = f"http://127.0.0.1:{port}/"
    print(f"loaded {len(_state.savefile.records)} records from {save_path}")
    print(f"serving on {url}  (Ctrl-C to stop)")

    if open_browser:
        threading.Thread(target=_open_after_delay, args=(url,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()
    return 0


def _open_after_delay(url: str, delay: float = 0.3) -> None:
    time.sleep(delay)
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        pass
