"""Exception hierarchy for the downloader core.

Every failure the core can produce is one of these types, so the three user
interfaces can present errors without pattern-matching on message strings or
catching bare ``Exception`` the way both original scripts did.

Each error carries a ``user_message`` suitable for direct display: the CLI
prints it, the web layer serialises it to JSON, and the GUI puts it in a label.
"""

from __future__ import annotations


class YtdlError(Exception):
    """Base class for every error raised by the downloader core."""

    #: Fallback shown when a subclass does not set a more specific message.
    default_message = "Something went wrong."

    def __init__(self, message: str | None = None, *, hint: str | None = None):
        self.user_message = message or self.default_message
        self.hint = hint
        super().__init__(self.user_message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.user_message} {self.hint}".strip() if self.hint else self.user_message


class InvalidURLError(YtdlError):
    """The supplied text is not a URL pointing at a YouTube video."""

    default_message = "That does not look like a YouTube video link."


class VideoUnavailableError(YtdlError):
    """YouTube will not serve this video (private, removed, age-gated, geo-blocked)."""

    default_message = "This video is not available."


class StreamNotFoundError(YtdlError):
    """No stream matches the requested quality."""

    default_message = "No stream is available at the requested quality."


class FFmpegNotFoundError(YtdlError):
    """No usable ffmpeg binary could be located."""

    default_message = "ffmpeg could not be found."

    def __init__(self, message: str | None = None, *, hint: str | None = None):
        super().__init__(
            message,
            hint=hint
            or (
                "Install ffmpeg and put it on PATH, set the YTDL_FFMPEG environment "
                "variable to its full path, or install the bundled fallback with "
                "'pip install youtube-downloader-suite[ffmpeg]'."
            ),
        )


class FFmpegError(YtdlError):
    """An ffmpeg invocation exited non-zero."""

    default_message = "ffmpeg failed while processing the file."

    def __init__(self, message: str | None = None, *, stderr: str = "", returncode: int | None = None):
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(message)


class DownloadCancelledError(YtdlError):
    """The caller cancelled the operation through a :class:`~ytdl.core.jobs.CancelToken`."""

    default_message = "Download cancelled."


class OutputPathError(YtdlError):
    """The requested output directory is unusable."""

    default_message = "The output folder cannot be written to."
