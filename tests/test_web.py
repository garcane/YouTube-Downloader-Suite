"""Web interface: API contract, job lifecycle, and file-serving safety."""

import json
import time

import pytest

pytest.importorskip("flask")

from ytdl.core.download import DownloadResult
from ytdl.core.errors import InvalidURLError, VideoUnavailableError
from ytdl.core.jobs import Phase, Progress
from ytdl.web import create_app
from ytdl.web.jobs import Job, JobRegistry, resolve_download_path


@pytest.fixture
def app():
    return create_app(registry=JobRegistry())


@pytest.fixture
def client(app):
    return app.test_client()


class FakeInfo:
    def __init__(self):
        self.title = "Test Video"
        self.url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def as_dict(self):
        return {
            "video_id": "dQw4w9WgXcQ",
            "url": self.url,
            "title": self.title,
            "author": "Chan",
            "length_seconds": 10,
            "thumbnail_url": None,
            "options": [
                {"label": "720p", "kind": "video", "recommended": True,
                 "requires_ffmpeg": False, "filesize_display": "1.0 MB"},
            ],
        }


class TestPages:
    def test_index_renders(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"Download from" in response.data

    def test_unknown_page_returns_the_app_shell(self, client):
        assert client.get("/nope").status_code == 404

    def test_unknown_api_path_returns_json_not_html(self, client):
        response = client.get("/api/nope")
        assert response.status_code == 404
        assert response.is_json


class TestStatus:
    def test_status_reports_capabilities(self, client):
        payload = client.get("/api/status").get_json()
        assert "ffmpeg" in payload and "tls" in payload
        assert "available" in payload["ffmpeg"]
        assert "output_dir" in payload


class TestResolve:
    def test_empty_url_is_rejected(self, client):
        response = client.post("/api/resolve", json={"url": "  "})
        assert response.status_code == 400
        assert response.get_json()["error"]

    def test_core_errors_become_json_with_a_hint(self, client, monkeypatch):
        def boom(*a, **k):
            raise VideoUnavailableError("Private video.", hint="Sign in required.")

        monkeypatch.setattr("ytdl.web.fetch_info", boom)
        response = client.post("/api/resolve", json={"url": "https://youtu.be/dQw4w9WgXcQ"})

        assert response.status_code == 400
        payload = response.get_json()
        assert payload["error"] == "Private video."
        assert payload["hint"] == "Sign in required."

    def test_successful_resolve_returns_options(self, client, monkeypatch):
        monkeypatch.setattr("ytdl.web.fetch_info", lambda *a, **k: FakeInfo())
        payload = client.post("/api/resolve",
                              json={"url": "https://youtu.be/dQw4w9WgXcQ"}).get_json()
        assert payload["title"] == "Test Video"
        assert payload["options"][0]["label"] == "720p"


class TestJobs:
    def test_creating_a_job_requires_url_and_option(self, client):
        assert client.post("/api/jobs", json={"url": "x"}).status_code == 400
        assert client.post("/api/jobs", json={"option": "720p"}).status_code == 400

    def test_job_runs_and_reports_completion(self, client, monkeypatch, tmp_path):
        target = tmp_path / "Test-Video.mp4"
        target.write_bytes(b"video-bytes")

        monkeypatch.setattr("ytdl.web.jobs.fetch_info", lambda *a, **k: FakeInfo())
        monkeypatch.setattr(
            "ytdl.web.jobs.download",
            lambda *a, **k: DownloadResult(
                path=target, title="Test Video", option_label="720p",
                filesize=target.stat().st_size, video_id="dQw4w9WgXcQ",
            ),
        )

        created = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "option": "720p"},
        )
        assert created.status_code == 202
        job_id = created.get_json()["id"]

        for _ in range(100):  # worker thread
            payload = client.get(f"/api/jobs/{job_id}").get_json()
            if payload["finished"]:
                break
            time.sleep(0.05)

        assert payload["finished"] is True
        assert payload["phase"] == "complete"
        assert payload["result"]["filename"] == "Test-Video.mp4"

    def test_failed_job_records_the_error(self, client, monkeypatch):
        def boom(*a, **k):
            raise VideoUnavailableError("Nope.", hint="Try later.")

        monkeypatch.setattr("ytdl.web.jobs.fetch_info", boom)
        job_id = client.post(
            "/api/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ", "option": "720p"}
        ).get_json()["id"]

        for _ in range(100):
            payload = client.get(f"/api/jobs/{job_id}").get_json()
            if payload["finished"]:
                break
            time.sleep(0.05)

        assert payload["phase"] == "failed"
        assert payload["error"] == "Nope."
        assert payload["error_hint"] == "Try later."

    def test_missing_job_is_404_everywhere(self, client):
        assert client.get("/api/jobs/nope").status_code == 404
        assert client.get("/api/jobs/nope/events").status_code == 404
        assert client.get("/api/jobs/nope/file").status_code == 404
        assert client.post("/api/jobs/nope/cancel").status_code == 409


class TestFileServing:
    def test_file_is_served_from_the_recorded_result(self, tmp_path):
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"data")

        job = Job(id="j", url="u", option="720p")
        job.result = DownloadResult(path=target, title="t", option_label="720p",
                                    filesize=4, video_id="v")
        assert resolve_download_path(job) == target

    def test_unfinished_job_has_no_file(self):
        with pytest.raises(Exception):
            resolve_download_path(Job(id="j", url="u", option="720p"))

    def test_vanished_file_is_reported_not_served(self, tmp_path):
        target = tmp_path / "gone.mp4"
        job = Job(id="j", url="u", option="720p")
        job.result = DownloadResult(path=target, title="t", option_label="720p",
                                    filesize=0, video_id="v")
        with pytest.raises(Exception):
            resolve_download_path(job)


class TestProgressPublishing:
    def test_subscriber_receives_the_current_state_immediately(self):
        job = Job(id="j", url="u", option="720p")
        listener = job.subscribe()
        assert listener.get(timeout=1)["id"] == "j"

    def test_progress_updates_reach_subscribers(self):
        job = Job(id="j", url="u", option="720p")
        listener = job.subscribe()
        listener.get(timeout=1)  # initial replay

        job.on_progress(Progress(phase=Phase.DOWNLOADING, message="go", fraction=0.4))
        payload = listener.get(timeout=1)
        assert payload["progress"]["fraction"] == 0.4
        assert payload["phase"] == "downloading"

    def test_unsubscribe_stops_delivery(self):
        job = Job(id="j", url="u", option="720p")
        listener = job.subscribe()
        listener.get(timeout=1)
        job.unsubscribe(listener)

        job.on_progress(Progress(phase=Phase.DOWNLOADING))
        assert listener.empty()

    def test_job_serialises_to_json(self):
        job = Job(id="j", url="u", option="720p")
        json.dumps(job.as_dict())


class TestRegistry:
    def test_cancel_marks_the_token(self):
        registry = JobRegistry()
        job = Job(id="j", url="u", option="720p")
        registry._jobs["j"] = job

        assert registry.cancel("j") is True
        assert job.cancel_token.cancelled

    def test_finished_jobs_cannot_be_cancelled(self, tmp_path):
        registry = JobRegistry()
        job = Job(id="j", url="u", option="720p")
        job.result = DownloadResult(path=tmp_path / "x", title="t",
                                    option_label="o", filesize=0, video_id="v")
        registry._jobs["j"] = job
        assert registry.cancel("j") is False

    def test_expiry_removes_only_old_finished_jobs(self):
        from ytdl.web.jobs import JOB_RETENTION_SECONDS

        registry = JobRegistry()
        fresh = Job(id="fresh", url="u", option="o")
        old = Job(id="old", url="u", option="o")
        old.finished_at = time.time() - JOB_RETENTION_SECONDS - 10
        running = Job(id="running", url="u", option="o")

        registry._jobs.update({"fresh": fresh, "old": old, "running": running})
        registry._expire_locked()

        assert set(registry._jobs) == {"fresh", "running"}
