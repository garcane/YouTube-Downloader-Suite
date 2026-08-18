"""Localhost web interface.

Returns YouTube-To-Audio to the Flask design it started with in January 2024,
before commit 17468d9 converted it to a command-line script — this time over the
unified core, so it serves video as well as audio.

Binds to 127.0.0.1 by default. This application has no authentication and will
write files anywhere the user running it can write, so exposing it on a network
interface would hand those abilities to anyone who can reach the port. Binding
elsewhere requires an explicit --host and prints a warning.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request, send_file

from .. import __version__
from ..core import YtdlError, default_output_dir, fetch_info, ffmpeg_status
from ..core.errors import InvalidURLError
from ..core.net import diagnose, enable_system_trust
from ..core.streams import DEFAULT_MP3_BITRATE, MP3_BITRATES
from .jobs import JobRegistry, resolve_download_path


def create_app(registry: Optional[JobRegistry] = None) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.registry = registry or JobRegistry()

    # Route TLS through the OS store before any outbound request, so machines
    # behind intercepting antivirus work without the user configuring anything.
    enable_system_trust()

    def _error_response(exc: YtdlError, status: int = 400):
        return jsonify({"error": exc.user_message, "hint": exc.hint}), status

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            version=__version__,
            default_output=str(default_output_dir()),
            bitrates=MP3_BITRATES,
            default_bitrate=DEFAULT_MP3_BITRATE,
        )

    @app.get("/api/status")
    def status():
        """Capability report, rendered in the page footer."""
        ff = ffmpeg_status()
        tls = diagnose()
        return jsonify(
            {
                "version": __version__,
                "ffmpeg": {
                    "available": ff.available,
                    "path": ff.path,
                    "version": ff.version,
                    "message": ff.message,
                },
                "tls": tls.as_dict(),
                "output_dir": str(default_output_dir()),
            }
        )

    @app.post("/api/resolve")
    def resolve():
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()
        if not url:
            return _error_response(InvalidURLError("Paste a YouTube link first."))

        try:
            info = fetch_info(url)
        except YtdlError as exc:
            return _error_response(exc)
        return jsonify(info.as_dict())

    @app.post("/api/jobs")
    def create_job():
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()
        option = (payload.get("option") or "").strip()

        if not url or not option:
            return _error_response(InvalidURLError("A link and a quality choice are required."))

        job = app.registry.create(
            url,
            option,
            output_dir=payload.get("output_dir") or None,
            mp3_bitrate=payload.get("bitrate") or DEFAULT_MP3_BITRATE,
        )
        return jsonify(job.as_dict()), 202

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify([job.as_dict() for job in app.registry.all()])

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        job = app.registry.get(job_id)
        if job is None:
            return jsonify({"error": "No such download."}), 404
        return jsonify(job.as_dict())

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id: str):
        if not app.registry.cancel(job_id):
            return jsonify({"error": "That download cannot be cancelled."}), 409
        return jsonify({"cancelled": True})

    @app.get("/api/jobs/<job_id>/events")
    def job_events(job_id: str):
        """Server-Sent Events stream of progress for one job."""
        job = app.registry.get(job_id)
        if job is None:
            return jsonify({"error": "No such download."}), 404

        def stream():
            listener = job.subscribe()
            try:
                while True:
                    payload = listener.get()
                    if payload is None:          # job finished
                        yield "event: done\ndata: {}\n\n"
                        return
                    yield f"data: {json.dumps(payload)}\n\n"
            finally:
                job.unsubscribe(listener)

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",   # don't buffer behind a proxy
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/jobs/<job_id>/file")
    def job_file(job_id: str):
        """Serve the finished file.

        The client supplies a job id, never a path; the path comes from the
        job's own recorded result, so this cannot be steered at arbitrary files.
        """
        job = app.registry.get(job_id)
        if job is None:
            return jsonify({"error": "No such download."}), 404
        try:
            path = resolve_download_path(job)
        except YtdlError as exc:
            return _error_response(exc, status=404)

        return send_file(path, as_attachment=True, download_name=path.name)

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found."}), 404
        return render_template("index.html", version=__version__,
                               default_output=str(default_output_dir()),
                               bitrates=MP3_BITRATES,
                               default_bitrate=DEFAULT_MP3_BITRATE), 404

    return app


__all__ = ["create_app", "JobRegistry"]
