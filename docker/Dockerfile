# Container image for the web interface.
#
# Descended from the Dockerfile added to YouTube-MP4-Downloader-Pro in c2804ef
# (February 2025) and removed the same day. The base idea is unchanged — install
# ffmpeg from apt rather than shipping Windows binaries — but the entry point,
# dependency install and exposed service are updated for the unified layout.

FROM python:3.13-slim

# ffmpeg is required for merging adaptive streams and for MP3 conversion.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy packaging metadata first so dependency layers cache independently of
# source changes.
COPY pyproject.toml README.md ./
COPY src ./src

# The GUI extra is deliberately excluded: Tk needs a display, which a container
# does not have. Web and TLS support are what matter here.
RUN pip install --no-cache-dir ".[web,tls]"

# Downloads land here; mount a volume over it to keep files on the host.
ENV YTDL_OUTPUT=/downloads
RUN mkdir -p /downloads

EXPOSE 8000

# 0.0.0.0 is correct *inside* a container — the port is only reachable if it is
# published. See docker-compose.yml, which binds it to localhost on the host.
CMD ["python", "-m", "ytdl.web", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
