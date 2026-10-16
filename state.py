"""Remembers each file's (size, mtime) on BOTH sides as of the last time they matched.

Tracking each side separately means a cloud client re-stamping a file's
mtime after upload is recognized as "touched, same content" instead of
"edited", and lets the reconciler tell which side actually changed:
  - one side only, unchanged since last sync -> it was deleted on the other side
  - one side only, new or changed            -> copy it across
"""
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

Snapshot = tuple[int, float]           # (size, mtime)
Pair = tuple[Snapshot, Snapshot]       # (local, icloud)


class SyncState:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.files: dict[str, Pair] = {}

    def __len__(self) -> int:
        return len(self.files)

    def load(self) -> None:
        if not self.path.exists():
            log.info("No state file yet; first run will only copy, never delete")
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == 1:  # old single-snapshot format
                self.files = {k: ((int(v[0]), float(v[1])),) * 2 for k, v in data["files"].items()}
            else:
                self.files = {k: ((int(l[0]), float(l[1])), (int(r[0]), float(r[1])))
                              for k, (l, r) in data["files"].items()}
        except Exception:
            # Starting empty is safe: it just means no deletions get propagated
            log.exception("State file unreadable; starting fresh")
            self.files = {}

    def save(self) -> None:
        with self.lock:
            payload = json.dumps({"version": 2, "files": self.files})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str) -> Pair | None:
        with self.lock:
            return self.files.get(key)

    def set(self, key: str, local: Snapshot, icloud: Snapshot) -> None:
        with self.lock:
            self.files[key] = (local, icloud)

    def _under(self, key: str, prefix: str) -> bool:
        return key == prefix or key.startswith(prefix + os.sep)

    def remove_tree(self, prefix: str) -> None:
        with self.lock:
            for k in [k for k in self.files if self._under(k, prefix)]:
                del self.files[k]

    def rename_tree(self, old: str, new: str) -> None:
        with self.lock:
            for k in [k for k in self.files if self._under(k, old)]:
                self.files[new + k[len(old):]] = self.files.pop(k)

    def prune(self, keep: "set[str]") -> None:
        with self.lock:
            self.files = {k: v for k, v in self.files.items() if k in keep}
