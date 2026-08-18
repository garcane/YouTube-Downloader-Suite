"""ffmpeg discovery, mocked so the suite passes with or without a binary."""

import subprocess
from pathlib import Path

import pytest

from ytdl.core import ffmpeg as ffmpeg_mod
from ytdl.core.errors import FFmpegNotFoundError


@pytest.fixture(autouse=True)
def clear_resolver_cache():
    """The resolver memoises; each test starts from a clean slate."""
    ffmpeg_mod._cached_ffmpeg = None
    ffmpeg_mod._cached_ffprobe = None
    yield
    ffmpeg_mod._cached_ffmpeg = None
    ffmpeg_mod._cached_ffprobe = None


@pytest.fixture
def working_binary(monkeypatch):
    """Make every probed path look like a functioning ffmpeg."""
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="ffmpeg version 7.1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


@pytest.fixture
def no_binary_anywhere(monkeypatch):
    """Simulate a machine with no ffmpeg at all."""
    monkeypatch.delenv(ffmpeg_mod.ENV_FFMPEG, raising=False)
    monkeypatch.delenv(ffmpeg_mod.ENV_FFPROBE, raising=False)
    monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: None)

    def fail(cmd, *args, **kwargs):
        raise OSError("not found")

    monkeypatch.setattr(subprocess, "run", fail)

    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "imageio_ffmpeg":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)


class TestResolutionOrder:
    def test_explicit_path_wins(self, working_binary):
        assert ffmpeg_mod.find_ffmpeg("/custom/ffmpeg") == Path("/custom/ffmpeg")

    def test_environment_variable_is_used(self, working_binary, monkeypatch):
        monkeypatch.setenv(ffmpeg_mod.ENV_FFMPEG, "/from/env/ffmpeg")
        assert ffmpeg_mod.find_ffmpeg() == Path("/from/env/ffmpeg")

    def test_path_lookup_is_used_when_env_is_unset(self, working_binary, monkeypatch):
        monkeypatch.delenv(ffmpeg_mod.ENV_FFMPEG, raising=False)
        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        assert ffmpeg_mod.find_ffmpeg() == Path("/usr/bin/ffmpeg")

    def test_env_takes_precedence_over_path(self, working_binary, monkeypatch):
        monkeypatch.setenv(ffmpeg_mod.ENV_FFMPEG, "/from/env/ffmpeg")
        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        assert ffmpeg_mod.find_ffmpeg() == Path("/from/env/ffmpeg")

    def test_result_is_cached(self, working_binary, monkeypatch):
        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        first = ffmpeg_mod.find_ffmpeg()
        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: "/somewhere/else")
        assert ffmpeg_mod.find_ffmpeg() == first  # cached, not re-resolved


class TestMissingBinary:
    def test_raises_with_actionable_guidance(self, no_binary_anywhere):
        with pytest.raises(FFmpegNotFoundError) as excinfo:
            ffmpeg_mod.find_ffmpeg()

        hint = excinfo.value.hint or ""
        assert "PATH" in hint
        assert ffmpeg_mod.ENV_FFMPEG in hint
        assert "pip install" in hint

    def test_is_available_reports_false_without_raising(self, no_binary_anywhere):
        assert ffmpeg_mod.is_available() is False

    def test_status_degrades_gracefully(self, no_binary_anywhere):
        result = ffmpeg_mod.status()
        assert result.available is False
        assert result.message
        assert result.path is None

    def test_explicit_bad_path_names_the_path(self, no_binary_anywhere):
        with pytest.raises(FFmpegNotFoundError) as excinfo:
            ffmpeg_mod.find_ffmpeg("/nope/ffmpeg")
        assert "/nope/ffmpeg" in excinfo.value.user_message

    def test_bad_env_var_is_reported_clearly(self, no_binary_anywhere, monkeypatch):
        monkeypatch.setenv(ffmpeg_mod.ENV_FFMPEG, "/bogus/path")
        with pytest.raises(FFmpegNotFoundError) as excinfo:
            ffmpeg_mod.find_ffmpeg()
        assert "/bogus/path" in excinfo.value.user_message

    def test_ffprobe_returns_none_rather_than_raising(self, no_binary_anywhere):
        # ffprobe is optional: without it progress is indeterminate, not broken.
        assert ffmpeg_mod.find_ffprobe() is None

    def test_probe_duration_returns_none(self, no_binary_anywhere):
        assert ffmpeg_mod.probe_duration("anything.mp4") is None


class TestStatus:
    def test_reports_version_when_available(self, working_binary, monkeypatch):
        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        result = ffmpeg_mod.status()
        assert result.available is True
        # Compare as paths: str(Path) is separator-dependent across platforms.
        assert Path(result.path) == Path("/usr/bin/ffmpeg")
        assert "ffmpeg version" in (result.version or "")
