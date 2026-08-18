"""Command-line interface.

Replaces the interactive ``input()`` loops both original scripts were built
around, while keeping that workflow available: running with a URL and no
quality flag still presents a numbered menu, so existing muscle memory works.
Supplying flags makes the tool scriptable, which neither original could be.

Output discipline: progress and status go to stderr, and the final file path
goes to stdout on its own line. ``ytdl URL -q 720p > path.txt`` therefore
captures just the path, and piping does not interleave with the progress bar.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from .. import __version__
from ..core import (
    CancelToken,
    DownloadCancelledError,
    Phase,
    Progress,
    YtdlError,
    default_output_dir,
    download,
    fetch_info,
    ffmpeg_status,
    format_bytes,
    format_duration,
)
from ..core.streams import DEFAULT_MP3_BITRATE, MP3_BITRATES, VideoInfo


def _use_colour(stream) -> bool:
    """Respect NO_COLOR, dumb terminals, and redirection."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def _supports(stream, glyphs: str) -> bool:
    """Can ``stream``'s encoding actually represent ``glyphs``?

    The Windows console still defaults to cp1252, which cannot encode the box
    drawing and star characters this interface would otherwise use. Printing
    them raises UnicodeEncodeError and kills the command, so the glyph set is
    chosen from what the terminal can render rather than assumed to be UTF-8.
    """
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        glyphs.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


class _Glyphs:
    """Terminal-safe symbols, degrading to ASCII where necessary."""

    UNICODE = {"bar_full": "█", "bar_empty": "░", "star": "★", "tick": "✓",
               "cross": "✗", "dot": "·", "ellipsis": "…"}
    ASCII = {"bar_full": "#", "bar_empty": "-", "star": "*", "tick": "+",
             "cross": "x", "dot": "-", "ellipsis": "..."}

    def __init__(self, stream):
        source = self.UNICODE if _supports(stream, "".join(self.UNICODE.values())) else self.ASCII
        self.__dict__.update(source)


class _Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t): return self._wrap("1", t)
    def dim(self, t): return self._wrap("2", t)
    def green(self, t): return self._wrap("32", t)
    def red(self, t): return self._wrap("31", t)
    def yellow(self, t): return self._wrap("33", t)
    def cyan(self, t): return self._wrap("36", t)


class ProgressBar:
    """Single-line progress rendered on stderr, quiet when not a terminal."""

    WIDTH = 28

    def __init__(self, stream=sys.stderr, quiet: bool = False):
        self.stream = stream
        self.interactive = hasattr(stream, "isatty") and stream.isatty()
        self.quiet = quiet
        self.style = _Style(_use_colour(stream))
        self.glyphs = _Glyphs(stream)
        self._last_phase: Optional[Phase] = None
        self._dirty = False

    def __call__(self, progress: Progress) -> None:
        if self.quiet:
            return

        if not self.interactive:
            # Piped or redirected: one line per phase change, no carriage returns.
            if progress.phase != self._last_phase:
                self._last_phase = progress.phase
                print(f"{progress.phase.value}: {progress.message}", file=self.stream)
            return

        if progress.phase.is_terminal:
            self._clear()
            return

        if progress.fraction is None:
            body = f"{progress.message or progress.phase.value}{self.glyphs.ellipsis}"
        else:
            filled = int(self.WIDTH * progress.fraction)
            bar = self.glyphs.bar_full * filled + self.glyphs.bar_empty * (self.WIDTH - filled)
            parts = [f"{bar} {progress.percent:5.1f}%"]
            if progress.bytes_total:
                parts.append(
                    f"{format_bytes(progress.bytes_done)}/{format_bytes(progress.bytes_total)}"
                )
            if progress.speed_bps:
                parts.append(f"{format_bytes(progress.speed_bps)}/s")
            if progress.eta_seconds is not None:
                parts.append(f"ETA {format_duration(progress.eta_seconds)}")
            body = f"{progress.message or progress.phase.value}  " + "  ".join(parts)

        self._dirty = True
        self.stream.write("\r\033[K" + self.style.cyan(body))
        self.stream.flush()

    def _clear(self) -> None:
        if self._dirty and self.interactive:
            self.stream.write("\r\033[K")
            self.stream.flush()
            self._dirty = False

    def finish(self) -> None:
        self._clear()


def _print_options(info: VideoInfo, style: _Style, stream=sys.stderr) -> None:
    glyphs = _Glyphs(stream)
    print(file=stream)
    print(style.bold(info.title), file=stream)
    meta = []
    if info.author:
        meta.append(info.author)
    if info.length_seconds:
        meta.append(format_duration(info.length_seconds))
    if meta:
        separator = f"  {glyphs.dot}  "
        print(style.dim("  " + separator.join(meta)), file=stream)
    print(file=stream)

    for index, option in enumerate(info.options, start=1):
        marker = style.green(" " + glyphs.star) if option.recommended else "  "
        notes = []
        if option.needs_merge:
            notes.append("merge")
        if option.needs_transcode:
            notes.append("convert")
        if option.codec:
            notes.append(option.codec)
        suffix = style.dim(f"  [{', '.join(notes)}]") if notes else ""
        size = style.dim(f"{option.filesize_display:>12}")
        print(f"{marker} {index:2}. {option.label:<24} {size}{suffix}", file=stream)
    print(file=stream)


def _prompt_for_option(info: VideoInfo, style: _Style) -> str:
    """Numbered menu, mirroring how both original scripts asked."""
    _print_options(info, style)
    default = next((o for o in info.options if o.recommended), info.options[0])

    while True:
        try:
            raw = input(f"Choose 1-{len(info.options)} [{default.label}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            raise DownloadCancelledError()

        if not raw:
            return default.label
        if raw.isdigit() and 1 <= int(raw) <= len(info.options):
            return info.options[int(raw) - 1].label
        # Accept a label typed directly, as the original scripts did.
        try:
            return info.find(raw).label
        except YtdlError:
            print(style.red("  Not a valid choice."), file=sys.stderr)


def _cmd_doctor(style: _Style) -> int:
    """Report the environment problems that actually stop this tool working."""
    from ..core.net import diagnose, enable_system_trust

    print(style.bold("ytdl doctor"))
    print(f"  version        {__version__}")
    print(f"  python         {sys.version.split()[0]}")

    try:
        import pytubefix
        print(f"  pytubefix      {pytubefix.__version__}")
    except Exception:
        print(f"  pytubefix      {style.red('not installed')}")

    ff = ffmpeg_status()
    if ff.available:
        print(f"  ffmpeg         {style.green('found')}  {ff.path}")
        print(f"  ffprobe        {style.green('found') if ff.ffprobe else style.yellow('missing (progress will be indeterminate)')}")
    else:
        print(f"  ffmpeg         {style.red('missing')}")
        print(style.dim(f"                 {ff.message}"))

    used_system_trust = enable_system_trust()
    tls = diagnose()
    if tls.ok:
        print(f"  https          {style.green('ok')}  {tls.detail}")
    else:
        print(f"  https          {style.red('failing')}")
        print(style.dim(f"                 {tls.detail}"))
        if tls.remedy:
            for line in tls.remedy.splitlines():
                print(style.dim(f"                 {line}"))
    if tls.interceptor and tls.ok:
        print(style.dim(f"                 traffic is intercepted by {tls.interceptor}"))
    if not used_system_trust:
        print(style.dim("                 truststore not installed (pip install truststore)"))

    print(f"  downloads to   {default_output_dir()}")
    return 0 if (ff.available and tls.ok) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytdl",
        description="Download YouTube video or audio.",
        epilog=(
            "examples:\n"
            "  ytdl https://youtu.be/VIDEOID                 pick quality interactively\n"
            "  ytdl https://youtu.be/VIDEOID -q 1080p        download a specific quality\n"
            "  ytdl https://youtu.be/VIDEOID --mp3 -b 320k   extract audio as MP3\n"
            "  ytdl https://youtu.be/VIDEOID --list          show options and exit\n"
            "  ytdl doctor                                   check the environment\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="YouTube URL, or 'doctor' to run diagnostics")
    parser.add_argument("-q", "--quality", help="quality label, e.g. 1080p, 720p")
    parser.add_argument("-o", "--output", help="output folder (default: your Downloads folder)")

    audio = parser.add_argument_group("audio")
    audio.add_argument("--mp3", action="store_true", help="extract audio as MP3")
    audio.add_argument("--audio", action="store_true", help="extract audio in source quality (M4A)")
    audio.add_argument(
        "-b", "--bitrate", default=DEFAULT_MP3_BITRATE,
        choices=[b for b, _ in MP3_BITRATES],
        help=f"MP3 bitrate (default: {DEFAULT_MP3_BITRATE})",
    )

    parser.add_argument("-l", "--list", action="store_true", dest="list_only",
                        help="list available options and exit")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    parser.add_argument("--version", action="version", version=f"ytdl {__version__}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    style = _Style(_use_colour(sys.stderr))

    if args.url in ("doctor", "check"):
        return _cmd_doctor(style)

    if not args.url:
        parser.print_help(sys.stderr)
        return 2

    cancel = CancelToken()

    def handle_interrupt(_signum, _frame):
        cancel.cancel()
        print(file=sys.stderr)
        print(style.yellow("Cancelling…"), file=sys.stderr)

    signal.signal(signal.SIGINT, handle_interrupt)

    bar = ProgressBar(quiet=args.quiet)

    try:
        info = fetch_info(args.url, cancel=cancel)

        if args.list_only:
            bar.finish()
            if args.json:
                import json
                print(json.dumps(info.as_dict(), indent=2))
            else:
                _print_options(info, style, stream=sys.stdout)
            return 0

        if args.mp3:
            choice = f"MP3 {args.bitrate}"
        elif args.audio:
            choice = next(o.label for o in info.audio_options if not o.needs_transcode)
        elif args.quality:
            choice = args.quality
        else:
            choice = _prompt_for_option(info, style)

        result = download(
            args.url,
            choice,
            output_dir=args.output,
            on_progress=bar,
            cancel=cancel,
            info=info,
            mp3_bitrate=args.bitrate,
        )
        bar.finish()

        if args.json:
            import json
            print(json.dumps(result.as_dict(), indent=2))
        else:
            print(style.green(_Glyphs(sys.stderr).tick + " ") + f"{result.title}  "
                  + style.dim(f"({result.option_label}, {format_bytes(result.filesize)})"),
                  file=sys.stderr)
            print(result.path)  # stdout: the path alone, for scripting
        return 0

    except DownloadCancelledError:
        bar.finish()
        print(style.yellow("Cancelled."), file=sys.stderr)
        return 130
    except YtdlError as exc:
        bar.finish()
        print(style.red(_Glyphs(sys.stderr).cross + " ") + exc.user_message, file=sys.stderr)
        if exc.hint:
            for line in exc.hint.splitlines():
                print(style.dim("  " + line), file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - race with the signal handler
        bar.finish()
        return 130


__all__ = ["main", "build_parser", "ProgressBar"]
