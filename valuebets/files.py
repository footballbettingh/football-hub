"""Writing a file so that a crash cannot leave half of it behind.

Every file this project keeps is written whole or not at all: into a temporary
file beside it, flushed to disk, then renamed over the old one. A rename within
one folder is atomic on Windows and on Linux, so whatever reads the file next —
the site, tomorrow's run, the cache an Actions run saves even when it failed —
finds the old version or the new one and never a truncated mix of the two.

Written the plain way, a run killed during the write left the ledger cut off
part-way. The cache step saves `data/` whatever happened, and the commit step
then committed what it found: the one file in the project that cannot be
rebuilt, shortened, with nothing to say so.
"""

import gzip
import os
import tempfile
from pathlib import Path


def write_text(path, text, encoding="utf-8", newline=None):
    """Replace `path` with `text` in one step.

    `newline=None` translates line endings the way `Path.write_text` does, so
    a file written here is byte for byte what it was before.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline=newline) as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_gzip(path, text, encoding="utf-8"):
    """`text`, gzipped, replacing `path` in one step, as `write_text` does."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(gzip.compress(text.encode(encoding), compresslevel=6))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_csv(frame, path, **kwargs):
    """`frame.to_csv(path, **kwargs)`, replacing the file in one step.

    Not translated: pandas already ends each line the way the platform does,
    exactly as it would writing to the path itself.
    """
    write_text(path, frame.to_csv(**kwargs), newline="")
