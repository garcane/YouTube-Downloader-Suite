# Repository history

This repository is the unification of two previously separate projects. Both of
their complete Git histories were merged in rather than being squashed or
re-imported, so development context going back to September 2023 is preserved
and browsable here.

## Source repositories

| Source | Merged from | Commits | Active period |
| --- | --- | --- | --- |
| [YouTube-To-Audio](https://github.com/garcane/YouTube-To-Audio) | `main` @ `2d83c2a` | 57 | Jan 2024 – Oct 2024 |
| [YouTube-MP4-Downloader-Pro](https://github.com/garcane/YouTube-MP4-Downloader-Pro) | `main` @ `0f89ed4` | 40 | Sep 2023 – Sep 2025 |

Both original tips are ancestors of `main`. Verify at any time with:

```bash
git merge-base --is-ancestor 2d83c2a HEAD && echo "YouTube-To-Audio history present"
git merge-base --is-ancestor 0f89ed4 HEAD && echo "YouTube-MP4-Downloader-Pro history present"
```

## How the merge was performed

The two histories are genuinely unrelated — they share no common ancestor — so
they were joined with two `--allow-unrelated-histories` merges. To guarantee the
merges produced no path conflicts (both repos had `app.py`, `README.md` and
`.gitignore` at their roots), each source tree was first relocated into its own
subdirectory on its own import branch:

```
main (empty initial commit)
 ├── merge import-yta   ── legacy/youtube-to-audio/   ── 57 commits back to 2024-01-19
 └── merge import-ympd  ── legacy/youtube-mp4-pro/    ── 40 commits back to 2023-09-17
```

As the unified application was built, the two scripts' logic was redistributed
across `src/ytdl/core/`, and `legacy/` was removed once the consolidation was
verified end to end. The originals remain fully readable in history:

```bash
git show 2d83c2a:app.py    # YouTube-To-Audio, final standalone version
git show 0f89ed4:app.py    # YouTube-MP4-Downloader-Pro, final standalone version
```

[MIGRATION.md](MIGRATION.md) records where each original capability ended up,
and the two that were deliberately not carried over.

## Reading pre-merge history

Path-limited log from the current layout:

```bash
git log --follow -- <path>
```

**Caveat:** because the two lineages contain independently written files that are
textually similar (both repos had their own `app.py` implementing overlapping
logic), Git's rename detection can occasionally cause `--follow` to hop from one
lineage into the other at the merge commit. This is a known limitation of
`--follow`'s single-path heuristic, not missing or corrupted history. When you
need one project's history unambiguously, anchor the query to that project's
pre-merge tip:

```bash
git log 2d83c2a -- app.py    # YouTube-To-Audio only
git log 0f89ed4 -- app.py    # YouTube-MP4-Downloader-Pro only
```

## Recovered material

Two sets of files that had been deleted from the source repositories were
restored from history during the merge, because they are directly useful to the
unified application:

- **Flask web application** (now `src/ytdl/web/`) — recovered from `253e473`. YouTube-To-Audio began life as a Flask site with Bootstrap
  templates (`index`, `about`, `contact`, `privacy`, `terms`) before commit
  `17468d9`, "Converting the website into a command line app", removed them in
  February 2024. The web interface of the unified application builds on these
  rather than starting from scratch. Note `static/styles.css` was already empty
  at the time of deletion.
- **Docker configuration** (now `docker/`) — recovered from `f67b9d9`, added and
  removed on the same day in February 2025. It installs `ffmpeg` via apt, which
  remains the basis of the unified container image, though the entry point and
  dependency install were updated for the new layout.

A later snapshot of the web app exists at `25c1f6d`, but its `index.html` is a
16-line stub against `253e473`'s 84-line Bootstrap page, so `253e473` was used.

## Authorship

Commits carry five author identities (`George`, `garcane`, `garcane` across four
email addresses) reflecting GitHub username and email changes over the years.
These are unified for display by `/.mailmap`, which changes no commit hashes.
`git shortlog -sne` shows the collapsed view.
