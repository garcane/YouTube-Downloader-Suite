# Applying DESIGN.md to this application

[`docs/DESIGN.md`](DESIGN.md) documents a **marketing site**. This project is a
**utility tool**: paste a URL, pick a format, watch progress, collect a file.
The visual language transfers well; the component inventory does not transfer
one-to-one. This document records how the system is mapped, what was added, and
what was deliberately not followed — so the design intent survives contact with
the app.

Tokens live in `src/ytdl/web/static/css/tokens.css` as a direct transcription of
DESIGN.md. Everything below concerns *application* of those tokens.

## Component mapping

| App surface | DESIGN.md component | Notes |
| --- | --- | --- |
| URL entry hero | `hero-band-dark` + `text-input` | The app's front door. Input at 56px, `rounded.md`. |
| Primary action ("Fetch") | `button-primary` | White pill on black — the brightest pixel, exactly as specified. |
| Resolution picker | `button-pill-sm` row | Maps unusually cleanly: `['360p','720p','1080p','audio']` becomes a pill row. Converts to a horizontal scroll-rail below 768px, per the responsive rules. |
| Format / quality options | `plan-card` grid | Each downloadable variant becomes a card on `surface-elevated`. |
| Recommended option | `plan-card-featured` | The single cobalt-violet element per viewport — best available quality. |
| "Audio only" / "1080p" labels | `badge-tag` | Neutral metadata chips. |
| "Recommended" flag | `badge-feature` | Cobalt badge, at most one visible. |
| Completed downloads list | `download-tile` | 56px rows on `surface-soft`, in a light band. |
| Results / history section | `band-light` catalogue mode | The dark→light band switch separates "doing" from "browsing", which happens to match the app's two modes exactly. |
| Footer | `footer` | Version, ffmpeg status, links. |

The band rhythm turns out to carry real information architecture here rather
than being decorative: **dark = the active job, light = the archive.**

## Additions the source system does not cover

DESIGN.md has no progress, status, spinner, modal, or toast language — the
marketing site never needed them. This app is progress-dominated, so these are
additions, made to stay inside the system's stated rules:

- **Progress bar** — track `surface-elevated`, fill `primary-bright` (`#4f55f1`).
  Cobalt is used here despite the "keep it scarce" rule because a running
  download is precisely the "single deliberate stamp" moment the rule protects.
  When a progress bar is visible, no other cobalt element should be.
- **Status text** — success uses `accent-green-text` (`#006400`) and errors use
  `accent-deep-red` (`#8b0000`), *not* `accent-teal` / `accent-danger`. See the
  contrast findings below; the source system already provides these as the
  text-safe variants.
- **Status fills** (icons, dots, bar segments) — may use `accent-teal`,
  `accent-warning`, `accent-danger`, since non-text graphics need only 3:1.
- **Indeterminate state** — for the stream-probing phase before a total byte
  count is known. Respects `prefers-reduced-motion`.

## Contrast findings

Measured against WCAG 2.1. Several palette entries fail as body text and must be
restricted to the roles noted:

| Pair | Ratio | Verdict |
| --- | --- | --- |
| `ink` on white | 17.1:1 | AAA — default body text |
| `mute` on white | 7.0:1 | AAA |
| `ash` on white | 6.5:1 | AA |
| white on black | 21.0:1 | AAA |
| `on-dark-mute` on black | 10.6:1 | AAA |
| white on `primary` | 6.1:1 | AA — featured card text is safe |
| `link` on white | 4.9:1 | AA |
| `accent-pink` on white | 4.5:1 | AA (borderline) |
| `stone` on white | 3.0:1 | **Fails body text** — metadata at ≥18.66px bold, or non-text only |
| `accent-teal` on white | 3.0:1 | **Fails as text** — fills and icons only |
| `accent-yellow` on white | 3.1:1 | **Fails as text** — fills and icons only |
| `accent-warning` on white | 2.8:1 | **Fails entirely as text** |
| `faint` on white | 1.7:1 | Disabled foreground only, never informational |
| `primary` on black | 3.5:1 | **Fails as text** — passes 1.4.11 for UI components |

Two consequences worth stating plainly:

1. **Never set status text in `accent-teal` or `accent-warning`.** The obvious
   instinct ("download complete" in teal) fails AA. Use the `-text` variants.
2. **`primary` must not be used for text on the dark canvas** — only as a
   surface with white on top, or as a non-text UI element such as the progress
   fill.

## Deviations from DESIGN.md

- **Aeonik Pro is not bundled.** It is proprietary. Display type uses the
  substitution ladder DESIGN.md itself recommends (Inter Display → General Sans
  → Söhne → system), with `line-height: 1.0` and −1% tracking clamped explicitly
  as instructed. Inter (SIL OFL) is **self-hosted, not CDN-loaded**, so the app
  works offline — a localhost tool must not depend on the network to render.
- **Display sizes are clamped fluidly** rather than stepped at breakpoints. The
  ladder still lands on the documented 136 → 80 → 64 → 48 endpoints.
- **`surface-elevated` on black is 1.18:1** — essentially invisible as an edge.
  DESIGN.md forbids drop shadows, so cards get a 1px `hairline-dark` border to
  remain perceivable. This is within the system's own vocabulary.
- **No hero at 136px.** `display-xxl` is specified as "one per page" for a
  marketing flagship. A tool that users open dozens of times a day should not
  shout; the URL hero uses `display-xl` (80px) as its ceiling.
- **No Revolut brand assets.** Wordmark, logo, product photography, and plan
  names are not used. What is borrowed is the token system and layout language.
  The cobalt `#494fdf` is retained as the accent per your direction; if this
  project is ever published under its own identity, that hex is the one thing
  worth changing to avoid reading as Revolut-branded.

## Cross-interface reach

The design system is web-only. For the other two interfaces:

- **CustomTkinter GUI** — can honour the palette, the radius scale, and the
  spacing scale (`fg_color`, `corner_radius` map directly, and pill buttons work
  via `corner_radius = height/2`). It cannot honour the type system: Tk has no
  variable-font or letter-spacing control, so display type degrades to the
  nearest available family at weight bold. The GUI will read as *related* to the
  web UI, not identical — that is the realistic ceiling, and worth knowing before
  Phase 4 rather than after.
- **CLI** — takes nothing from the design system beyond, optionally, the status
  colour semantics mapped onto the 16 ANSI colours. Its output must stay
  pipe-safe and degrade cleanly when not attached to a TTY.
