"""Desktop GUI: threading contract and state handling.

These tests create a real Tk root, so they skip cleanly on a machine with no
display (headless CI). What matters here is the threading discipline — Tk
corrupts silently when touched off its own thread — and that is verified by
inspecting where widget calls are allowed to happen, not only by running them.
"""

import inspect
import queue
import threading

import pytest

ctk = pytest.importorskip("customtkinter")

from ytdl.core.download import DownloadResult
from ytdl.core.jobs import Phase, Progress
from ytdl.core.streams import StreamOption, VideoInfo


@pytest.fixture
def app():
    from ytdl.gui import DownloaderApp

    try:
        instance = DownloaderApp()
    except Exception as exc:  # no display available
        pytest.skip(f"Tk unavailable: {exc}")

    instance.withdraw()  # never flash a window during a test run
    yield instance
    try:
        instance.destroy()
    except Exception:
        pass


def sample_info():
    return VideoInfo(
        video_id="abc",
        url="https://www.youtube.com/watch?v=abc",
        title="Sample",
        author="Chan",
        length_seconds=100,
        options=[
            StreamOption(kind="video", label="1080p", itag=1, resolution="1080p",
                         needs_merge=True, recommended=True, codec="avc1"),
            StreamOption(kind="video", label="360p", itag=2, resolution="360p",
                         progressive=True, codec="avc1"),
            StreamOption(kind="audio", label="MP3 320k", abr="320k",
                         needs_transcode=True, is_default=True),
        ],
    )


class TestThreadingContract:
    """Tk is not thread-safe; the worker must never touch a widget."""

    def test_post_only_enqueues(self):
        from ytdl.gui import DownloaderApp

        source = inspect.getsource(DownloaderApp._post)
        for forbidden in ("configure(", ".grid(", ".set(", "winfo_", "destroy("):
            assert forbidden not in source, f"_post must not call {forbidden}"
        assert "put(" in source

    def test_workers_communicate_only_through_the_queue(self):
        from ytdl.gui import DownloaderApp

        for name in ("_fetch_worker", "_download_worker"):
            source = inspect.getsource(getattr(DownloaderApp, name))
            assert "self._post(" in source
            for forbidden in ("configure(", ".grid(", "self.status_label", "self.progress_bar"):
                assert forbidden not in source, f"{name} must not touch widgets"

    def test_events_queue_is_drained_on_the_tk_thread(self, app):
        app._post("progress", Progress(phase=Phase.DOWNLOADING, fraction=0.5))
        assert not app._events.empty()

        app._drain_events()
        assert app._events.empty()

    def test_posting_from_another_thread_is_safe(self, app):
        def worker():
            for i in range(20):
                app._post("progress", Progress(phase=Phase.DOWNLOADING, fraction=i / 20))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        app._drain_events()
        assert app._events.empty()

    def test_unknown_event_kinds_are_ignored_not_fatal(self, app):
        app._post("nonsense", 1, 2)
        app._drain_events()   # must not raise


class TestState:
    def test_info_handler_populates_options(self, app):
        info = sample_info()
        app._handle_info(info)
        assert app.info is info
        assert app.fetch_button.cget("text") == "Fetch"

    def test_featured_card_stands_down_during_a_download(self, app):
        # docs/DESIGN-APPLICATION.md: the progress bar is the one cobalt element
        # on screen while it is visible.
        app._handle_info(sample_info())
        assert app._downloading is False

        app._downloading = True
        app._render_options(app.info)   # must not raise; featured styling drops

        app._downloading = False
        app._render_options(app.info)

    def test_error_handler_restores_the_idle_state(self, app):
        app._handle_info(sample_info())
        app._handle_error("Something failed.", "Try later.")

        assert app._downloading is False
        assert app.fetch_button.cget("state") == "normal"
        assert "Something failed." in app.status_label.cget("text")

    def test_done_handler_reports_the_saved_file(self, app, tmp_path):
        target = tmp_path / "Sample.mp4"
        target.write_bytes(b"x" * 2048)

        app._handle_info(sample_info())
        app._handle_done(
            DownloadResult(path=target, title="Sample", option_label="1080p",
                           filesize=2048, video_id="abc")
        )

        assert app._downloading is False
        assert "Sample.mp4" in app.status_label.cget("text")

    def test_indeterminate_progress_is_handled(self, app):
        app._handle_progress(Progress(phase=Phase.RESOLVING, message="Reading"))
        assert "Reading" in app.status_label.cget("text")

    def test_determinate_progress_updates_the_bar(self, app):
        app._handle_progress(
            Progress(phase=Phase.DOWNLOADING, message="Downloading",
                     fraction=0.25, bytes_done=25, bytes_total=100)
        )
        assert abs(app.progress_bar.get() - 0.25) < 0.01

    def test_cancel_sets_the_token(self, app):
        from ytdl.core import CancelToken

        app.cancel_token = CancelToken()
        app.on_cancel()
        assert app.cancel_token.cancelled

    def test_cancel_without_a_job_is_harmless(self, app):
        app.cancel_token = None
        app.on_cancel()

    def test_empty_url_does_not_start_work(self, app):
        app.url_entry.delete(0, "end")
        app.on_fetch()
        assert app.worker is None


class TestTokens:
    def test_palette_matches_the_design_system(self):
        from ytdl import gui

        assert gui.PRIMARY == "#494fdf"
        assert gui.CANVAS_DARK == "#000000"       # true black, never near-black
        assert gui.SURFACE_ELEVATED == "#16181a"

    def test_buttons_are_pills(self):
        from ytdl import gui

        assert gui.CONTROL_HEIGHT // 2 == 24      # corner radius = height/2
        assert gui.INPUT_HEIGHT == 56             # matches the web input
