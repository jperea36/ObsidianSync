"""Entry point: two-way reconcile on startup/wake/interval, live local -> iCloud push."""
import logging
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from watchdog.observers import Observer

from config import SyncConfig, load_config
from reconcile import reconcile
from state import SyncState
from watcher import DebounceWorker, SyncEventHandler

log = logging.getLogger("folder_sync")

TICK = 15          # seconds between sleep/interval checks
SLEEP_GAP = 60     # a tick this late means the machine was asleep


def setup_logging(cfg: SyncConfig) -> None:
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s: %(message)s")

    file_handler = RotatingFileHandler(cfg.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    root.addHandler(file_handler)

    if sys.stderr is not None:  # pythonw has no console
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    sys.excepthook = lambda *exc: log.critical("Unhandled exception", exc_info=exc)
    threading.excepthook = lambda a: log.critical(
        "Unhandled exception in %s", a.thread.name, exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def run_reconcile(cfg: SyncConfig, state: SyncState) -> None:
    try:
        reconcile(cfg, state)
    except Exception:
        log.exception("Reconcile failed")


def reconcile_loop(cfg: SyncConfig, state: SyncState, stop_event: threading.Event) -> None:
    """Reconcile after startup, after waking from sleep, and every reconcile_interval."""
    if stop_event.wait(cfg.startup_delay):
        return
    run_reconcile(cfg, state)

    last_tick = time.time()
    next_due = last_tick + cfg.reconcile_interval
    while not stop_event.wait(TICK):
        now = time.time()
        if now - last_tick > TICK + SLEEP_GAP:
            log.info("Woke from sleep (~%.0f min); reconciling after %ds", (now - last_tick) / 60, cfg.startup_delay)
            if stop_event.wait(cfg.startup_delay):
                return
            run_reconcile(cfg, state)
            next_due = time.time() + cfg.reconcile_interval
        elif now >= next_due:
            run_reconcile(cfg, state)
            next_due = time.time() + cfg.reconcile_interval
        last_tick = time.time()


def start_observer(cfg: SyncConfig, handler: SyncEventHandler) -> Observer:
    observer = Observer()
    observer.schedule(handler, str(cfg.local), recursive=True)
    observer.start()
    return observer


def main() -> None:
    try:
        cfg = load_config()
    except Exception:
        Path(__file__).with_name("startup_error.log").write_text(traceback.format_exc())
        raise

    setup_logging(cfg)
    log.info("Starting: %s <-> %s", cfg.local, cfg.remote)

    state = SyncState(cfg.state_file)
    state.load()

    pending: dict = {}
    lock = threading.Lock()
    stop_event = threading.Event()
    handler = SyncEventHandler(cfg, pending, lock)

    observer = start_observer(cfg, handler)
    worker = DebounceWorker(cfg, state, pending, lock, stop_event)
    worker.start()
    threading.Thread(target=reconcile_loop, args=(cfg, state, stop_event),
                     daemon=True, name="Reconcile").start()

    try:
        while True:
            observer.join(1)
            if not observer.is_alive():
                log.error("Watcher stopped unexpectedly; restarting it and reconciling")
                observer = start_observer(cfg, handler)
                threading.Thread(target=run_reconcile, args=(cfg, state), daemon=True).start()
    except KeyboardInterrupt:
        log.info("Stopping...")
    finally:
        stop_event.set()
        observer.stop()
        observer.join()
        worker.join(timeout=30)
        log.info("Stopped")


if __name__ == "__main__":
    main()
