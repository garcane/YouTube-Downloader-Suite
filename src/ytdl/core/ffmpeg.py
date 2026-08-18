"""Locating and driving ffmpeg.

This replaces two different approaches inherited from the source repositories:

* YouTube-To-Audio downloaded a Windows-only ffmpeg build from a GitHub release
  at import time and moved ``ffmpeg.exe``, ``ffprobe.exe`` and ``ffplay.exe``
  into the current working directory — which made the tool Windows-only, wrote
  binaries into whatever folder the user happened to be in, and hit the network
  before doing anything else.
* YouTube-MP4-Downloader-Pro pulled in MoviePy, which re-encoded every merged
  download through libx264 even though the streams were already H.264.

Here ffmpeg is *located*, never installed, and invoked directly. Merging becomes
a stream copy, which is lossless and typically an order of magnitude faster than
the re-encode it replaces.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .errors import FFmpegError, FFmpegNotFoundError
from .jobs import CancelToken, Phase, ProgressReporter

#: Environment variable holding an explicit path to the ffmpeg binary.
ENV_FFMPEG = "YTDL_FFMPEG"
ENV_FFPROBE = "YTDL_FFPROBE"

_cached_ffmpeg: Optional[Path] = None
_cached_ffprobe: Optional[Path] = None


def _no_window_kwargs() -> dict:
    """Suppress the console window ffmpeg would otherwise flash on Windows.

    Without this, every subprocess call from the desktop GUI pops a black
    console for the duration of the run.
    """
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {
            "startupinfo": startupinfo,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }
    return {}


def _probe_candidate(path: str | Path) -> Optional[Path]:
    """Return ``path`` if it is an ffmpeg-like binary that actually runs."""
    try:
        result = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            timeout=10,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(path) if result.returncode == 0 else None


def find_ffmpeg(explicit: str | Path | None = None, *, refresh: bool = False) -> Path:
    """Locate an ffmpeg binary, in order of preference.

    1. the ``explicit`` argument,
    2. the ``YTDL_FFMPEG`` environment variable,
    3. ``ffmpeg`` on ``PATH``,
    4. the binary bundled with ``imageio-ffmpeg``, if installed.

    :raises FFmpegNotFoundError: when none of the above yields a working binary.
    """
    global _cached_ffmpeg

    if explicit:
        found = _probe_candidate(explicit)
        if found is None:
            raise FFmpegNotFoundError(f"No usable ffmpeg at '{explicit}'.")
        return found

    if _cached_ffmpeg is not None and not refresh:
        return _cached_ffmpeg

    from_env = os.environ.get(ENV_FFMPEG)
    if from_env:
        found = _probe_candidate(from_env)
        if found is None:
            raise FFmpegNotFoundError(
                f"{ENV_FFMPEG} is set to '{from_env}', but that is not a working ffmpeg."
            )
        _cached_ffmpeg = found
        return found

    on_path = shutil.which("ffmpeg")
    if on_path:
        found = _probe_candidate(on_path)
        if found is not None:
            _cached_ffmpeg = found
            return found

    try:
        import imageio_ffmpeg

        found = _probe_candidate(imageio_ffmpeg.get_ffmpeg_exe())
        if found is not None:
            _cached_ffmpeg = found
            return found
    except Exception:  # pragma: no cover - optional dependency absent
        pass

    raise FFmpegNotFoundError()


def find_ffprobe(*, refresh: bool = False) -> Optional[Path]:
    """Locate ffprobe. Returns ``None`` rather than raising — it is optional.

    ffprobe is used only to read media duration for progress percentages; every
    operation still works without it, just with an indeterminate progress bar.
    """
    global _cached_ffprobe

    if _cached_ffprobe is not None and not refresh:
        return _cached_ffprobe

    candidates = [os.environ.get(ENV_FFPROBE), shutil.which("ffprobe")]

    try:  # ffprobe usually sits beside ffmpeg
        candidates.append(str(find_ffmpeg().parent / "ffprobe"))
    except FFmpegNotFoundError:
        pass

    for candidate in candidates:
        if candidate:
            found = _probe_candidate(candidate)
            if found is not None:
                _cached_ffprobe = found
                return found
    return None


def is_available() -> bool:
    """True when ffmpeg can be resolved. For UI capability checks."""
    try:
        find_ffmpeg()
    except FFmpegNotFoundError:
        return False
    return True


@dataclass(frozen=True)
class FFmpegStatus:
    """Snapshot of ffmpeg availability, surfaced in every interface's footer."""

    available: bool
    path: Optional[str] = None
    version: Optional[str] = None
    ffprobe: Optional[str] = None
    message: str = ""


def status() -> FFmpegStatus:
    """Describe the current ffmpeg situation without raising."""
    try:
        binary = find_ffmpeg()
    except FFmpegNotFoundError as exc:
        return FFmpegStatus(available=False, message=str(exc))

    version = None
    try:
        result = subprocess.run(
            [str(binary), "-version"], capture_output=True, text=True,
            timeout=10, **_no_window_kwargs(),
        )
        if result.stdout:
            version = result.stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pass

    probe = find_ffprobe()
    return FFmpegStatus(
        available=True,
        path=str(binary),
        version=version,
        ffprobe=str(probe) if probe else None,
        message="ffmpeg ready",
    )


def probe_duration(path: str | Path) -> Optional[float]:
    """Return media duration in seconds, or ``None`` if it cannot be determined."""
    probe = find_ffprobe()
    if probe is None:
        return None
    try:
        result = subprocess.run(
            [
                str(probe), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=30, **_no_window_kwargs(),
        )
        if result.returncode != 0:
            return None
        return float(json.loads(result.stdout)["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return None


def run_ffmpeg(
    args: Sequence[str],
    *,
    total_duration: Optional[float] = None,
    reporter: Optional[ProgressReporter] = None,
    phase: Phase = Phase.CONVERTING,
    message: str = "",
    cancel: Optional[CancelToken] = None,
) -> None:
    """Run ffmpeg with ``args``, reporting progress and honouring cancellation.

    ``-progress pipe:1`` makes ffmpeg emit machine-readable key=value lines; the
    ``out_time_us`` field is compared against ``total_duration`` to derive a
    percentage. Without a known duration the reporter emits an indeterminate
    update instead, which the interfaces render as a spinner.

    :raises FFmpegError: on a non-zero exit, carrying ffmpeg's own stderr.
    :raises DownloadCancelledError: if ``cancel`` is set while running.
    """
    binary = find_ffmpeg()
    command = [
        str(binary),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        *args,
        "-progress", "pipe:1",
        "-nostats",
    ]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **_no_window_kwargs(),
    )

    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancel is not None and cancel.cancelled:
                process.kill()
                process.wait(timeout=5)
                cancel.raise_if_cancelled()

            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and reporter is not None:
                fraction = None
                if total_duration and total_duration > 0:
                    try:
                        fraction = min(1.0, (int(value) / 1_000_000) / total_duration)
                    except ValueError:
                        fraction = None
                reporter.emit(phase, message=message, fraction=fraction)
    finally:
        if process.poll() is None:  # pragma: no cover - cancellation path
            process.kill()
        stderr = process.stderr.read() if process.stderr else ""
        process.wait()

    if process.returncode != 0:
        raise FFmpegError(
            "ffmpeg could not process the downloaded file.",
            stderr=stderr.strip(),
            returncode=process.returncode,
        )


def merge_streams(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    reporter: Optional[ProgressReporter] = None,
    cancel: Optional[CancelToken] = None,
) -> Path:
    """Combine separate video and audio tracks without re-encoding.

    ``-c copy`` remuxes: the compressed streams are placed into a new container
    untouched. This is lossless and typically 10–50× faster than the MoviePy
    libx264 re-encode it replaces. ``+faststart`` moves the index to the front
    so the result plays immediately in a browser.
    """
    run_ffmpeg(
        [
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-movflags", "+faststart",
            str(output_path),
        ],
        total_duration=probe_duration(video_path),
        reporter=reporter,
        phase=Phase.MUXING,
        message="Combining video and audio",
        cancel=cancel,
    )
    return output_path


def transcode_audio(
    source_path: Path,
    output_path: Path,
    *,
    bitrate: str = "320k",
    sample_rate: int = 48000,
    reporter: Optional[ProgressReporter] = None,
    cancel: Optional[CancelToken] = None,
) -> Path:
    """Convert a downloaded audio track to MP3.

    Defaults preserve YouTube-To-Audio's behaviour: 320 kbps at 48 kHz.
    """
    run_ffmpeg(
        [
            "-i", str(source_path),
            "-vn",
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            "-ar", str(sample_rate),
            str(output_path),
        ],
        total_duration=probe_duration(source_path),
        reporter=reporter,
        phase=Phase.CONVERTING,
        message=f"Converting to MP3 at {bitrate}",
        cancel=cancel,
    )
    return output_path
