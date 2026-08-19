# Migration from the two source projects

This records where every capability of the two original scripts ended up, and
what deliberately did not carry over. The originals lived at
`legacy/youtube-to-audio/app.py` and `legacy/youtube-mp4-pro/app.py` until the
consolidation was verified; both remain readable in this repository's history.

```bash
git show 2cb3e4a:app.py    # YouTube-To-Audio, final standalone version
git show 7e1f6eb:app.py    # YouTube-MP4-Downloader-Pro, final standalone version
```

## YouTube-To-Audio

| Original behaviour | Where it lives now | Notes |
| --- | --- | --- |
| URL validation | `core/urls.py` | Rewritten — the original rejected `youtube.com` and `m.youtube.com`, and accepted any URL merely containing the text `youtu.be` |
| `remove_non_alpha()` | `core/naming.py` | Rewritten as `sanitize_filename`; the original discarded digits and non-ASCII titles. Original kept as `legacy_remove_non_alpha` for reference |
| Availability check | `core/streams.py` | The original fetched the watch page separately to look for the string "Video unavailable"; pytubefix already performs this check properly |
| Bitrate menu (32k–320k) | `core/streams.py` `MP3_BITRATES` | Identical ladder, identical guidance text |
| 48 kHz sample rate | `core/ffmpeg.py` `transcode_audio` | Preserved as the default |
| Audio download → MP3 | `core/download.py` | Now staged through a temp directory |
| Temp `.mp4` cleanup | `core/download.py` | Handled by `TemporaryDirectory` |
| Flask web app (deleted 2024) | `web/` | Restored from `11ac3a7`; the 2024 favicon is byte-identical |
| **ffmpeg auto-download** | — | **Deliberately dropped.** See below |

## YouTube-MP4-Downloader-Pro

| Original behaviour | Where it lives now | Notes |
| --- | --- | --- |
| URL validation | `core/urls.py` | Shared with the above — the two implementations were near-identical |
| Resolution discovery | `core/streams.py` | Now also picks the most compatible codec per resolution |
| 144p exclusion | `core/streams.py` | Preserved |
| Audio-only `.m4a` | `core/download.py` | Preserved as "M4A (source quality)" |
| Adaptive stream merging | `core/ffmpeg.py` `merge_streams` | Now a stream copy rather than a MoviePy re-encode |
| Temp file cleanup | `core/download.py` | Handled by `TemporaryDirectory` |
| "Download another?" loop | all three interfaces | Each interface handles repetition in its own idiom |
| Docker config (deleted 2025) | `docker/` | Recovered from `239108c` and updated for the new layout |
| **Duration-mismatch trimming** | — | **Deliberately dropped.** See below |

## Deliberate omissions

### ffmpeg auto-download

YouTube-To-Audio downloaded a Windows-only ffmpeg build from a GitHub release
at import time and moved `ffmpeg.exe`, `ffprobe.exe` and `ffplay.exe` into the
current working directory.

This is not reproduced. It made the tool Windows-only, wrote executables into
whatever folder the user happened to be in, and performed a network fetch before
doing anything else. `core/ffmpeg.py` instead *locates* a binary — explicit
argument, `YTDL_FFMPEG`, `PATH`, then the `imageio-ffmpeg` fallback — and fails
with instructions if none is found. Users who want a bundled binary can install
the `[ffmpeg]` extra.

### Duration-mismatch trimming

When merging, YouTube-MP4-Downloader-Pro compared the video and audio durations
and trimmed both to the shorter one:

```python
if video_clip.duration != audio_clip.duration:
    min_duration = min(video_clip.duration, audio_clip.duration)
    video_clip = video_clip.subclip(0, min_duration)
```

This is not reproduced, because it is incompatible with a stream copy — trimming
requires decoding and re-encoding, which is precisely the cost the remux
removes. In practice YouTube's video and audio tracks for a given video are the
same length, and where they differ by a frame or two, players handle it.

If a mismatched file ever appears, the fix is to re-encode that one download
rather than to re-encode every download forever. This is the one place where the
unified app does less than an original did, and it is a deliberate trade.

## Behavioural improvements

Beyond consolidation, the unified core fixes defects present in both originals:

- **Files are never overwritten.** Both originals silently replaced an existing
  file of the same name; downloads now become `name-1.mp4`, `name-2.mp4`.
- **Failures leave nothing behind.** Downloads stage in a temp directory and
  move into place only on success. Both originals could leave a partial file.
- **Output location is explicit.** Both wrote to `os.getcwd()`; the default is
  now the user's Downloads folder, overridable per call.
- **Errors are typed and readable.** Both caught bare `Exception` and printed
  the message; failures are now `YtdlError` subclasses carrying a displayable
  message and often a remedy.
- **Client fallback.** YouTube blocks pytubefix client types unpredictably;
  the core tries several rather than failing on the first.
- **TLS interception is handled.** See `core/net.py`.
- **`pytube` → `pytubefix`.** YouTube-To-Audio's downloader had stopped working
  entirely; the pinned `pytubefix==9.4.1` in the other repo had too.
