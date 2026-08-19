"""CustomTkinter desktop interface.

This is the only interface with no ancestor in either source repository — no
tkinter code appears anywhere in their histories — so it is written from
scratch against the same core the CLI and web interface use.

Threading is the whole problem here. Tk is not thread-safe: touching a widget
from anywhere but the main thread corrupts the interpreter, usually much later
and somewhere unrelated. Downloads therefore run on a worker thread, and every
progress callback is handed back to the Tk thread with ``after(0, ...)``. No
widget is touched from the worker.

Design fidelity is deliberately partial. The palette, radii and spacing from
docs/DESIGN.md transfer directly; the type system does not, because Tk offers
no letter-spacing or variable-weight control. See docs/DESIGN-APPLICATION.md.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from .. import __version__
from ..core import (
    CancelToken,
    Phase,
    Progress,
    StreamOption,
    VideoInfo,
    YtdlError,
    default_output_dir,
    download,
    fetch_info,
    ffmpeg_available,
    format_bytes,
    format_duration,
)

# --- Tokens from docs/DESIGN.md -------------------------------------------
CANVAS_DARK = "#000000"
SURFACE_ELEVATED = "#16181a"
SURFACE_DEEP = "#0a0a0a"
PRIMARY = "#494fdf"
PRIMARY_BRIGHT = "#4f55f1"
PRIMARY_DEEP = "#3a40c4"
ON_DARK = "#ffffff"
ON_DARK_MUTE = "#b8b8b8"
HAIRLINE = "#2a2d30"
STATUS_OK = "#00a87e"
STATUS_ERR = "#e23b4a"
FAINT = "#c9c9cd"

RADIUS_MD = 12
RADIUS_LG = 20
CONTROL_HEIGHT = 48
INPUT_HEIGHT = 56


class DownloaderApp(ctk.CTk):
    """Main window."""

    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")
        self.title("YouTube Downloader Suite")
        self.geometry("880x680")
        self.minsize(640, 520)
        self.configure(fg_color=CANVAS_DARK)

        self.info: Optional[VideoInfo] = None
        self.cancel_token: Optional[CancelToken] = None
        # docs/DESIGN-APPLICATION.md: while a progress bar is visible it is the
        # one cobalt element on screen, so the featured card stands down.
        self._downloading = False
        self.worker: Optional[threading.Thread] = None
        self.output_dir = default_output_dir()

        # Progress arrives on a worker thread and is drained on the Tk thread.
        self._events: "queue.Queue[tuple]" = queue.Queue()

        self._build()
        self.after(50, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=32, pady=(28, 0))
        ctk.CTkLabel(
            header, text="Download from YouTube.",
            font=ctk.CTkFont(size=34, weight="bold"),
            text_color=ON_DARK,
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="Video up to 4K, or audio as MP3 or M4A.",
            font=ctk.CTkFont(size=14), text_color=ON_DARK_MUTE,
        ).pack(anchor="w", pady=(4, 0))

        # --- URL row
        entry_row = ctk.CTkFrame(self, fg_color="transparent")
        entry_row.grid(row=1, column=0, sticky="ew", padx=32, pady=(24, 16))
        entry_row.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            entry_row, placeholder_text="Paste a YouTube link",
            height=INPUT_HEIGHT, corner_radius=RADIUS_MD,
            fg_color=ON_DARK, text_color="#191c1f",
            placeholder_text_color="#8d969e",
            border_width=0, font=ctk.CTkFont(size=14),
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.url_entry.bind("<Return>", lambda _event: self.on_fetch())

        # The primary action is a white pill on black, per the design system.
        self.fetch_button = ctk.CTkButton(
            entry_row, text="Fetch", command=self.on_fetch,
            height=CONTROL_HEIGHT, width=132,
            corner_radius=CONTROL_HEIGHT // 2,   # pill
            fg_color=ON_DARK, hover_color=FAINT,
            text_color=CANVAS_DARK,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.fetch_button.grid(row=0, column=1)

        # --- Options list
        self.options_frame = ctk.CTkScrollableFrame(
            self, fg_color=SURFACE_DEEP, corner_radius=RADIUS_LG,
            label_text="", scrollbar_button_color=HAIRLINE,
        )
        self.options_frame.grid(row=2, column=0, sticky="nsew", padx=32, pady=(0, 12))
        self.options_frame.grid_columnconfigure(0, weight=1)

        self.placeholder = ctk.CTkLabel(
            self.options_frame,
            text="Paste a link above to see the available qualities.",
            text_color=ON_DARK_MUTE, font=ctk.CTkFont(size=14),
        )
        self.placeholder.grid(row=0, column=0, pady=48)

        # --- Progress
        progress_area = ctk.CTkFrame(self, fg_color="transparent")
        progress_area.grid(row=3, column=0, sticky="ew", padx=32)
        progress_area.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(
            progress_area, height=6, corner_radius=3,
            fg_color=SURFACE_ELEVATED, progress_color=PRIMARY_BRIGHT,
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(0)
        self.progress_bar.grid_remove()

        self.status_label = ctk.CTkLabel(
            progress_area, text="", text_color=ON_DARK_MUTE,
            font=ctk.CTkFont(size=13), anchor="w",
        )
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.cancel_button = ctk.CTkButton(
            progress_area, text="Cancel", command=self.on_cancel,
            height=36, width=100, corner_radius=18,
            fg_color="transparent", border_width=1, border_color=ON_DARK,
            text_color=ON_DARK, hover_color=SURFACE_ELEVATED,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.cancel_button.grid(row=0, column=1, rowspan=2, padx=(16, 0))
        self.cancel_button.grid_remove()

        # --- Footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=32, pady=(12, 20))
        footer.grid_columnconfigure(1, weight=1)

        self.folder_button = ctk.CTkButton(
            footer, text="Change folder…", command=self.on_choose_folder,
            height=32, width=140, corner_radius=16,
            fg_color=SURFACE_ELEVATED, hover_color=HAIRLINE,
            text_color=ON_DARK, font=ctk.CTkFont(size=12),
        )
        self.folder_button.grid(row=0, column=0)

        self.folder_label = ctk.CTkLabel(
            footer, text=f"Saving to {self.output_dir}",
            text_color=ON_DARK_MUTE, font=ctk.CTkFont(size=12), anchor="w",
        )
        self.folder_label.grid(row=0, column=1, sticky="ew", padx=12)

        ffmpeg_note = "ffmpeg ready" if ffmpeg_available() else "ffmpeg missing"
        ctk.CTkLabel(
            footer, text=f"v{__version__}  ·  {ffmpeg_note}",
            text_color=ON_DARK_MUTE if ffmpeg_available() else STATUS_ERR,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=2, sticky="e")

    # ------------------------------------------------------------- fetching

    def on_fetch(self) -> None:
        url = self.url_entry.get().strip()
        if not url or self._busy():
            return

        self.fetch_button.configure(state="disabled", text="Fetching…")
        self._set_status("Reading video details…", ON_DARK_MUTE)
        self._start_worker(self._fetch_worker, url)

    def _fetch_worker(self, url: str) -> None:
        try:
            info = fetch_info(url)
        except YtdlError as exc:
            self._post("error", exc.user_message, exc.hint)
        except Exception as exc:  # pragma: no cover - defensive
            self._post("error", f"Unexpected error: {exc}", None)
        else:
            self._post("info", info)

    def _render_options(self, info: VideoInfo) -> None:
        for child in self.options_frame.winfo_children():
            child.destroy()

        header = ctk.CTkLabel(
            self.options_frame, text=info.title,
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=ON_DARK, anchor="w", wraplength=680, justify="left",
        )
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 2))

        meta = "  ·  ".join(
            part for part in (info.author, format_duration(info.length_seconds)) if part
        )
        ctk.CTkLabel(
            self.options_frame, text=meta, text_color=ON_DARK_MUTE,
            font=ctk.CTkFont(size=12), anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 12))

        ffmpeg_ok = ffmpeg_available()
        for index, option in enumerate(info.options, start=2):
            self._option_row(index, option, ffmpeg_ok)

    def _option_row(self, row: int, option: StreamOption, ffmpeg_ok: bool) -> None:
        blocked = option.requires_ffmpeg and not ffmpeg_ok
        featured = option.recommended and not blocked and not self._downloading

        card = ctk.CTkFrame(
            self.options_frame,
            fg_color=PRIMARY if featured else SURFACE_ELEVATED,
            corner_radius=RADIUS_MD,
            border_width=0 if featured else 1,
            border_color=HAIRLINE,
        )
        card.grid(row=row, column=0, sticky="ew", padx=20, pady=5)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text=option.label, font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ON_DARK, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(18, 12), pady=(14, 2))

        notes = []
        if option.recommended:
            notes.append("Best")
        elif option.is_default:
            notes.append("Default")
        if option.needs_merge:
            notes.append("merged")
        if option.codec:
            notes.append(option.codec)

        detail = "Needs ffmpeg" if blocked else "  ·  ".join(
            [option.filesize_display] + notes
        )
        ctk.CTkLabel(
            card, text=detail,
            text_color=STATUS_ERR if blocked else ON_DARK_MUTE,
            font=ctk.CTkFont(size=12), anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=(18, 12), pady=(0, 14))

        button = ctk.CTkButton(
            card, text="Download",
            command=lambda o=option: self.on_download(o),
            height=36, width=112, corner_radius=18,
            fg_color=ON_DARK, hover_color=FAINT, text_color=CANVAS_DARK,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled" if blocked else "normal",
        )
        button.grid(row=0, column=2, rowspan=2, padx=18, pady=14)

    # ----------------------------------------------------------- downloading

    def on_download(self, option: StreamOption) -> None:
        if self.info is None or self._busy():
            return

        self.cancel_token = CancelToken()
        self._downloading = True
        self._render_options(self.info)   # featured card yields cobalt to the bar
        self.progress_bar.grid()
        self.progress_bar.set(0)
        self.cancel_button.grid()
        self.cancel_button.configure(state="normal")
        self.fetch_button.configure(state="disabled")
        self._set_status(f"Starting {option.label}…", ON_DARK_MUTE)

        self._start_worker(self._download_worker, option)

    def _download_worker(self, option: StreamOption) -> None:
        try:
            result = download(
                self.info.url,
                option,
                output_dir=self.output_dir,
                on_progress=lambda progress: self._post("progress", progress),
                cancel=self.cancel_token,
                info=self.info,
            )
        except YtdlError as exc:
            self._post("error", exc.user_message, exc.hint)
        except Exception as exc:  # pragma: no cover - defensive
            self._post("error", f"Unexpected error: {exc}", None)
        else:
            self._post("done", result)

    def on_cancel(self) -> None:
        if self.cancel_token is not None:
            self.cancel_token.cancel()
            self.cancel_button.configure(state="disabled")
            self._set_status("Cancelling…", ON_DARK_MUTE)

    def on_choose_folder(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=str(self.output_dir))
        if chosen:
            self.output_dir = Path(chosen)
            self.folder_label.configure(text=f"Saving to {self.output_dir}")

    # ------------------------------------------------- thread communication

    def _start_worker(self, target, *args) -> None:
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _post(self, kind: str, *payload) -> None:
        """Called from the worker thread — queue only, never touch widgets."""
        self._events.put((kind, payload))

    def _drain_events(self) -> None:
        """Runs on the Tk thread; the only place widgets are modified."""
        try:
            while True:
                kind, payload = self._events.get_nowait()
                handler = getattr(self, f"_handle_{kind}", None)
                if handler is not None:
                    handler(*payload)
        except queue.Empty:
            pass
        finally:
            self.after(50, self._drain_events)

    # ------------------------------------------------------------- handlers

    def _handle_info(self, info: VideoInfo) -> None:
        self.info = info
        self.fetch_button.configure(state="normal", text="Fetch")
        self._render_options(info)
        self._set_status("", ON_DARK_MUTE)

    def _handle_progress(self, progress: Progress) -> None:
        if progress.fraction is None:
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            self.progress_bar.configure(mode="determinate")
            self.progress_bar.stop()
            self.progress_bar.set(progress.fraction)

        parts = [progress.message or progress.phase.value]
        if progress.percent is not None:
            parts.append(f"{progress.percent:.0f}%")
        if progress.speed_bps:
            parts.append(f"{format_bytes(progress.speed_bps)}/s")
        if progress.eta_seconds is not None:
            parts.append(f"ETA {format_duration(progress.eta_seconds)}")
        self._set_status("  ·  ".join(parts), ON_DARK_MUTE)

    def _handle_done(self, result) -> None:
        self._downloading = False
        if self.info is not None:
            self._render_options(self.info)
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(1)
        self.cancel_button.grid_remove()
        self.fetch_button.configure(state="normal")
        self._set_status(f"Saved  {result.path.name}  ({format_bytes(result.filesize)})",
                         STATUS_OK)

    def _handle_error(self, message: str, hint: Optional[str]) -> None:
        self._downloading = False
        if self.info is not None:
            self._render_options(self.info)
        self.progress_bar.stop()
        self.progress_bar.grid_remove()
        self.cancel_button.grid_remove()
        self.fetch_button.configure(state="normal", text="Fetch")
        self._set_status(f"{message}  {hint or ''}".strip(), STATUS_ERR)

    def _set_status(self, text: str, colour: str) -> None:
        self.status_label.configure(text=text, text_color=colour)

    def _on_close(self) -> None:
        if self.cancel_token is not None:
            self.cancel_token.cancel()
        self.destroy()


def main() -> int:
    app = DownloaderApp()
    app.mainloop()
    return 0


__all__ = ["DownloaderApp", "main"]
