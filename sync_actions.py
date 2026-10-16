"""Low-level file operations shared by the watcher and the reconciler."""
import filecmp
import fnmatch
import logging
import os
import shutil
import stat
import threading
import time
from pathlib import Path

from config import SyncConfig
from state import Snapshot, SyncState

log = logging.getLogger(__name__)

io_lock = threading.RLock()  # serializes all writes between reconciler and watcher

MTIME_TOLERANCE = 2.0
TEMP_SUFFIX = ".synctmp"     # in-progress copies; always ignored


# ---------- helpers ----------

def rel_key(rel: Path) -> str:
    """State key for a relative path (case-insensitive on Windows)."""
    return os.path.normcase(str(rel))


def is_ignored_name(name: str, cfg: SyncConfig) -> bool:
    return (name == cfg.archive_folder or name.endswith(TEMP_SUFFIX)
            or any(fnmatch.fnmatch(name, p) for p in cfg.ignore_patterns))


def should_ignore(path: str, cfg: SyncConfig) -> bool:
    """True if any part of a local path is ignored (or it's outside the local folder)."""
    try:
        rel = Path(path).relative_to(cfg.local)
    except ValueError:
        return True
    return any(is_ignored_name(part, cfg) for part in rel.parts)


def snapshot(path: Path) -> Snapshot | None:
    """(size, mtime) for a file, or None if it's missing or a directory."""
    try:
        st = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    if stat.S_ISDIR(st.st_mode):
        return None
    return st.st_size, st.st_mtime


def same(a: Snapshot | None, b: Snapshot | None) -> bool:
    return a is not None and b is not None and a[0] == b[0] and abs(a[1] - b[1]) <= MTIME_TOLERANCE


def files_equal(a: Path, b: Path) -> bool:
    """Byte-for-byte comparison; False if either can't be read."""
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def in_sync(lp: Path, rp: Path, l: Snapshot | None, r: Snapshot | None) -> bool:
    """Same size+mtime, or same size and identical bytes (e.g. mtime re-stamped by a cloud client)."""
    return l is not None and r is not None and (same(l, r) or (l[0] == r[0] and files_equal(lp, rp)))


def with_retry(func, *args, attempts: int = 5, delay: float = 1.0):
    """Call func(*args), retrying on PermissionError (file locked by Obsidian/iCloud/OneDrive)."""
    for attempt in range(1, attempts + 1):
        try:
            return func(*args)
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(delay)


def iter_files(root: Path, cfg: SyncConfig):
    """Yield every non-ignored file under root, skipping symlinks and junctions."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as e:
            log.warning("Cannot read %s: %s", current, e)
            continue
        for entry in entries:
            if is_ignored_name(entry.name, cfg):
                continue
            if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy to a temp file beside dst, then swap it in.

    - Obsidian never sees a half-written note.
    - A failed copy never deletes dst (which the watcher would read as a deletion).
    - Falls back to a plain read/write when Windows' CopyFile2 rejects a
      cloud placeholder (WinError 380 and friends).
    """
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}{TEMP_SUFFIX}")
    try:
        try:
            shutil.copy2(src, tmp)
        except PermissionError:
            raise
        except OSError:
            with open(src, "rb") as fs, open(tmp, "wb") as fd:
                shutil.copyfileobj(fs, fd, 1024 * 1024)
            shutil.copystat(src, tmp)
        os.replace(tmp, dst)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ---------- core operations ----------

def archive(path: Path, root: Path, cfg: SyncConfig, reason: str) -> None:
    """Move a file/folder into root/<archive_folder>/ with a timestamp instead of losing it."""
    rel = path.relative_to(root)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{rel.name}.{stamp}.{reason}" if path.is_dir() else f"{rel.stem}.{stamp}.{reason}{rel.suffix}"
    target = root / cfg.archive_folder / rel.parent / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with_retry(shutil.move, str(path), str(target))
    log.info("Archived (%s) %s", reason, path)


def record(rel: Path, cfg: SyncConfig, state: SyncState) -> None:
    """Remember both sides' current snapshots as 'in sync'."""
    l, r = snapshot(cfg.local / rel), snapshot(cfg.remote / rel)
    if l and r:
        state.set(rel_key(rel), l, r)


def copy_over(rel: Path, to_icloud: bool, cfg: SyncConfig, state: SyncState) -> bool:
    """Copy one side's file over the other. Archives the target first if it changed since last sync."""
    lp, rp = cfg.local / rel, cfg.remote / rel
    src, dst, dst_root = (lp, rp, cfg.remote) if to_icloud else (rp, lp, cfg.local)
    s, d = snapshot(src), snapshot(dst)
    if s is None:
        return False
    if in_sync(src, dst, s, d):
        record(rel, cfg, state)
        return False
    if d is not None:
        known = state.get(rel_key(rel))
        known_dst = None if known is None else known[1 if to_icloud else 0]
        if not same(d, known_dst):
            archive(dst, dst_root, cfg, "conflict")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with_retry(_atomic_copy, src, dst)
    record(rel, cfg, state)
    log.info("Copied   %s -> %s", src, dst)
    return True


# ---------- watcher actions (local -> iCloud) ----------

def _rel(local_path: str, cfg: SyncConfig) -> Path:
    return Path(local_path).relative_to(cfg.local)


def push_item(local_path: str, cfg: SyncConfig, state: SyncState) -> None:
    """Push a created/modified local file or folder to iCloud."""
    src = Path(local_path)
    with io_lock:
        if src.is_dir():
            (cfg.remote / _rel(local_path, cfg)).mkdir(parents=True, exist_ok=True)
            for f in iter_files(src, cfg):
                copy_over(f.relative_to(cfg.local), True, cfg, state)
        elif src.exists():
            copy_over(_rel(local_path, cfg), True, cfg, state)


def push_delete(local_path: str, cfg: SyncConfig, state: SyncState) -> None:
    """Archive the iCloud copy of a locally deleted file, unless iCloud has edits we haven't seen."""
    rel = _rel(local_path, cfg)
    key, dst = rel_key(rel), cfg.remote / rel
    with io_lock:
        if Path(local_path).exists():
            return  # recreated before the debounce fired
        if not dst.exists():
            state.remove_tree(key)
            return
        if dst.is_file():
            known = state.get(key)
            if known is None:
                log.warning("Kept %s: deleted locally but never synced, so it will be restored", dst)
                return
            if not same(snapshot(dst), known[1]):
                log.warning("Kept %s: deleted locally but changed in iCloud since last sync", dst)
                return
        archive(dst, cfg.remote, cfg, "deleted")
        state.remove_tree(key)


def push_move(old_local: str, new_local: str, cfg: SyncConfig, state: SyncState) -> None:
    """Mirror a local rename/move in iCloud, then make sure content is current."""
    old_rel, new_rel = _rel(old_local, cfg), _rel(new_local, cfg)
    old_dst, new_dst = cfg.remote / old_rel, cfg.remote / new_rel
    with io_lock:
        if old_dst.exists() and not new_dst.exists():
            new_dst.parent.mkdir(parents=True, exist_ok=True)
            with_retry(shutil.move, str(old_dst), str(new_dst))
            state.rename_tree(rel_key(old_rel), rel_key(new_rel))
            log.info("Moved    %s -> %s", old_dst, new_dst)
        elif old_dst.exists():
            push_delete(old_local, cfg, state)
        push_item(new_local, cfg, state)
