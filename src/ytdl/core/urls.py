"""YouTube URL parsing, validation and normalisation.

Replaces the validation in both source scripts, each of which was unsound:

* ``result.netloc == "www.youtube.com"`` rejected perfectly valid links —
  bare ``youtube.com``, ``m.youtube.com`` (what you get sharing from a phone),
  and ``music.youtube.com``.
* ``"youtu.be" in video_url`` was a substring test against the *whole* URL, so
  ``https://example.com/?ref=youtu.be`` passed validation and was handed
  straight to the downloader.
* Neither handled ``/shorts/``, ``/embed/`` or ``/live/`` links at all.

Validation here is host-based against an allow-list, and the video id is
extracted and checked, so a URL that passes is one the downloader can use.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

from .errors import InvalidURLError

#: Hosts that serve YouTube videos. Compared case-insensitively with any
#: leading "www." already stripped, so each entry is listed once.
_ALLOWED_HOSTS = frozenset(
    {
        "youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "youtu.be",
    }
)

#: YouTube video ids are exactly 11 characters of URL-safe base64.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

#: Path forms that carry the id as the final segment.
_PATH_PREFIXES = ("/shorts/", "/embed/", "/live/", "/v/")

CANONICAL_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"


def _normalise_host(netloc: str) -> str:
    host = netloc.split("@")[-1]           # strip any userinfo
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host  # strip port
    host = host.strip("[]").lower()        # strip IPv6 brackets
    return host[4:] if host.startswith("www.") else host


def extract_video_id(url: str) -> str:
    """Return the 11-character video id, or raise :class:`InvalidURLError`.

    Accepts every common share form::

        https://www.youtube.com/watch?v=dQw4w9WgXcQ
        https://youtu.be/dQw4w9WgXcQ?t=42
        https://m.youtube.com/watch?v=dQw4w9WgXcQ
        https://music.youtube.com/watch?v=dQw4w9WgXcQ
        https://www.youtube.com/shorts/dQw4w9WgXcQ
        https://www.youtube.com/embed/dQw4w9WgXcQ
        https://www.youtube.com/live/dQw4w9WgXcQ
    """
    if not url or not url.strip():
        raise InvalidURLError("No link was provided.")

    raw = url.strip()

    # Accept a bare video id pasted on its own.
    if _VIDEO_ID_RE.match(raw):
        return raw

    # Tolerate a missing scheme ("youtube.com/watch?v=...") rather than
    # rejecting it, since that is how links arrive when copied from text.
    if "//" not in raw:
        raw = "https://" + raw

    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise InvalidURLError("That link could not be parsed.") from exc

    if parts.scheme not in ("http", "https"):
        raise InvalidURLError("Only http and https links are supported.")

    host = _normalise_host(parts.netloc)
    if host not in _ALLOWED_HOSTS:
        raise InvalidURLError(
            f"'{host or raw}' is not a YouTube address.",
            hint="Links must be on youtube.com, youtu.be, or music.youtube.com.",
        )

    candidate: Optional[str] = None

    if host == "youtu.be":
        candidate = parts.path.lstrip("/").split("/")[0]
    else:
        path = parts.path
        if path == "/watch":
            candidate = urllib.parse.parse_qs(parts.query).get("v", [None])[0]
        else:
            for prefix in _PATH_PREFIXES:
                if path.startswith(prefix):
                    candidate = path[len(prefix) :].split("/")[0]
                    break
            # A /watch/<id> form also appears in the wild.
            if candidate is None and path.startswith("/watch/"):
                candidate = path[len("/watch/") :].split("/")[0]

    if not candidate:
        raise InvalidURLError(
            "That is a YouTube address but does not point at a specific video.",
            hint="Use a link to a video, short, or livestream — not a channel or playlist.",
        )

    if not _VIDEO_ID_RE.match(candidate):
        raise InvalidURLError(f"'{candidate}' is not a valid YouTube video id.")

    return candidate


def normalize_url(url: str) -> str:
    """Return the canonical ``watch?v=`` URL for any accepted input form.

    Normalising before handing the URL to pytubefix means every downstream
    caller, cache key and log line sees one spelling of a given video.
    """
    return CANONICAL_WATCH_URL.format(video_id=extract_video_id(url))


def is_valid_youtube_url(url: str) -> bool:
    """Non-raising form of :func:`extract_video_id`, for input validation."""
    try:
        extract_video_id(url)
    except InvalidURLError:
        return False
    return True
