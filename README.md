# YouTube Downloader Suite

Download YouTube video or audio from a command line, a browser, or a desktop
window — all three over one shared core.

This repository unifies two previously separate projects,
[YouTube-To-Audio](https://github.com/garcane/YouTube-To-Audio) and
[YouTube-MP4-Downloader-Pro](https://github.com/garcane/YouTube-MP4-Downloader-Pro),
with both of their full Git histories preserved. See [docs/HISTORY.md](docs/HISTORY.md).

## What it does

- Video from 240p to 4K, merging separate video and audio tracks when needed
- Audio as MP3 at any bitrate from 32k to 320k, or M4A at source quality
- Merging is a **stream copy** — lossless, and far faster than re-encoding
- Prefers H.264/AAC so the result plays everywhere, not just in a browser
- Never overwrites an existing file, and never leaves a partial one behind

## Install

```bash
git clone https://github.com/garcane/YouTube-Downloader-Suite.git
cd YouTube-Downloader-Suite
pip install -e ".[all]"
```

`[all]` pulls in the web interface, the desktop GUI, a bundled ffmpeg fallback,
and the system-trust bridge. To keep it minimal, install only what you need:
`.[web]`, `.[gui]`, `.[tls]`, `.[ffmpeg]`.

**ffmpeg** is required for merging video with audio and for MP3 conversion.
Progressive downloads and M4A audio work without it.

```bash
winget install Gyan.FFmpeg
```

Check everything at once:

```bash
ytdl doctor
```

## Use

### Command line

```bash
ytdl https://youtu.be/VIDEOID
```

```bash
ytdl https://youtu.be/VIDEOID -q 1080p -o ~/Videos
```

```bash
ytdl https://youtu.be/VIDEOID --mp3 -b 320k
```

`--list` shows the options and exits. Progress goes to stderr and the final
path to stdout alone, so `ytdl URL -q 720p > path.txt` captures just the path.

### Web interface

```bash
ytdl-web
```

Serves on <http://localhost:8000>. It binds to `127.0.0.1` deliberately: there
is no authentication and it writes files as whoever runs it, so exposing it on a
network interface would hand both to anyone who can reach the port.

### Desktop

```bash
ytdl-gui
```

## Known operating conditions

**YouTube actively restricts non-browser clients.** It gates metadata and media
separately, and blocks pytubefix's client types unpredictably — the library's
own default client is currently rejected outright. The core tries several
clients in turn and remembers whichever works. If downloads start failing,
first try upgrading:

```bash
pip install -U pytubefix
```

You can force a specific client with `YTDL_CLIENT=MWEB`.

**Antivirus HTTPS scanning breaks Python's TLS.** Products such as Avast
re-sign traffic with a root certificate that OpenSSL 3.x rejects as
non-conforming, and adding it to the trust store does not help. Installing
`truststore` (included in `[all]` and `[tls]`) routes verification through the
operating system's own verifier and resolves it. `ytdl doctor` will tell you if
this applies to your machine.

## Configuration

| Variable | Purpose |
| --- | --- |
| `YTDL_CLIENT` | Force pytubefix client order, e.g. `MWEB` or `WEB,MWEB` |
| `YTDL_FFMPEG` | Full path to an ffmpeg binary |
| `YTDL_FFPROBE` | Full path to an ffprobe binary |
| `YTDL_OUTPUT` | Default download folder, overriding `~/Downloads` |
| `NO_COLOR` | Disable CLI colour |

## Development

```bash
pip install -e ".[dev,all]"
```

```bash
pytest
```

The suite needs no network and no ffmpeg; tests that would require either are
mocked or skipped. `tests/conftest.py` clears all `YTDL_*` variables so an
exported override cannot change what is being tested.

### Layout

```
src/ytdl/
├── core/     interface-agnostic library — no printing, no prompting
├── cli/      command line
├── web/      Flask + Server-Sent Events
└── gui/      CustomTkinter
```

Everything in `core` reports through `Progress` callbacks and raises
`YtdlError` subclasses, which is what lets one implementation serve three
interfaces.

- [docs/HISTORY.md](docs/HISTORY.md) — how the two repositories were merged
- [docs/MIGRATION.md](docs/MIGRATION.md) — where each original capability went
- [docs/DESIGN.md](docs/DESIGN.md) — the design system
- [docs/DESIGN-APPLICATION.md](docs/DESIGN-APPLICATION.md) — how it maps onto this app

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

Serves the web interface on <http://localhost:8000>, with downloads landing in
`docker/downloads/` on the host. The GUI extra is excluded from the image, since
Tk needs a display.

## Legal

For personal use with content you have the right to download. Downloading
copyrighted material without permission may breach YouTube's Terms of Service
and local law.

## Licence

MIT
