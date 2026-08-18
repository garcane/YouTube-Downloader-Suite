"""Video metadata and the list of things the user can actually download.

Both source scripts asked YouTube for streams and immediately printed them.
Here the result is a plain data structure, so the CLI can tabulate it, the web
layer can serialise it to JSON, and the GUI can bind it to a widget — from one
network round trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .errors import StreamNotFoundError, VideoUnavailableError
from .jobs import CancelToken, Phase, ProgressReporter, format_bytes
from .urls import normalize_url

#: MP3 bitrates offered for audio extraction, carried over verbatim from
#: YouTube-To-Audio's menu along with its guidance text.
MP3_BITRATES: list[tuple[str, str]] = [
    ("32k", "generally acceptable only for speech"),
    ("96k", "speech or low-quality streaming"),
    ("128k", "mid-range bitrate quality"),
    ("192k", "medium-quality bitrate"),
    ("256k", "commonly used high-quality bitrate"),
    ("320k", "highest level supported by the MP3 standard"),
]

DEFAULT_MP3_BITRATE = "320k"

#: pytubefix talks to YouTube's InnerTube API as one of several client types.
#: YouTube blocks them selectively and unpredictably — its "PO token" bot checks
#: reject some clients while leaving others working, and which ones change over
#: time. Rather than pinning one (pytubefix's own default, ANDROID_VR, is
#: currently rejected outright), each is tried in turn.
#:
#: MWEB leads because it is the only client still exposing progressive streams,
#: which download without needing ffmpeg to merge anything.
DEFAULT_CLIENT_ORDER = ("MWEB", "WEB", "WEB_SAFARI", "ANDROID_VR", "TV", "IOS")

#: Override the order with a comma-separated list, e.g. YTDL_CLIENT=WEB,MWEB
ENV_CLIENT = "YTDL_CLIENT"

#: Remembers whichever client last worked, so repeated calls in one session do
#: not re-probe the blocked ones.
_working_client: Optional[str] = None


def _client_candidates() -> list[str]:
    import os

    override = os.environ.get(ENV_CLIENT, "").strip()
    if override:
        return [c.strip().upper() for c in override.split(",") if c.strip()]

    order = list(DEFAULT_CLIENT_ORDER)
    if _working_client and _working_client in order:
        order.remove(_working_client)
        order.insert(0, _working_client)
    return order


def _is_client_specific_failure(exc: Exception) -> bool:
    """True when another client might still succeed.

    A blocked or unsupported client reports the video as unavailable, returns
    HTTP 400/403, or trips bot detection — none of which say anything about the
    video itself. Conditions that are genuinely true of the video, such as it
    being private or members-only, are handled by the caller and never retried.
    """
    import urllib.error

    from pytubefix import exceptions as pte

    return isinstance(
        exc,
        (
            pte.BotDetection,
            pte.PoTokenRequired,
            pte.LoginRequired,
            pte.VideoUnavailable,
            pte.UnknownVideoError,
            pte.RegexMatchError,
            pte.ExtractError,
            urllib.error.HTTPError,
            IndexError,   # raised from pytubefix internals on an empty response
            KeyError,
        ),
    )


def _is_definitive_failure(exc: Exception) -> bool:
    """True when the video cannot be downloaded by any client."""
    from pytubefix import exceptions as pte

    return isinstance(
        exc,
        (
            pte.VideoPrivate,
            pte.MembersOnly,
            pte.VideoRegionBlocked,
            pte.AgeRestrictedError,
            pte.AgeCheckRequiredError,
            pte.AgeCheckRequiredAccountError,
            pte.LiveStreamError,
            pte.LiveStreamOffline,
            pte.RecordingUnavailable,
        ),
    )


def _open_video(canonical: str, video_id: str, reporter: Optional[ProgressReporter]):
    """Return a live pytubefix ``YouTube`` for ``canonical``, trying each client.

    :raises VideoUnavailableError: if every client fails.
    """
    global _working_client
    from pytubefix import YouTube

    candidates = _client_candidates()
    last_error: Optional[Exception] = None

    for index, client in enumerate(candidates):
        try:
            yt = YouTube(canonical, client=client)
            title = yt.title  # first property access performs the network call
        except Exception as exc:
            if _is_definitive_failure(exc):
                raise _translate_pytubefix_error(exc, video_id) from exc
            if not _is_client_specific_failure(exc):
                raise _translate_pytubefix_error(exc, video_id) from exc

            last_error = exc
            if reporter and index + 1 < len(candidates):
                reporter.emit(
                    Phase.RESOLVING,
                    message=f"Retrying with a different YouTube client "
                            f"({candidates[index + 1]})",
                    force=True,
                )
            continue

        _working_client = client
        return yt, title

    raise _translate_pytubefix_error(
        last_error or VideoUnavailableError("No YouTube client could read this video."),
        video_id,
    ) from last_error


@dataclass(frozen=True)
class StreamOption:
    """One downloadable variant of a video."""

    kind: str                        # "video" or "audio"
    label: str                       # "1080p", "MP3 320k", "M4A (source quality)"
    itag: Optional[int] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None
    abr: Optional[str] = None
    mime_type: Optional[str] = None
    subtype: Optional[str] = None      # "mp4" or "webm" — determines the container
    codec: Optional[str] = None        # "avc1", "av01", "vp9", "opus", "mp4a"
    filesize: Optional[int] = None
    progressive: bool = False        # video and audio already in one stream
    needs_merge: bool = False        # requires ffmpeg to combine tracks
    needs_transcode: bool = False    # requires ffmpeg to change format
    recommended: bool = False        # drives the featured card in the UI

    @property
    def requires_ffmpeg(self) -> bool:
        return self.needs_merge or self.needs_transcode

    @property
    def filesize_display(self) -> str:
        return format_bytes(self.filesize) if self.filesize else "size unknown"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "itag": self.itag,
            "resolution": self.resolution,
            "fps": self.fps,
            "abr": self.abr,
            "mime_type": self.mime_type,
            "subtype": self.subtype,
            "codec": self.codec,
            "filesize": self.filesize,
            "filesize_display": self.filesize_display,
            "progressive": self.progressive,
            "needs_merge": self.needs_merge,
            "needs_transcode": self.needs_transcode,
            "requires_ffmpeg": self.requires_ffmpeg,
            "recommended": self.recommended,
        }


@dataclass
class VideoInfo:
    """Everything the interfaces need to render a video before downloading it."""

    video_id: str
    url: str
    title: str
    author: Optional[str] = None
    length_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    options: List[StreamOption] = field(default_factory=list)
    _yt: Any = field(default=None, repr=False, compare=False)

    @property
    def video_options(self) -> List[StreamOption]:
        return [o for o in self.options if o.kind == "video"]

    @property
    def audio_options(self) -> List[StreamOption]:
        return [o for o in self.options if o.kind == "audio"]

    def find(self, label: str) -> StreamOption:
        """Look up an option by label, case-insensitively."""
        wanted = label.strip().lower()
        for option in self.options:
            if option.label.lower() == wanted:
                return option
        # Accept a bare resolution such as "720p" for a "720p60" label.
        for option in self.options:
            if option.resolution and option.resolution.lower() == wanted:
                return option
        raise StreamNotFoundError(
            f"'{label}' is not an available option for this video.",
            hint="Available: " + ", ".join(o.label for o in self.options),
        )

    def as_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "length_seconds": self.length_seconds,
            "thumbnail_url": self.thumbnail_url,
            "options": [o.as_dict() for o in self.options],
        }


#: Video codec preference for a given resolution, most compatible first.
#: H.264 (avc1) plays everywhere; AV1 and VP9 are smaller but are refused by
#: older players, TVs and QuickTime. Since merging is a stream copy, whichever
#: codec is chosen here is the codec in the delivered file — so compatibility
#: is preferred over efficiency unless nothing else is offered.
_VIDEO_CODEC_PREFERENCE = ("avc1", "av01", "vp09", "vp9")

#: Audio containers that pair legally and playably with each video container.
_AUDIO_SUBTYPE_FOR_VIDEO = {"mp4": "mp4", "webm": "webm"}


def _stream_codec(stream) -> str:
    codecs = getattr(stream, "codecs", None) or []
    return (codecs[0] if codecs else "").split(".")[0].lower()


def _video_codec_rank(stream) -> tuple:
    """Sort key preferring MP4/H.264, so remuxed output stays widely playable."""
    codec = _stream_codec(stream)
    for index, known in enumerate(_VIDEO_CODEC_PREFERENCE):
        if codec.startswith(known):
            codec_rank = index
            break
    else:
        codec_rank = len(_VIDEO_CODEC_PREFERENCE)
    # MP4 first: an MP4 container accepts the AAC audio that plays everywhere.
    return (0 if getattr(stream, "subtype", "") == "mp4" else 1, codec_rank)


def _resolution_sort_key(resolution: Optional[str]) -> int:
    if not resolution:
        return 0
    try:
        return int(resolution.rstrip("p").split("p")[0])
    except (ValueError, AttributeError):
        return 0


def _translate_pytubefix_error(exc: Exception, video_id: str) -> Exception:
    """Map pytubefix's exception vocabulary onto ours, with usable wording."""
    import ssl
    import urllib.error

    from pytubefix import exceptions as pte

    # A TLS failure here is almost always local interception rather than
    # anything to do with the video, so answer the question the user actually
    # has instead of surfacing an SSL stack trace.
    ssl_error = exc if isinstance(exc, ssl.SSLError) else getattr(exc, "reason", None)
    if isinstance(exc, urllib.error.URLError) or isinstance(ssl_error, ssl.SSLError):
        from .net import diagnose

        result = diagnose()
        if not result.ok:
            return VideoUnavailableError(
                f"Could not reach YouTube securely. {result.detail}",
                hint=result.remedy,
            )

    if isinstance(exc, pte.VideoPrivate):
        return VideoUnavailableError("This video is private.")
    if isinstance(exc, pte.VideoRegionBlocked):
        return VideoUnavailableError("This video is blocked in your region.")
    if isinstance(exc, pte.MembersOnly):
        return VideoUnavailableError("This video is for channel members only.")
    if isinstance(exc, (pte.AgeRestrictedError, pte.AgeCheckRequiredError,
                        pte.AgeCheckRequiredAccountError)):
        return VideoUnavailableError(
            "This video is age-restricted and cannot be downloaded without signing in."
        )
    if isinstance(exc, (pte.LiveStreamError, pte.LiveStreamOffline, pte.RecordingUnavailable)):
        return VideoUnavailableError(
            "This is a livestream, which cannot be downloaded until it has finished."
        )
    if isinstance(exc, (pte.BotDetection, pte.PoTokenRequired, pte.LoginRequired)):
        return VideoUnavailableError(
            "YouTube is asking this request to prove it is not automated.",
            hint="Retrying later usually clears this. Frequent occurrences mean "
                 "pytubefix needs updating: pip install -U pytubefix",
        )
    if isinstance(exc, pte.VideoUnavailable):
        return VideoUnavailableError(f"Video {video_id} is unavailable.")
    if isinstance(exc, (pte.RegexMatchError, pte.ExtractError, pte.HTMLParseError,
                        pte.InterpretationError, pte.UnknownVideoError)):
        return VideoUnavailableError(
            "YouTube's page format has changed and this video could not be read.",
            hint="This usually means pytubefix is out of date: pip install -U pytubefix",
        )
    return exc


def fetch_info(
    url: str,
    *,
    reporter: Optional[ProgressReporter] = None,
    cancel: Optional[CancelToken] = None,
    include_filesize: bool = True,
) -> VideoInfo:
    """Resolve ``url`` to a :class:`VideoInfo` with all downloadable options.

    Performs exactly one metadata fetch. The originals called ``requests.get``
    on the watch page purely to look for the string "Video unavailable" — an
    extra request whose check pytubefix already performs properly.
    """
    from .net import enable_system_trust

    # Use the OS certificate verifier when available, so machines behind
    # antivirus or corporate TLS interception work without configuration.
    enable_system_trust()

    canonical = normalize_url(url)
    video_id = canonical.rsplit("=", 1)[-1]

    if reporter:
        reporter.emit(Phase.RESOLVING, message="Reading video details", force=True)
    if cancel:
        cancel.raise_if_cancelled()

    yt, title = _open_video(canonical, video_id, reporter)

    if cancel:
        cancel.raise_if_cancelled()
    if reporter:
        reporter.emit(Phase.RESOLVING, message="Listing available quality options", force=True)

    options: List[StreamOption] = []
    seen_resolutions: set[str] = set()

    def size_of(stream) -> Optional[int]:
        if not include_filesize:
            return None
        try:
            return stream.filesize_approx or stream.filesize
        except Exception:
            return None

    # Progressive streams first: they carry audio already, so they never need
    # ffmpeg and are preferred whenever a resolution is available both ways.
    for stream in yt.streams.filter(progressive=True).order_by("resolution").desc():
        if not stream.resolution or stream.resolution == "144p":
            continue
        if stream.resolution in seen_resolutions:
            continue
        seen_resolutions.add(stream.resolution)
        options.append(
            StreamOption(
                kind="video",
                label=stream.resolution,
                itag=stream.itag,
                resolution=stream.resolution,
                fps=getattr(stream, "fps", None),
                mime_type=stream.mime_type,
                subtype=getattr(stream, "subtype", None),
                codec=_stream_codec(stream),
                filesize=size_of(stream),
                progressive=True,
            )
        )

    # Adaptive video-only streams cover the higher resolutions, and need a merge.
    # YouTube offers the same resolution in several codecs; group by resolution
    # and keep the most compatible one, rather than whichever arrives first.
    by_resolution: dict[str, list] = {}
    for stream in yt.streams.filter(adaptive=True, only_video=True):
        if not stream.resolution or stream.resolution == "144p":
            continue
        if stream.resolution in seen_resolutions:
            continue
        by_resolution.setdefault(stream.resolution, []).append(stream)

    for resolution, candidates in by_resolution.items():
        stream = sorted(candidates, key=_video_codec_rank)[0]
        options.append(
            StreamOption(
                kind="video",
                label=resolution,
                itag=stream.itag,
                resolution=resolution,
                fps=getattr(stream, "fps", None),
                mime_type=stream.mime_type,
                subtype=getattr(stream, "subtype", None),
                codec=_stream_codec(stream),
                filesize=size_of(stream),
                progressive=False,
                needs_merge=True,
            )
        )

    options.sort(key=lambda o: _resolution_sort_key(o.resolution), reverse=True)

    # Audio: the source-quality M4A passthrough from YouTube-MP4-Downloader-Pro,
    # then the MP3 bitrate ladder from YouTube-To-Audio.
    best_audio = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    if best_audio is not None:
        options.append(
            StreamOption(
                kind="audio",
                label="M4A (source quality)",
                itag=best_audio.itag,
                abr=getattr(best_audio, "abr", None),
                mime_type=best_audio.mime_type,
                subtype=getattr(best_audio, "subtype", None),
                codec=_stream_codec(best_audio),
                filesize=size_of(best_audio),
            )
        )
        for bitrate, _description in MP3_BITRATES:
            options.append(
                StreamOption(
                    kind="audio",
                    label=f"MP3 {bitrate}",
                    abr=bitrate,
                    mime_type="audio/mpeg",
                    needs_transcode=True,
                    recommended=(bitrate == DEFAULT_MP3_BITRATE),
                )
            )

    if not options:
        raise StreamNotFoundError("YouTube returned no downloadable streams for this video.")

    # Mark the highest available video quality as the featured option.
    for option in options:
        if option.kind == "video":
            object.__setattr__(option, "recommended", True)
            break

    return VideoInfo(
        video_id=video_id,
        url=canonical,
        title=title,
        author=getattr(yt, "author", None),
        length_seconds=getattr(yt, "length", None),
        thumbnail_url=getattr(yt, "thumbnail_url", None),
        options=options,
        _yt=yt,
    )
