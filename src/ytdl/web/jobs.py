"""Background job registry for the web interface.

A download takes minutes, so it cannot run inside a request. Each job runs on a
worker thread and publishes :class:`~ytdl.core.jobs.Progress` snapshots that the
browser consumes over Server-Sent Events.

Jobs are held in memory only. This is a single-user localhost tool, so there is
no database and nothing survives a restart — which is the correct trade-off
here, not an oversight.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..core import CancelToken, Phase, Progress, YtdlError, download, fetch_info
from ..core.download import DownloadResult

#: Completed jobs are forgotten after this long, so a long-running server does
#: not accumulate finished jobs (and their downloadable files) indefinitely.
JOB_RETENTION_SECONDS = 60 * 60

#: Bound on concurrent downloads. YouTube throttles aggressive clients, and
#: unbounded threads would let a page reload start a dozen transfers.
MAX_CONCURRENT_JOBS = 3


@dataclass
class Job:
    """One download, its progress history, and its outcome."""

    id: str
    url: str
    option: str
    output_dir: Optional[str] = None
    mp3_bitrate: str = "320k"

    progress: Optional[Progress] = None
    result: Optional[DownloadResult] = None
    error: Optional[str] = None
    error_hint: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    cancel_token: CancelToken = field(default_factory=CancelToken)
    _subscribers: List["queue.Queue[Optional[dict]]"] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def phase(self) -> Phase:
        if self.error:
            return Phase.FAILED
        if self.result:
            return Phase.COMPLETE
        if self.progress:
            return self.progress.phase
        return Phase.RESOLVING

    @property
    def finished(self) -> bool:
        return self.result is not None or self.error is not None

    def subscribe(self) -> "queue.Queue[Optional[dict]]":
        """Register a listener; ``None`` is pushed to it when the job ends."""
        listener: "queue.Queue[Optional[dict]]" = queue.Queue()
        with self._lock:
            self._subscribers.append(listener)
            # Replay the latest state so a late subscriber is not left blank
            # until the next update happens to arrive.
            listener.put(self.as_dict())
        return listener

    def unsubscribe(self, listener) -> None:
        with self._lock:
            if listener in self._subscribers:
                self._subscribers.remove(listener)

    def _publish(self, payload: Optional[dict]) -> None:
        with self._lock:
            for listener in list(self._subscribers):
                listener.put(payload)

    def on_progress(self, progress: Progress) -> None:
        self.progress = progress
        self._publish(self.as_dict())

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "option": self.option,
            "phase": self.phase.value,
            "finished": self.finished,
            "progress": self.progress.as_dict() if self.progress else None,
            "result": self.result.as_dict() if self.result else None,
            "error": self.error,
            "error_hint": self.error_hint,
            "created_at": self.created_at,
        }


class JobRegistry:
    """Thread-safe store of jobs, with worker threads and expiry."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_JOBS):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(max_concurrent)

    def create(
        self,
        url: str,
        option: str,
        *,
        output_dir: Optional[str] = None,
        mp3_bitrate: str = "320k",
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            url=url,
            option=option,
            output_dir=output_dir,
            mp3_bitrate=mp3_bitrate,
        )
        with self._lock:
            self._expire_locked()
            self._jobs[job.id] = job

        thread = threading.Thread(target=self._run, args=(job,), daemon=True,
                                  name=f"ytdl-job-{job.id[:8]}")
        thread.start()
        return job

    def _run(self, job: Job) -> None:
        acquired = self._slots.acquire(timeout=300)
        if not acquired:
            job.error = "Timed out waiting for a free download slot."
            job.finished_at = time.time()
            job._publish(job.as_dict())
            job._publish(None)
            return

        try:
            info = fetch_info(job.url, cancel=job.cancel_token)
            job.result = download(
                job.url,
                job.option,
                output_dir=job.output_dir,
                on_progress=job.on_progress,
                cancel=job.cancel_token,
                info=info,
                mp3_bitrate=job.mp3_bitrate,
            )
        except YtdlError as exc:
            job.error = exc.user_message
            job.error_hint = exc.hint
        except Exception as exc:  # pragma: no cover - unexpected failure
            job.error = f"Unexpected error: {exc}"
        finally:
            self._slots.release()
            job.finished_at = time.time()
            job._publish(job.as_dict())
            job._publish(None)  # sentinel: closes every SSE stream

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> List[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.finished:
            return False
        job.cancel_token.cancel()
        return True

    def _expire_locked(self) -> None:
        cutoff = time.time() - JOB_RETENTION_SECONDS
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]
        for job_id in stale:
            del self._jobs[job_id]


def resolve_download_path(job: Job) -> Path:
    """Validate the file a job produced before it is served.

    The browser asks for a job id, never a path, and the path is taken from the
    job's own result — so a request cannot reach arbitrary files. This re-checks
    existence, because the user may have moved or deleted the file since.
    """
    if job.result is None:
        raise YtdlError("That download has not finished.")

    path = Path(job.result.path)
    if not path.is_file():
        raise YtdlError("The downloaded file is no longer where it was saved.")
    return path
