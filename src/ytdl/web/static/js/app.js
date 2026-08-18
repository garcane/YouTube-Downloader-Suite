/* Front-end for the YouTube Downloader Suite.
 *
 * No framework and no build step: the page is small enough that vanilla DOM
 * work is clearer than a toolchain, and it keeps the app fully offline —
 * nothing is fetched from a CDN.
 */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    form: $("resolve-form"),
    url: $("url"),
    resolveButton: $("resolve-button"),
    resolveError: $("resolve-error"),
    optionsSection: $("options-section"),
    optionsGrid: $("options-grid"),
    kindFilter: $("kind-filter"),
    title: $("video-title"),
    meta: $("video-meta"),
    thumb: $("thumb"),
    resultsSection: $("results-section"),
    resultsList: $("results-list"),
    statusLine: $("status-line"),
    ffmpegBadge: $("ffmpeg-badge"),
  };

  let currentInfo = null;
  let ffmpegAvailable = true;
  let activeFilter = "all";

  /* ---------------------------------------------------------------- utils */

  const formatBytes = (n) => {
    if (!n && n !== 0) return "unknown";
    const units = ["B", "KB", "MB", "GB"];
    let value = n, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return `${unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
  };

  const formatDuration = (seconds) => {
    if (seconds == null) return "";
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
             : `${m}:${String(sec).padStart(2, "0")}`;
  };

  // Always build DOM nodes rather than assigning innerHTML: video titles are
  // attacker-influenced text and must never be parsed as markup.
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  };

  const showError = (message, hint) => {
    el.resolveError.textContent = "";
    el.resolveError.appendChild(document.createTextNode(message));
    if (hint) el.resolveError.appendChild(node("span", "feedback__hint", hint));
    el.resolveError.hidden = false;
  };

  const clearError = () => { el.resolveError.hidden = true; };

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || `Request failed (${response.status})`);
      error.hint = payload.hint;
      throw error;
    }
    return payload;
  };

  /* -------------------------------------------------------------- status */

  const loadStatus = async () => {
    try {
      const status = await api("/api/status");
      ffmpegAvailable = status.ffmpeg.available;

      if (!status.tls.ok) {
        el.statusLine.textContent = status.tls.detail;
      } else if (!ffmpegAvailable) {
        el.statusLine.textContent = "ffmpeg not found — only single-stream downloads available";
      } else {
        el.statusLine.textContent = "ffmpeg ready";
      }

      if (!ffmpegAvailable) {
        el.ffmpegBadge.textContent = "ffmpeg missing";
        el.ffmpegBadge.hidden = false;
      }
    } catch {
      el.statusLine.textContent = "Could not read environment status";
    }
  };

  /* ------------------------------------------------------------- resolve */

  el.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();

    const url = el.url.value.trim();
    if (!url) return;

    el.resolveButton.disabled = true;
    el.resolveButton.textContent = "Fetching…";

    try {
      currentInfo = await api("/api/resolve", {
        method: "POST",
        body: JSON.stringify({ url }),
      });
      renderInfo(currentInfo);
    } catch (error) {
      showError(error.message, error.hint);
      el.optionsSection.hidden = true;
    } finally {
      el.resolveButton.disabled = false;
      el.resolveButton.textContent = "Fetch";
    }
  });

  const renderInfo = (info) => {
    el.title.textContent = info.title;

    const meta = [];
    if (info.author) meta.push(info.author);
    if (info.length_seconds) meta.push(formatDuration(info.length_seconds));
    el.meta.textContent = meta.join("  ·  ");

    if (info.thumbnail_url) {
      el.thumb.src = info.thumbnail_url;
      el.thumb.hidden = false;
    } else {
      el.thumb.hidden = true;
    }

    renderOptions();
    el.optionsSection.hidden = false;
    el.optionsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const renderOptions = () => {
    el.optionsGrid.textContent = "";
    if (!currentInfo) return;

    const options = currentInfo.options.filter(
      (option) => activeFilter === "all" || option.kind === activeFilter
    );

    for (const option of options) {
      const blocked = option.requires_ffmpeg && !ffmpegAvailable;

      const card = node("button", "card");
      card.type = "button";
      if (option.recommended && !blocked) card.classList.add("card--featured");
      card.disabled = blocked;

      card.appendChild(node("span", "card__label", option.label));

      const badges = node("div", "badges");
      if (option.recommended) badges.appendChild(node("span", "badge badge--feature", "Best"));
      else if (option.is_default) badges.appendChild(node("span", "badge", "Default"));
      if (option.kind === "audio") badges.appendChild(node("span", "badge", "Audio"));
      if (option.needs_merge) badges.appendChild(node("span", "badge", "Merged"));
      if (option.codec) badges.appendChild(node("span", "badge", option.codec));
      if (badges.childElementCount) card.appendChild(badges);

      const metaText = blocked
        ? "Needs ffmpeg — not installed"
        : [option.filesize_display, option.fps ? `${option.fps}fps` : null]
            .filter(Boolean).join("  ·  ");
      card.appendChild(node("span", "card__meta", metaText));

      // Buttons built from spans get an implicit name, but an explicit one
      // reads far better in a screen reader than "360p Best avc1 618.2 KB".
      card.setAttribute(
        "aria-label",
        blocked
          ? `${option.label} — unavailable, requires ffmpeg`
          : `Download ${option.label}, ${option.filesize_display}`
      );

      if (!blocked) card.addEventListener("click", () => startDownload(option));
      el.optionsGrid.appendChild(card);
    }
  };

  el.kindFilter.addEventListener("click", (event) => {
    const button = event.target.closest("[data-kind]");
    if (!button) return;
    activeFilter = button.dataset.kind;
    [...el.kindFilter.children].forEach((child) =>
      child.classList.toggle("chip--active", child === button)
    );
    renderOptions();
  });

  /* ------------------------------------------------------------ download */

  const startDownload = async (option) => {
    clearError();

    let job;
    try {
      job = await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({ url: currentInfo.url, option: option.label }),
      });
    } catch (error) {
      showError(error.message, error.hint);
      return;
    }

    const tile = createTile(currentInfo.title, option.label, job.id);
    el.resultsSection.hidden = false;
    el.resultsList.prepend(tile.root);
    // The options list is long; without this the new tile and its progress bar
    // start well below the fold and the download looks like it did nothing.
    tile.root.scrollIntoView({ behavior: "smooth", block: "center" });
    followJob(job.id, tile);
  };

  const createTile = (title, optionLabel, jobId) => {
    const root = node("li", "tile");
    root.dataset.jobId = jobId;

    const left = node("div");
    left.appendChild(node("div", "tile__name", title));
    const meta = node("div", "tile__meta", `${optionLabel} — starting…`);
    left.appendChild(meta);

    const progress = node("div", "progress");
    const track = node("div", "progress__track");
    const fill = node("div", "progress__fill progress__fill--indeterminate");
    track.appendChild(fill);
    progress.appendChild(track);
    left.appendChild(progress);

    const actions = node("div", "tile__actions");
    const cancel = node("button", "button button--soft button--sm", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      cancel.disabled = true;
    });
    actions.appendChild(cancel);

    root.append(left, actions);
    return { root, meta, fill, actions, cancel, optionLabel };
  };

  const followJob = (jobId, tile) => {
    const events = new EventSource(`/api/jobs/${jobId}/events`);

    events.onmessage = (event) => {
      const job = JSON.parse(event.data);
      const progress = job.progress;

      if (progress && progress.fraction != null) {
        tile.fill.classList.remove("progress__fill--indeterminate");
        tile.fill.style.width = `${(progress.fraction * 100).toFixed(1)}%`;
      }

      if (progress) {
        const bits = [progress.message || progress.phase];
        if (progress.percent != null) bits.push(`${progress.percent.toFixed(0)}%`);
        if (progress.speed_bps) bits.push(`${formatBytes(progress.speed_bps)}/s`);
        if (progress.eta_seconds != null) bits.push(`ETA ${formatDuration(progress.eta_seconds)}`);
        tile.meta.textContent = bits.join("  ·  ");
      }

      if (job.finished) finishTile(job, tile);
    };

    events.addEventListener("done", () => events.close());
    events.onerror = () => events.close();
  };

  const finishTile = (job, tile) => {
    tile.cancel.remove();

    if (job.error) {
      tile.fill.classList.remove("progress__fill--indeterminate");
      tile.fill.style.width = "100%";
      tile.fill.style.background = "var(--status-err-fill)";
      tile.meta.textContent = "";
      tile.meta.appendChild(node("span", "status-err", "Failed"));
      tile.meta.appendChild(document.createTextNode(`  ${job.error}`));
      return;
    }

    if (job.result) {
      tile.fill.style.width = "100%";
      tile.fill.classList.remove("progress__fill--indeterminate");
      tile.meta.textContent = "";
      tile.meta.appendChild(node("span", "status-ok", "Saved"));
      tile.meta.appendChild(
        document.createTextNode(`  ${tile.optionLabel}  ·  ${formatBytes(job.result.filesize)}`)
      );

      const save = node("a", "button button--dark button--sm", "Save a copy");
      save.href = `/api/jobs/${job.id}/file`;
      tile.actions.appendChild(save);
    }
  };

  loadStatus();
})();
