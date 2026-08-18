"""Turning video titles into safe filenames.

Both source repositories shared this function, byte for byte::

    def remove_non_alpha(s):
        return re.sub(r"[^a-zA-Z\\s]", '', s).replace(' ', '-')

It strips every character that is not an ASCII letter, which discards more than
it protects:

* digits vanish — "Top 10 Songs of 2024" becomes "Top-Songs-of",
* non-English titles can reduce to an empty string, and the download then fails
  on a file named ".mp4",
* it still permits names Windows rejects outright (``CON``, ``PRN``, trailing
  dots or spaces).

:func:`sanitize_filename` keeps digits and Unicode letters, removes only what is
genuinely unsafe for a path, and always returns a usable name. The original is
preserved as :func:`legacy_remove_non_alpha` so historical behaviour can still
be reproduced.
"""

from __future__ import annotations

import re
import unicodedata

#: Reserved device names on Windows, which cannot be used as a file stem even
#: with an extension. Checked case-insensitively.
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: Characters no mainstream filesystem accepts, plus the ASCII control range.
_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_HYPHEN_RUN_RE = re.compile(r"-{2,}")

DEFAULT_MAX_LENGTH = 120
FALLBACK_NAME = "video"


def sanitize_filename(
    title: str,
    *,
    max_length: int = DEFAULT_MAX_LENGTH,
    fallback: str = FALLBACK_NAME,
) -> str:
    """Return ``title`` as a filename stem that is safe on Windows, macOS and Linux.

    Spaces become hyphens, matching the naming convention both original scripts
    produced, so output files look familiar to existing users.

    >>> sanitize_filename("Top 10 Songs of 2024")
    'Top-10-Songs-of-2024'
    >>> sanitize_filename("AC/DC: Back in Black")
    'ACDC-Back-in-Black'
    >>> sanitize_filename("   ")
    'video'
    """
    if not title:
        return fallback

    # NFKC folds compatibility forms (full-width Latin, ligatures) to plain
    # equivalents, so visually identical titles produce identical filenames.
    name = unicodedata.normalize("NFKC", title)

    name = _UNSAFE_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()
    name = name.replace(" ", "-")
    name = _HYPHEN_RUN_RE.sub("-", name)

    # Windows cannot store a name ending in a dot or space.
    name = name.strip(". -")

    if len(name) > max_length:
        name = name[:max_length].rstrip(". -")

    if not name:
        return fallback

    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = f"{name}-file"

    return name


def unique_path(directory, stem: str, suffix: str):
    """Return a non-colliding path, appending ``-1``, ``-2``… as needed.

    Both original scripts silently overwrote an existing file of the same name.
    Downloading the same video twice now produces two files rather than
    destroying the first.
    """
    from pathlib import Path

    directory = Path(directory)
    suffix = suffix if suffix.startswith(".") else f".{suffix}"

    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate

    for counter in range(1, 1000):
        candidate = directory / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate

    # Practically unreachable; keeps the function total rather than looping.
    from time import time_ns

    return directory / f"{stem}-{time_ns()}{suffix}"


def legacy_remove_non_alpha(s: str) -> str:
    """The original shared implementation, retained for reference and tests.

    .. deprecated:: 1.0
       Use :func:`sanitize_filename`. This drops digits and non-ASCII letters.
    """
    return re.sub(r"[^a-zA-Z\s]", "", s).replace(" ", "-")
