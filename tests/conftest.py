"""Shared test configuration.

The core reads several environment variables to let users override binary paths
and client selection. If a developer has any of them exported — which is likely,
since they are the documented way to work around a blocked YouTube client or a
non-standard ffmpeg location — the suite would silently test a different
configuration than CI does, and fail in ways that look like real regressions.

Every test therefore starts from a clean environment. Tests that care about a
variable set it explicitly with monkeypatch.
"""

import pytest

#: Every environment variable the core consults.
_YTDL_ENV_VARS = (
    "YTDL_CLIENT",
    "YTDL_FFMPEG",
    "YTDL_FFPROBE",
    "YTDL_OUTPUT",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Remove ambient ytdl configuration so tests are reproducible anywhere."""
    for name in _YTDL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def reset_module_caches():
    """Clear the module-level caches the core keeps between calls.

    ``streams._working_client`` remembers a successful YouTube client and
    ``ffmpeg._cached_*`` memoise binary discovery. Left set, they leak state
    from one test into the next depending on execution order.
    """
    from ytdl.core import ffmpeg, streams

    streams._working_client = None
    ffmpeg._cached_ffmpeg = None
    ffmpeg._cached_ffprobe = None
    yield
    streams._working_client = None
    ffmpeg._cached_ffmpeg = None
    ffmpeg._cached_ffprobe = None
