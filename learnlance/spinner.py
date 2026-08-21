"""A tiny animated terminal spinner, à la Claude Code's thinking indicator.

Shows a moving loader on the current line while a slow task runs, then wipes
itself clean so the real output can take its place. No dependencies; degrades
gracefully to a one-line message when stdout isn't a TTY.
"""
from __future__ import annotations

import itertools
import sys
import threading
import time

# Braille dots give a smooth "in motion" feel, like Claude's loader.
_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
# ASCII fallback for consoles that can't encode braille (e.g. legacy cp1252).
_ASCII_FRAMES = ["|", "/", "-", "\\"]
_INTERVAL = 0.08


def _frames_for(stream) -> list[str]:
    """Pick braille frames if the stream's encoding can render them, else ASCII."""
    enc = getattr(stream, "encoding", None)
    if enc:
        try:
            "".join(_FRAMES).encode(enc)
            return _FRAMES
        except (UnicodeEncodeError, LookupError):
            pass
    return _ASCII_FRAMES


class Spinner:
    """Context manager that animates a label until the block exits.

    Usage:
        with Spinner("loading"):
            do_slow_work()
        print(result)   # spinner line already cleared
    """

    def __init__(self, label: str = "loading", stream=None, min_duration: float = 0.4):
        self.label = label
        self.stream = stream or sys.stdout
        self.min_duration = min_duration  # floor so fast tasks still flash it
        self._start = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Only animate for interactive terminals; otherwise stay quiet-ish.
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._frames = _frames_for(self.stream)
        self._ell = "…" if self._frames is _FRAMES else "..."

    def _run(self) -> None:
        for frame in itertools.cycle(self._frames):
            if self._stop.is_set():
                break
            self.stream.write(f"\r{frame} {self.label}{self._ell} ")
            self.stream.flush()
            time.sleep(_INTERVAL)

    def __enter__(self) -> "Spinner":
        self._start = time.monotonic()
        if self._tty:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            # Non-TTY (piped/redirected): a single static note, no animation.
            self.stream.write(f"{self.label}{self._ell}\n")
            self.stream.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Keep the animation up for a brief floor so quick tasks still show it.
        if self._tty and self._thread is not None:
            remaining = self.min_duration - (time.monotonic() - self._start)
            if remaining > 0:
                time.sleep(remaining)
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if self._tty:
            # Erase the spinner line by overwriting with spaces (portable —
            # avoids ANSI escapes that some Windows consoles don't handle).
            self.stream.write("\r" + " " * (len(self.label) + 6) + "\r")
            self.stream.flush()
