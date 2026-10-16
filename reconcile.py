"""Two-way comparison: the side that changed wins; if both changed, newest wins."""
import logging
from pathlib import Path

from config import SyncConfig
from state import Snapshot, SyncState
from sync_actions import archive, copy_over, in_sync, io_lock, iter_files, rel_key, same, snapshot

log = logging.getLogger(__name__)

# Refuse to propagate deletions above this, in case a folder is temporarily
# empty or unavailable (iCloud signed out, not finished downloading, etc.)
MAX_DELETE_FRACTION = 0.3
MAX_DELETE_FLOOR = 10


def scan(root: Path, cfg: SyncConfig) -> dict[str, tuple[Path, Snapshot]]:
    found = {}
    for f in iter_files(root, cfg):
        snap = snapshot(f)
        if snap:
            rel = f.relative_to(root)
            found[rel_key(rel)] = (rel, snap)
    return found


def reconcile(cfg: SyncConfig, state: SyncState) -> None:
    with io_lock:
        if not cfg.remote.is_dir():
            log.error("iCloud folder not found (%s); skipping reconcile", cfg.remote)
            return

        local, remote = scan(cfg.local, cfg), scan(cfg.remote, cfg)
        plan: list[tuple[str, Path, str]] = []

        for key in local.keys() | remote.keys():
            l, r, known = local.get(key), remote.get(key), state.get(key)
            known_l, known_r = known if known else (None, None)

            if l and r:
                rel = l[0]
                l_changed, r_changed = not same(l[1], known_l), not same(r[1], known_r)
                if not l_changed and not r_changed:
                    continue
                if in_sync(cfg.local / rel, cfg.remote / rel, l[1], r[1]):
                    state.set(key, l[1], r[1])  # e.g. cloud client re-stamped the mtime
                elif l_changed and not r_changed:
                    plan.append(("to_icloud", rel, key))
                elif r_changed and not l_changed:
                    plan.append(("to_local", rel, key))
                else:  # both changed: newest wins, loser is archived by copy_over
                    plan.append(("to_icloud" if l[1][1] > r[1][1] else "to_local", rel, key))
            elif l:
                # Unchanged here and gone there -> deleted in iCloud
                plan.append(("archive_local" if same(l[1], known_l) else "to_icloud", l[0], key))
            else:
                plan.append(("archive_icloud" if same(r[1], known_r) else "to_local", r[0], key))

        deletes = [p for p in plan if p[0].startswith("archive")]
        side_empty = (not local or not remote) and len(state) > 0
        limit = max(MAX_DELETE_FLOOR, len(state) * MAX_DELETE_FRACTION)
        if deletes and (side_empty or len(deletes) > limit):
            log.error("Refusing to archive %d files (looks like a folder is empty or unavailable). "
                      "Check both folders; deletions will be retried next reconcile.", len(deletes))
            plan = [p for p in plan if not p[0].startswith("archive")]

        state.prune(local.keys() | remote.keys())

        counts = dict.fromkeys(("to_icloud", "to_local", "archive_local", "archive_icloud"), 0)
        failed = 0
        for action, rel, key in plan:
            try:
                if action == "to_icloud":
                    done = copy_over(rel, True, cfg, state)
                elif action == "to_local":
                    done = copy_over(rel, False, cfg, state)
                else:
                    root = cfg.local if action == "archive_local" else cfg.remote
                    archive(root / rel, root, cfg, "deleted")
                    state.remove_tree(key)
                    done = True
                counts[action] += int(bool(done))
            except OSError as e:
                failed += 1
                log.warning("Could not sync %s (%s): %s", rel, action, e)
            except Exception:
                failed += 1
                log.exception("Reconcile failed for %s (%s)", rel, action)

        state.save()
        log.info("Reconcile done: %d -> iCloud, %d -> local, %d archived locally, "
                 "%d archived in iCloud, %d failed",
                 counts["to_icloud"], counts["to_local"], counts["archive_local"],
                 counts["archive_icloud"], failed)
