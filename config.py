"""Loads and validates settings from config.ini."""
import configparser
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).parent
CONFIG_PATH = APP_DIR / "config.ini"


@dataclass(frozen=True)
class SyncConfig:
    local: Path
    remote: Path
    debounce_seconds: float
    reconcile_interval: int
    startup_delay: int
    archive_folder: str
    state_file: Path
    log_file: Path
    log_level: str
    ignore_patterns: list[str] = field(default_factory=list)


def _app_relative(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else APP_DIR / p


def load_config(path: Path = CONFIG_PATH) -> SyncConfig:
    """Read config.ini and return a validated SyncConfig."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")

    local = Path(parser.get("paths", "local")).expanduser().resolve()
    remote = Path(parser.get("paths", "icloud")).expanduser().resolve()

    raw = parser.get("ignore", "patterns", fallback="")
    patterns = [p.strip() for p in raw.replace(",", "\n").splitlines() if p.strip()]

    archive = parser.get("sync", "archive_folder", fallback=".sync-archive").strip()
    if not archive:
        raise ValueError("archive_folder cannot be empty")
    if not local.is_dir():
        raise ValueError(f"Local folder does not exist: {local}")
    if local == remote or remote.is_relative_to(local) or local.is_relative_to(remote):
        raise ValueError("The two folders cannot be the same or nested inside each other")

    return SyncConfig(
        local=local,
        remote=remote,
        debounce_seconds=parser.getfloat("sync", "debounce_seconds", fallback=3.0),
        reconcile_interval=parser.getint("sync", "reconcile_interval", fallback=300),
        startup_delay=parser.getint("sync", "startup_delay", fallback=60),
        archive_folder=archive,
        state_file=_app_relative(parser.get("sync", "state_file", fallback="sync_state.json")),
        log_file=_app_relative(parser.get("logging", "log_file", fallback="sync.log")),
        log_level=parser.get("logging", "level", fallback="INFO").upper(),
        ignore_patterns=patterns,
    )
