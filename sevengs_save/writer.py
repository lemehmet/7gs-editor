"""Pickle protocol-1 writer that matches what the game's Python 2.6 cPickle
produced.

Two reasons to depart from `pickle.dump(..., protocol=2)`:

1. **String opcode.** Python 3's pickler emits BINUNICODE (`X`) for `str`.
   Python 2.6 cPickle accepts that, but materializes the value as `unicode`,
   which can subtly break Py2 code that treats it as bytes. The game was
   written when `str` == bytes, so we emit SHORT_BINSTRING / BINSTRING for
   ASCII strings, matching what its own saves contain. Non-ASCII falls back
   to BINUNICODE.

2. **Protocol.** The game calls `cPickle.dump(obj, fid, True)` — the third
   arg is `protocol`, and `True` is `int(1)`. So saves are protocol 1, not
   2. Protocol 1 lacks the leading `\\x80\\x02` PROTO header opcode, which
   keeps concatenated records byte-aligned the way the game expects.
"""

import pickle


PROTOCOL = 1


class _Py2CompatPickler(pickle._Pickler):  # pure-Python pickler is overridable
    dispatch = pickle._Pickler.dispatch.copy()

    def save_str(self, obj):
        try:
            encoded = obj.encode("ascii")
        except UnicodeEncodeError:
            # Non-ASCII: emit BINUNICODE (proto-1 supports it; SHORT_BINUNICODE
            # was only added in protocol 4).
            data = obj.encode("utf-8")
            self.write(pickle.BINUNICODE + len(data).to_bytes(4, "little") + data)
            self.memoize(obj)
            return

        n = len(encoded)
        if n <= 0xFF:
            self.write(pickle.SHORT_BINSTRING + bytes([n]) + encoded)
        else:
            self.write(pickle.BINSTRING + n.to_bytes(4, "little") + encoded)
        self.memoize(obj)

    dispatch[str] = save_str


def dump_py2_compat(obj, file) -> None:
    """Write one pickle record matching the game's protocol-1 cPickle output."""
    _Py2CompatPickler(file, protocol=PROTOCOL).dump(obj)
