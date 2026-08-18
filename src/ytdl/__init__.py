"""YouTube Downloader Suite.

A unified downloader combining two previously separate projects:

* **YouTube-To-Audio** — MP3 extraction with selectable bitrate,
* **YouTube-MP4-Downloader-Pro** — video downloads with stream merging.

Their full development histories are preserved in this repository; see
``docs/HISTORY.md``.

The core library is interface-agnostic. Three interfaces build on it:

* ``ytdl.cli`` — command line,
* ``ytdl.web`` — localhost web interface,
* ``ytdl.gui`` — CustomTkinter desktop application.
"""

__version__ = "1.0.0.dev0"

__all__ = ["__version__"]
