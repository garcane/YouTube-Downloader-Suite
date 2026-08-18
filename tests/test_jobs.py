"""Progress reporting and cancellation semantics."""

import time

import pytest

from ytdl.core.errors import DownloadCancelledError
from ytdl.core.jobs import (
    CancelToken,
    Phase,
    Progress,
    ProgressReporter,
    format_bytes,
    format_duration,
)


class TestCancelToken:
    def test_starts_uncancelled(self):
        assert not CancelToken().cancelled

    def test_cancel_is_observable_and_raises(self):
        token = CancelToken()
        token.cancel()
        assert token.cancelled
        with pytest.raises(DownloadCancelledError):
            token.raise_if_cancelled()

    def test_raise_is_a_no_op_when_not_cancelled(self):
        CancelToken().raise_if_cancelled()


class TestProgressReporter:
    def test_emits_snapshots_to_the_callback(self):
        seen = []
        reporter = ProgressReporter(callback=seen.append)
        reporter.emit(Phase.DOWNLOADING, message="hello", bytes_done=5, bytes_total=10)

        assert len(seen) == 1
        assert seen[0].phase is Phase.DOWNLOADING
        assert seen[0].fraction == pytest.approx(0.5)
        assert seen[0].percent == 50.0

    def test_throttles_rapid_updates_within_one_phase(self):
        seen = []
        reporter = ProgressReporter(callback=seen.append, min_interval=5.0)
        for i in range(50):
            reporter.emit(Phase.DOWNLOADING, bytes_done=i, bytes_total=50)

        # First emit establishes the phase; the rest are suppressed.
        assert len(seen) == 1

    def test_phase_changes_are_never_throttled(self):
        seen = []
        reporter = ProgressReporter(callback=seen.append, min_interval=99.0)
        reporter.emit(Phase.RESOLVING)
        reporter.emit(Phase.DOWNLOADING)
        reporter.emit(Phase.MUXING)
        reporter.emit(Phase.COMPLETE)

        assert [p.phase for p in seen] == [
            Phase.RESOLVING, Phase.DOWNLOADING, Phase.MUXING, Phase.COMPLETE
        ]

    def test_terminal_phases_always_deliver(self):
        seen = []
        reporter = ProgressReporter(callback=seen.append, min_interval=99.0)
        reporter.emit(Phase.DOWNLOADING, bytes_done=1, bytes_total=2)
        reporter.emit(Phase.FAILED, message="boom")
        assert seen[-1].phase is Phase.FAILED

    def test_force_bypasses_throttling(self):
        seen = []
        reporter = ProgressReporter(callback=seen.append, min_interval=99.0)
        reporter.emit(Phase.DOWNLOADING, bytes_done=1, bytes_total=10)
        reporter.emit(Phase.DOWNLOADING, bytes_done=2, bytes_total=10, force=True)
        assert len(seen) == 2

    def test_cancellation_raises_from_emit(self):
        token = CancelToken()
        token.cancel()
        reporter = ProgressReporter(callback=lambda p: None, cancel_token=token)
        with pytest.raises(DownloadCancelledError):
            reporter.emit(Phase.DOWNLOADING)

    def test_indeterminate_progress_stays_none(self):
        seen = []
        reporter = ProgressReporter(callback=seen.append)
        reporter.emit(Phase.RESOLVING, message="working")
        assert seen[0].fraction is None
        assert seen[0].percent is None

    def test_speed_and_eta_appear_once_enough_time_has_passed(self):
        seen = []
        reporter = ProgressReporter(callback=seen.append, min_interval=0.0)
        reporter.emit(Phase.DOWNLOADING, bytes_done=0, bytes_total=1000)
        time.sleep(0.3)
        reporter.emit(Phase.DOWNLOADING, bytes_done=500, bytes_total=1000, force=True)

        assert seen[-1].speed_bps is not None and seen[-1].speed_bps > 0
        assert seen[-1].eta_seconds is not None

    def test_works_without_a_callback(self):
        ProgressReporter().emit(Phase.DOWNLOADING, bytes_done=1, bytes_total=2)


class TestSerialisation:
    def test_progress_is_json_ready(self):
        import json

        payload = Progress(phase=Phase.DOWNLOADING, message="x", fraction=0.25).as_dict()
        assert payload["phase"] == "downloading"
        assert payload["percent"] == 25.0
        json.dumps(payload)  # must not raise

    def test_phase_terminality(self):
        assert Phase.COMPLETE.is_terminal
        assert Phase.FAILED.is_terminal
        assert Phase.CANCELLED.is_terminal
        assert not Phase.DOWNLOADING.is_terminal


@pytest.mark.parametrize(
    "value,expected",
    [(None, "unknown"), (512, "512 B"), (2048, "2.0 KB"), (5 * 1024**2, "5.0 MB")],
)
def test_format_bytes(value, expected):
    assert format_bytes(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(None, "--:--"), (0, "0:00"), (59, "0:59"), (143, "2:23"), (3661, "1:01:01")],
)
def test_format_duration(value, expected):
    assert format_duration(value) == expected


class TestOutputDirectory:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        from ytdl.core.download import ENV_OUTPUT, default_output_dir

        monkeypatch.setenv(ENV_OUTPUT, str(tmp_path))
        assert default_output_dir() == tmp_path

    def test_falls_back_to_downloads_when_unset(self, monkeypatch):
        from pathlib import Path

        from ytdl.core.download import ENV_OUTPUT, default_output_dir

        monkeypatch.delenv(ENV_OUTPUT, raising=False)
        result = default_output_dir()
        assert result == Path.home() / "Downloads" or result == Path.home()

    def test_blank_override_is_ignored(self, monkeypatch):
        from pathlib import Path

        from ytdl.core.download import ENV_OUTPUT, default_output_dir

        monkeypatch.setenv(ENV_OUTPUT, "   ")
        assert default_output_dir() != Path("   ")
