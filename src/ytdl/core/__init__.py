"""Interface-agnostic downloader core.

Nothing in this package prints, prompts, or blocks on user input. All feedback
travels through :class:`~ytdl.core.jobs.Progress` callbacks and all failures are
:class:`~ytdl.core.errors.YtdlError` subclasses, so the CLI, web and desktop
interfaces can each present the same operations in their own idiom.
"""

from .download import DownloadResult, default_output_dir, download
from .errors import (
    DownloadCancelledError,
    FFmpegError,
    FFmpegNotFoundError,
    InvalidURLError,
    OutputPathError,
    StreamNotFoundError,
    VideoUnavailableError,
    YtdlError,
)
from .ffmpeg import FFmpegStatus, is_available as ffmpeg_available, status as ffmpeg_status
from .jobs import (
    CancelToken,
    Phase,
    Progress,
    ProgressCallback,
    format_bytes,
    format_duration,
)
from .naming import sanitize_filename
from .net import TlsDiagnosis, diagnose as tls_diagnose, enable_system_trust
from .streams import (
    DEFAULT_MP3_BITRATE,
    MP3_BITRATES,
    StreamOption,
    VideoInfo,
    fetch_info,
)
from .urls import extract_video_id, is_valid_youtube_url, normalize_url

__all__ = [
    # operations
    "fetch_info",
    "download",
    "default_output_dir",
    # data
    "VideoInfo",
    "StreamOption",
    "DownloadResult",
    "Progress",
    "Phase",
    "CancelToken",
    "ProgressCallback",
    "FFmpegStatus",
    # helpers
    "normalize_url",
    "extract_video_id",
    "is_valid_youtube_url",
    "sanitize_filename",
    "format_bytes",
    "format_duration",
    "ffmpeg_available",
    "enable_system_trust",
    "tls_diagnose",
    "TlsDiagnosis",
    "ffmpeg_status",
    "MP3_BITRATES",
    "DEFAULT_MP3_BITRATE",
    # errors
    "YtdlError",
    "InvalidURLError",
    "VideoUnavailableError",
    "StreamNotFoundError",
    "FFmpegNotFoundError",
    "FFmpegError",
    "DownloadCancelledError",
    "OutputPathError",
]
