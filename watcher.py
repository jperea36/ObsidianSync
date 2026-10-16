"""Watchdog event handler and debounce worker (local -> iCloud)."""
import logging
import os
import threading
import time

from watchdog.events import FileSystemEvent, FileSystemEventHandler

import sync_actions
from config import SyncConfig
from state import SyncState

log = logging.getLogger(__name__)


class SyncEventHandler(FileSystemEventHandler):
    """Records events into a shared pending dict keyed by path; latest action wins.

    That collapses noisy save patterns, e.g. "delete note.md, rename temp ->
    note.md" becomes a single copy of note.md.
    """

    def __init__(self, cfg: SyncConfig, pending: dict, lock: threading.Lock):
        super().__init__()
        self.cfg = cfg
        self.pending = pending  # {path: (action, extra, timestamp)}
        self.lock = lock

    def _ignored(self, path) -> bool:
        return sync_actions.should_ignore(os.fsdecode(path), self.cfg)

    def _record(self, path, action: str, extra: str | None = None) -> None:
        path = os.fsdecode(path)
        with self.lock:
            existing = self.pending.get(path)
            if existing and existing[0] == "move" and action == "copy":
                action, extra = "move", existing[1]  # push_move also copies
            self.pending[path] = (action, extra, time.monotonic())

    def on_created(self, event: FileSystemEvent) -> None:
        if not self._ignored(event.src_path):
            self._record(event.src_path, "copy")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and not self._ignored(event.src_path):
            self._record(event.src_path, "copy")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not self._ignored(event.src_path):
            self._record(event.src_path, "delete")

    def on_moved(self, event: FileSystemEvent) -> None:
        src_ignored, dest_ignored = self._ignored(event.src_path), self._ignored(event.dest_path)
        if src_ignored and dest_ignored:
            return
        if dest_ignored:
            self._record(event.src_path, "delete")
        elif src_ignored:
            self._record(event.dest_path, "copy")
        else:
            self._record(event.dest_path, "move", os.fsdecode(event.src_path))


class DebounceWorker(threading.Thread):
    """Flushes pending events once they've been quiet for debounce_seconds."""

    def __init__(self, cfg: SyncConfig, state: SyncState, pending: dict,
                 lock: threading.Lock, stop_event: threading.Event):
        super().__init__(daemon=True, name="DebounceWorker")
        self.cfg, self.state = cfg, state
        self.pending, self.lock, self.stop_event = pending, lock, stop_event

    def run(self) -> None:
        while not self.stop_event.wait(0.5):
            self.flush()
        self.flush(force=True)

    def flush(self, force: bool = False) -> None:
        now = time.monotonic()
        with self.lock:
            ready = [(p, v) for p, v in self.pending.items()
                     if force or now - v[2] >= self.cfg.debounce_seconds]
            for path, _ in ready:
                del self.pending[path]
        if not ready:
            return

        for path, (action, extra, _ts) in sorted(ready, key=lambda item: item[1][2]):
            try:
                if action == "copy":
                    sync_actions.push_item(path, self.cfg, self.state)
                elif action == "delete":
                    sync_actions.push_delete(path, self.cfg, self.state)
                elif action == "move":
                    sync_actions.push_move(extra, path, self.cfg, self.state)
            except OSError as e:
                log.warning("Could not %s %s: %s", action, path, e)
            except Exception:
                log.exception("Failed to %s %s", action, path)

        try:
            self.state.save()
        except Exception:
            log.exception("Could not save state")
