"""Download orchestration — the single entry point every interface calls.

One function, :func:`download`, covers everything the two original scripts did
between them:

============================  ==========================================
Request                       Pipeline
============================  ==========================================
progressive video (≤720p)     download
adaptive video (1080p+)       download video → download audio → remux
M4A audio                     download
MP3 audio                     download → transcode
============================  ==========================================

Intermediate files are written to a temporary directory and moved into place
only on success, so an interrupted download never leaves a half-written file
next to the user's finished ones — a failure mode both originals had.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import ffmpeg as ffmpeg_mod
from .errors import (
    DownloadCancelledError,
    OutputPathError,
    StreamNotFoundError,
    YtdlError,
)
from .jobs import CancelToken, Phase, ProgressCallback, ProgressReporter
from .naming import sanitize_filename, unique_path
from .streams import DEFAULT_MP3_BITRATE, StreamOption, VideoInfo, fetch_info


def default_output_dir() -> Path:
    """Where downloads land when the caller does not say.

    Both originals used ``os.getcwd()``, which put files wherever the terminal
    happened to be — and is meaningless for a web server or a GUI. The user's
    Downloads folder is the least surprising default.
    """
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


def _prepare_output_dir(output_dir: str | Path | None) -> Path:
    directory = Path(output_dir).expanduser() if output_dir else default_output_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputPathError(f"Cannot create output folder '{directory}': {exc}") from exc
    if not directory.is_dir():
        raise OutputPathError(f"'{directory}' is not a folder.")
    return directory


@dataclass(frozen=True)
class DownloadResult:
    """What the caller gets back on success."""

    path: Path
    title: str
    option_label: str
    filesize: int
    video_id: str

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "filename": self.path.name,
            "title": self.title,
            "option": self.option_label,
            "filesize": self.filesize,
            "video_id": self.video_id,
        }


class _Stage:
    """Maps one pipeline step's 0–1 progress onto a slice of the overall bar.

    Without this, a 1080p download shows the bar filling three separate times.
    """

    def __init__(self, reporter: ProgressReporter, base: float, span: float):
        self.reporter = reporter
        self.base = base
        self.span = span

    def emit(self, phase: Phase, *, local_fraction: Optional[float], **kwargs) -> None:
        overall = None if local_fraction is None else self.base + self.span * local_fraction
        self.reporter.emit(phase, fraction=overall, **kwargs)


def _download_stream(
    stream,
    destination: Path,
    stage: _Stage,
    *,
    message: str,
    cancel: Optional[CancelToken],
) -> Path:
    """Fetch one pytubefix stream to ``destination`` with progress and cancellation."""
    total = None
    try:
        total = stream.filesize or stream.filesize_approx
    except Exception:
        total = None

    def on_progress(_stream, _chunk, bytes_remaining):
        done = (total - bytes_remaining) if total else 0
        stage.emit(
            Phase.DOWNLOADING,
            local_fraction=(done / total) if total else None,
            message=message,
            bytes_done=done,
            bytes_total=total,
            filename=destination.name,
        )

    yt = stream._monostate  # pytubefix routes callbacks through the shared monostate
    previous = getattr(yt, "on_progress", None)
    yt.on_progress = on_progress

    try:
        stream.download(
            output_path=str(destination.parent),
            filename=destination.name,
            skip_existing=False,
            max_retries=2,
            interrupt_checker=(lambda: cancel.cancelled) if cancel else None,
        )
    finally:
        yt.on_progress = previous

    # interrupt_checker stops the transfer without raising, so confirm the state
    # ourselves rather than treating a truncated file as a success.
    if cancel is not None and cancel.cancelled:
        raise DownloadCancelledError()
    if not destination.exists():
        raise YtdlError("The download did not produce a file.")

    return destination


def _pick_stream(info: VideoInfo, option: StreamOption):
    """Resolve a :class:`StreamOption` back to a live pytubefix stream."""
    yt = info._yt
    if option.itag is not None:
        stream = yt.streams.get_by_itag(option.itag)
        if stream is not None:
            return stream
    if option.kind == "audio":
        stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
        if stream is not None:
            return stream
    raise StreamNotFoundError(f"The '{option.label}' stream is no longer available.")


def _best_audio_stream(info: VideoInfo, *, prefer_subtype: Optional[str] = None):
    """Highest-bitrate audio, preferring one that matches the video's container.

    Container matching matters: YouTube's best audio is usually Opus in WebM,
    but Opus inside an MP4 is refused by QuickTime, older Windows players and
    most TVs. Since merging is a stream copy, pairing an MP4 video with WebM
    audio produces a technically valid file that many players will not open.
    Matching containers keeps the remux lossless *and* the output playable.
    """
    streams = info._yt.streams.filter(only_audio=True)

    if prefer_subtype:
        matching = streams.filter(subtype=prefer_subtype).order_by("abr").desc().first()
        if matching is not None:
            return matching

    stream = streams.order_by("abr").desc().first()
    if stream is None:
        raise StreamNotFoundError("This video has no audio track to download.")
    return stream


def download(
    url: str,
    option: str | StreamOption,
    *,
    output_dir: str | Path | None = None,
    on_progress: Optional[ProgressCallback] = None,
    cancel: Optional[CancelToken] = None,
    info: Optional[VideoInfo] = None,
    mp3_bitrate: str = DEFAULT_MP3_BITRATE,
) -> DownloadResult:
    """Download ``url`` at the quality named by ``option``.

    :param option: a label such as ``"1080p"``, ``"M4A (source quality)"`` or
        ``"MP3 320k"``, or a :class:`StreamOption` from a previous
        :func:`~ytdl.core.streams.fetch_info` call.
    :param info: reuse an already-fetched :class:`VideoInfo` to avoid a second
        network round trip — the web and GUI layers list options first, then
        download, and should pass what they already have.
    :raises YtdlError: any failure; every error type is a subclass.
    """
    reporter = ProgressReporter(callback=on_progress, cancel_token=cancel)
    directory = _prepare_output_dir(output_dir)

    if info is None:
        info = fetch_info(url, reporter=reporter, cancel=cancel)

    chosen = info.find(option) if isinstance(option, str) else option

    if chosen.requires_ffmpeg and not ffmpeg_mod.is_available():
        ffmpeg_mod.find_ffmpeg()  # raises FFmpegNotFoundError with install guidance

    stem = sanitize_filename(info.title)

    try:
        if chosen.kind == "audio":
            result_path = _run_audio_pipeline(
                info, chosen, directory, stem, reporter, cancel, mp3_bitrate
            )
        else:
            result_path = _run_video_pipeline(info, chosen, directory, stem, reporter, cancel)
    except DownloadCancelledError:
        reporter.emit(Phase.CANCELLED, message="Cancelled", force=True)
        raise
    except YtdlError as exc:
        reporter.emit(Phase.FAILED, message=exc.user_message, force=True)
        raise

    reporter.emit(
        Phase.COMPLETE,
        message="Done",
        fraction=1.0,
        filename=result_path.name,
        force=True,
    )
    return DownloadResult(
        path=result_path,
        title=info.title,
        option_label=chosen.label,
        filesize=result_path.stat().st_size,
        video_id=info.video_id,
    )


def _run_video_pipeline(
    info: VideoInfo,
    option: StreamOption,
    directory: Path,
    stem: str,
    reporter: ProgressReporter,
    cancel: Optional[CancelToken],
) -> Path:
    stream = _pick_stream(info, option)

    # The container follows the video stream: an MP4 video stays in .mp4, a
    # VP9/WebM video is written as .webm rather than being forced into an MP4
    # box it does not belong in.
    subtype = getattr(stream, "subtype", None) or option.subtype or "mp4"
    extension = ".webm" if subtype == "webm" else ".mp4"
    final_path = unique_path(directory, stem, extension)

    if option.progressive:
        with tempfile.TemporaryDirectory(prefix="ytdl-") as tmp:
            staged = Path(tmp) / f"{stem}{extension}"
            _download_stream(
                stream, staged, _Stage(reporter, 0.0, 1.0),
                message=f"Downloading {option.label}", cancel=cancel,
            )
            shutil.move(str(staged), str(final_path))
        return final_path

    # Adaptive: video-only plus audio, then remux. Weights reflect that video
    # dominates transfer time and the stream copy is comparatively quick.
    audio_stream = _best_audio_stream(info, prefer_subtype=subtype)
    audio_extension = ".webm" if getattr(audio_stream, "subtype", None) == "webm" else ".m4a"

    with tempfile.TemporaryDirectory(prefix="ytdl-") as tmp:
        tmp_dir = Path(tmp)
        video_part = tmp_dir / f"video{extension}"
        audio_part = tmp_dir / f"audio{audio_extension}"
        merged = tmp_dir / f"merged{extension}"

        _download_stream(
            stream, video_part, _Stage(reporter, 0.0, 0.65),
            message=f"Downloading video ({option.label})", cancel=cancel,
        )
        _download_stream(
            audio_stream, audio_part, _Stage(reporter, 0.65, 0.15),
            message="Downloading audio track", cancel=cancel,
        )

        if cancel:
            cancel.raise_if_cancelled()

        stage = _Stage(reporter, 0.80, 0.20)
        ffmpeg_mod.merge_streams(
            video_part, audio_part, merged,
            reporter=ProgressReporter(
                callback=lambda p: stage.emit(
                    Phase.MUXING, local_fraction=p.fraction, message=p.message
                ),
                cancel_token=cancel,
            ),
            cancel=cancel,
        )
        shutil.move(str(merged), str(final_path))

    return final_path


def _run_audio_pipeline(
    info: VideoInfo,
    option: StreamOption,
    directory: Path,
    stem: str,
    reporter: ProgressReporter,
    cancel: Optional[CancelToken],
    mp3_bitrate: str,
) -> Path:
    stream = _best_audio_stream(info)

    if not option.needs_transcode:
        final_path = unique_path(directory, stem, ".m4a")
        with tempfile.TemporaryDirectory(prefix="ytdl-") as tmp:
            staged = Path(tmp) / f"{stem}.m4a"
            _download_stream(
                stream, staged, _Stage(reporter, 0.0, 1.0),
                message="Downloading audio", cancel=cancel,
            )
            shutil.move(str(staged), str(final_path))
        return final_path

    bitrate = option.abr or mp3_bitrate
    final_path = unique_path(directory, stem, ".mp3")

    with tempfile.TemporaryDirectory(prefix="ytdl-") as tmp:
        tmp_dir = Path(tmp)
        source = tmp_dir / "source.m4a"
        converted = tmp_dir / "converted.mp3"

        _download_stream(
            stream, source, _Stage(reporter, 0.0, 0.70),
            message="Downloading audio", cancel=cancel,
        )

        if cancel:
            cancel.raise_if_cancelled()

        stage = _Stage(reporter, 0.70, 0.30)
        ffmpeg_mod.transcode_audio(
            source, converted,
            bitrate=bitrate,
            reporter=ProgressReporter(
                callback=lambda p: stage.emit(
                    Phase.CONVERTING, local_fraction=p.fraction, message=p.message
                ),
                cancel_token=cancel,
            ),
            cancel=cancel,
        )
        shutil.move(str(converted), str(final_path))

    return final_path
