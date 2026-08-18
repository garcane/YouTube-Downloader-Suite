"""Progress reporting and cancellation — the contract between core and UI.

This module is the reason one core can serve three interfaces. Nothing in the
core prints or blocks on input; it emits :class:`Progress` snapshots through a
callback, and each interface renders them its own way:

* the CLI draws a bar on stderr,
* the web layer serialises them to JSON over Server-Sent Events,
* the desktop GUI marshals them onto the Tk thread with ``widget.after()``.

Callbacks are invoked on whatever thread is doing the work, which for the GUI
and web layers is a worker thread. Interfaces must not touch their widget
toolkit directly from the callback.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .errors import DownloadCancelledError


class Phase(str, Enum):
    """Coarse stage of a job. ``str`` mixin keeps it JSON-serialisable."""

    RESOLVING = "resolving"      # contacting YouTube, reading metadata and streams
    DOWNLOADING = "downloading"  # transferring bytes
    MUXING = "muxing"            # combining separate video and audio tracks
    CONVERTING = "converting"    # transcoding audio to the requested format
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (Phase.COMPLETE, Phase.FAILED, Phase.CANCELLED)


@dataclass(frozen=True)
class Progress:
    """An immutable snapshot of a job's state.

    ``fraction`` is ``None`` when the work is indeterminate — during stream
    resolution, or while ffmpeg runs without a known duration. Interfaces should
    show an indeterminate indicator rather than assuming zero.
    """

    phase: Phase
    message: str = ""
    fraction: Optional[float] = None
    bytes_done: int = 0
    bytes_total: Optional[int] = None
    speed_bps: Optional[float] = None
    eta_seconds: Optional[float] = None
    filename: Optional[str] = None

    @property
    def percent(self) -> Optional[float]:
        return None if self.fraction is None else round(self.fraction * 100, 1)

    def as_dict(self) -> dict:
        """JSON-ready form, used directly by the web layer's SSE stream."""
        return {
            "phase": self.phase.value,
            "message": self.message,
            "fraction": self.fraction,
            "percent": self.percent,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "speed_bps": self.speed_bps,
            "eta_seconds": self.eta_seconds,
            "filename": self.filename,
        }


ProgressCallback = Callable[[Progress], None]


class CancelToken:
    """Cooperative cancellation, safe to set from another thread.

    The core checks this between pipeline stages, passes it to pytubefix's
    ``interrupt_checker`` so an in-flight transfer stops between chunks, and
    hands it to ffmpeg runs so a long remux can be killed. Because
    ``interrupt_checker`` stops a download by returning rather than raising,
    the caller re-checks the token afterwards instead of trusting that a
    truncated file means success.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise DownloadCancelledError()


@dataclass
class ProgressReporter:
    """Throttles callbacks and derives transfer speed and ETA.

    A chunked download fires its callback far more often than any UI can paint.
    Updates are rate-limited to ``min_interval`` seconds, except for phase
    changes and terminal states, which are always delivered so no interface
    misses a transition.
    """

    callback: Optional[ProgressCallback] = None
    cancel_token: Optional[CancelToken] = None
    min_interval: float = 0.1

    _last_emit: float = field(default=0.0, init=False)
    _last_phase: Optional[Phase] = field(default=None, init=False)
    _started_at: float = field(default_factory=time.monotonic, init=False)
    _phase_started_at: float = field(default_factory=time.monotonic, init=False)
    _phase_start_bytes: int = field(default=0, init=False)

    def emit(
        self,
        phase: Phase,
        *,
        message: str = "",
        fraction: Optional[float] = None,
        bytes_done: int = 0,
        bytes_total: Optional[int] = None,
        filename: Optional[str] = None,
        force: bool = False,
    ) -> None:
        """Send a progress snapshot, honouring throttling and cancellation."""
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()

        now = time.monotonic()
        phase_changed = phase != self._last_phase

        if phase_changed:
            self._phase_started_at = now
            self._phase_start_bytes = bytes_done

        if not (force or phase_changed or phase.is_terminal):
            if now - self._last_emit < self.min_interval:
                return

        speed = eta = None
        if phase is Phase.DOWNLOADING and bytes_done > self._phase_start_bytes:
            elapsed = now - self._phase_started_at
            if elapsed > 0.25:  # ignore the noisy first fraction of a second
                speed = (bytes_done - self._phase_start_bytes) / elapsed
                if speed > 0 and bytes_total:
                    eta = max(0.0, (bytes_total - bytes_done) / speed)

        if fraction is None and bytes_total:
            fraction = min(1.0, bytes_done / bytes_total) if bytes_total > 0 else None

        self._last_emit = now
        self._last_phase = phase

        if self.callback is not None:
            self.callback(
                Progress(
                    phase=phase,
                    message=message,
                    fraction=fraction,
                    bytes_done=bytes_done,
                    bytes_total=bytes_total,
                    speed_bps=speed,
                    eta_seconds=eta,
                    filename=filename,
                )
            )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at


def format_bytes(n: Optional[float]) -> str:
    """Human-readable byte count, shared by all three interfaces."""
    if n is None:
        return "unknown"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < step or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} GB"  # pragma: no cover - unreachable


def format_duration(seconds: Optional[float]) -> str:
    """``143`` -> ``2:23``; ``None`` -> ``--:--``."""
    if seconds is None:
        return "--:--"
    seconds = int(max(0, seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
