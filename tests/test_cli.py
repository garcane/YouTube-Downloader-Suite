"""CLI behaviour: argument handling, output discipline, terminal safety."""

import io
import sys

import pytest

from ytdl.cli import ProgressBar, _Glyphs, _supports, build_parser, main
from ytdl.core.errors import InvalidURLError, VideoUnavailableError
from ytdl.core.jobs import Phase, Progress


class FakeStream:
    """Text sink with a settable ``encoding`` — StringIO's is read-only."""

    def __init__(self, encoding="utf-8", tty=False):
        self._buffer = io.StringIO()
        self.encoding = encoding
        self._tty = tty

    def write(self, text):
        return self._buffer.write(text)

    def flush(self):
        pass

    def isatty(self):
        return self._tty

    def getvalue(self):
        return self._buffer.getvalue()


class TestTerminalSafety:
    """The Windows console defaults to cp1252 and cannot encode box drawing."""

    def test_cp1252_falls_back_to_ascii(self):
        glyphs = _Glyphs(FakeStream(encoding="cp1252"))
        assert glyphs.star == "*"
        assert glyphs.bar_full == "#"
        assert glyphs.ellipsis == "..."

    def test_utf8_keeps_the_nicer_glyphs(self):
        glyphs = _Glyphs(FakeStream(encoding="utf-8"))
        assert glyphs.star == "★"
        assert glyphs.bar_full == "█"

    def test_ascii_only_encoding_falls_back(self):
        assert not _supports(FakeStream(encoding="ascii"), "★")
        assert _supports(FakeStream(encoding="utf-8"), "★")

    def test_unknown_encoding_is_treated_as_unsafe(self):
        assert not _supports(FakeStream(encoding="not-a-real-codec"), "★")

    def test_progress_bar_renders_without_raising_on_cp1252(self):
        stream = FakeStream(encoding="cp1252", tty=True)
        bar = ProgressBar(stream=stream)
        bar(Progress(phase=Phase.DOWNLOADING, message="x", fraction=0.5,
                     bytes_done=50, bytes_total=100))
        rendered = stream.getvalue()
        rendered.encode("cp1252")  # must not raise
        assert "#" in rendered


class TestProgressBar:
    def test_non_interactive_prints_one_line_per_phase(self):
        stream = FakeStream(tty=False)
        bar = ProgressBar(stream=stream)
        for _ in range(5):
            bar(Progress(phase=Phase.DOWNLOADING, message="Downloading", fraction=0.1))
        bar(Progress(phase=Phase.MUXING, message="Combining"))

        lines = [l for l in stream.getvalue().splitlines() if l]
        assert lines == ["downloading: Downloading", "muxing: Combining"]

    def test_no_carriage_returns_when_redirected(self):
        stream = FakeStream(tty=False)
        bar = ProgressBar(stream=stream)
        bar(Progress(phase=Phase.DOWNLOADING, fraction=0.5))
        assert "\r" not in stream.getvalue()

    def test_quiet_suppresses_everything(self):
        stream = FakeStream(tty=True)
        bar = ProgressBar(stream=stream, quiet=True)
        bar(Progress(phase=Phase.DOWNLOADING, fraction=0.5))
        assert stream.getvalue() == ""

    def test_indeterminate_progress_is_rendered(self):
        stream = FakeStream(tty=True)
        bar = ProgressBar(stream=stream)
        bar(Progress(phase=Phase.RESOLVING, message="Reading video details"))
        assert "Reading video details" in stream.getvalue()


class TestArgumentParsing:
    def test_quality_and_output(self):
        args = build_parser().parse_args(["URL", "-q", "1080p", "-o", "/tmp"])
        assert args.quality == "1080p" and args.output == "/tmp"

    def test_mp3_bitrate_choices_are_constrained(self):
        args = build_parser().parse_args(["URL", "--mp3", "-b", "320k"])
        assert args.mp3 and args.bitrate == "320k"

        with pytest.raises(SystemExit):
            build_parser().parse_args(["URL", "--mp3", "-b", "999k"])

    def test_defaults(self):
        args = build_parser().parse_args(["URL"])
        assert args.bitrate == "320k"
        assert not args.mp3 and not args.audio and not args.list_only


class TestMain:
    def test_no_arguments_prints_help_and_fails(self, capsys):
        assert main([]) == 2
        assert "usage" in capsys.readouterr().err.lower()

    def test_invalid_url_reports_cleanly_without_a_traceback(self, capsys, monkeypatch):
        def boom(*a, **k):
            raise InvalidURLError("That is not a YouTube link.")

        monkeypatch.setattr("ytdl.cli.fetch_info", boom)
        assert main(["https://example.com"]) == 1

        captured = capsys.readouterr()
        assert "not a YouTube link" in captured.err
        assert "Traceback" not in captured.err
        assert captured.out == ""  # nothing on stdout when it fails

    def test_hint_is_shown_with_the_error(self, capsys, monkeypatch):
        def boom(*a, **k):
            raise VideoUnavailableError("Blocked.", hint="Try again later.")

        monkeypatch.setattr("ytdl.cli.fetch_info", boom)
        main(["https://youtu.be/dQw4w9WgXcQ"])
        assert "Try again later." in capsys.readouterr().err

    def test_successful_download_prints_only_the_path_to_stdout(self, capsys, monkeypatch, tmp_path):
        from ytdl.core.download import DownloadResult

        target = tmp_path / "Video.mp4"
        target.write_bytes(b"x")

        class FakeInfo:
            title = "Video"
            options = []
            audio_options = []

        monkeypatch.setattr("ytdl.cli.fetch_info", lambda *a, **k: FakeInfo())
        monkeypatch.setattr(
            "ytdl.cli.download",
            lambda *a, **k: DownloadResult(
                path=target, title="Video", option_label="720p",
                filesize=1, video_id="x",
            ),
        )

        assert main(["https://youtu.be/dQw4w9WgXcQ", "-q", "720p"]) == 0

        captured = capsys.readouterr()
        assert captured.out.strip() == str(target)   # stdout is the path alone
        assert "Video" in captured.err               # human output on stderr

    def test_json_output_is_valid(self, capsys, monkeypatch, tmp_path):
        import json

        from ytdl.core.download import DownloadResult

        target = tmp_path / "Video.mp4"
        target.write_bytes(b"x")

        class FakeInfo:
            title = "Video"
            options = []
            audio_options = []

        monkeypatch.setattr("ytdl.cli.fetch_info", lambda *a, **k: FakeInfo())
        monkeypatch.setattr(
            "ytdl.cli.download",
            lambda *a, **k: DownloadResult(
                path=target, title="Video", option_label="720p", filesize=1, video_id="x"
            ),
        )
        main(["https://youtu.be/dQw4w9WgXcQ", "-q", "720p", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["title"] == "Video"

    def test_doctor_runs_and_returns_a_status_code(self, capsys):
        code = main(["doctor"])
        out = capsys.readouterr().out
        assert "ytdl doctor" in out
        assert "ffmpeg" in out
        assert code in (0, 1)
