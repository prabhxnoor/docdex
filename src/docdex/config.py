"""Project discovery, configuration, and on-disk layout.

A docdex project is any directory containing a `.docdex.json` marker. All
derived data lives under `<root>/<index_dir>/_state/`; the only user-facing
content inside the index dir is the curated markdown files, the `Update/`
inbox, and `vision_notes/`.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

MARKER_NAME = ".docdex.json"
DEFAULT_INDEX_DIR = "_index"
DEFAULT_WRAPPER = "ctx"
STATE_DIR = "_state"
UPDATE_DIR = "Update"
NOTES_DIR = "vision_notes"

# Directory names skipped at any depth, in addition to per-project skip_dirs.
BUILTIN_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".docdex", ".claude", ".vscode", ".idea",
    "__pycache__", ".venv", "venv", "node_modules", ".next", ".cache",
    ".pytest_cache", ".mypy_cache", ".Trash",
}
# File names skipped at any depth.
SKIP_FILE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}

# Cache stems longer than this get truncated; the rel-path digest keeps them
# unique. 140 bytes leaves headroom under common 255-byte filename limits.
MAX_STEM_BYTES = 140


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate_utf8(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


class DocdexError(Exception):
    """Base class for docdex errors the CLI should report cleanly (exit 2)."""


class NotAProject(DocdexError):
    """Raised when no `.docdex.json` marker is found at or above a path."""


class ConfigError(DocdexError):
    """Raised when a `.docdex.json` marker is malformed or unsafe."""


class StateError(DocdexError):
    """Raised when a derived state file is corrupt and cannot be parsed."""


def validate_index_dir(name: str) -> str:
    """A project's index dir must be a single, in-project folder name.

    Rejecting separators, `.`/`..`, and absolute paths is what keeps a
    corrupt or hostile `.docdex.json` from steering writes — and `purge`
    deletes — outside the project root.
    """
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("index_dir must be a non-empty folder name")
    if name in (".", ".."):
        raise ConfigError("index_dir may not be '.' or '..'")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ConfigError(
            f"index_dir must be a plain folder name, not a path: {name!r}")
    if PurePosixPath(name).is_absolute() or Path(name).is_absolute():
        raise ConfigError(f"index_dir must be relative, not absolute: {name!r}")
    return name


class Project:
    def __init__(self, root: Path, config: dict):
        self.root = root.resolve()
        self.config = config
        self.index_dir_name: str = validate_index_dir(
            config.get("index_dir", DEFAULT_INDEX_DIR))
        self.wrapper_name: str = config.get("wrapper", DEFAULT_WRAPPER)
        self.skip_dirs = set(config.get("skip_dirs", [])) | BUILTIN_SKIP_DIRS
        self.follow_symlinks: bool = bool(config.get("follow_symlinks", False))

    def is_within_root(self, path: Path) -> bool:
        """True iff `path` resolves to somewhere inside the project root.

        Defense in depth for every destructive or write operation: even if a
        config value slipped past validation, nothing acts outside the root.
        """
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return resolved == self.root or self.root in resolved.parents

    # ---------------------------------------------------------------- layout
    @property
    def marker_path(self) -> Path:
        return self.root / MARKER_NAME

    @property
    def index_dir(self) -> Path:
        return self.root / self.index_dir_name

    @property
    def update_dir(self) -> Path:
        return self.index_dir / UPDATE_DIR

    @property
    def notes_dir(self) -> Path:
        return self.index_dir / NOTES_DIR

    @property
    def state_dir(self) -> Path:
        return self.index_dir / STATE_DIR

    @property
    def extracted_dir(self) -> Path:
        return self.state_dir / "extracted"

    @property
    def dumps_dir(self) -> Path:
        return self.state_dir / "context_dumps"

    @property
    def vision_dir(self) -> Path:
        return self.state_dir / "vision_tasks"

    @property
    def inventory_path(self) -> Path:
        return self.state_dir / "inventory.tsv"

    @property
    def history_path(self) -> Path:
        return self.state_dir / "inventory_history.tsv"

    @property
    def extract_status_path(self) -> Path:
        return self.state_dir / "extract_status.tsv"

    @property
    def errors_log(self) -> Path:
        return self.state_dir / "errors.log"

    @property
    def last_run_path(self) -> Path:
        return self.state_dir / "LAST_RUN.txt"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / ".sync.lock"

    @property
    def index_db_path(self) -> Path:
        return self.state_dir / "index.db"

    @property
    def semantic_index_path(self) -> Path:
        return self.state_dir / "semantic_index.jsonl"

    @property
    def semantic_manifest_path(self) -> Path:
        return self.state_dir / "semantic_manifest.tsv"

    @property
    def semantic_meta_path(self) -> Path:
        return self.state_dir / "semantic_meta.json"

    # The only subtrees of the index dir that the walker may index.
    @property
    def indexable_index_subdirs(self) -> tuple[str, ...]:
        return (
            f"{self.index_dir_name}/{UPDATE_DIR}",
            f"{self.index_dir_name}/{NOTES_DIR}",
        )

    # ----------------------------------------------------------- cache names
    def cache_path_for(self, rel: str) -> Path:
        """Deterministic cache path for a source rel-path.

        The sha1 digest of the *full* rel path makes the mapping injective:
        `A B.docx` and `A_B.docx` flatten identically but get distinct
        digests, so they can never overwrite each other's cache.
        """
        digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
        flat = rel.replace("/", "__").replace(" ", "_")
        stem = _truncate_utf8(Path(flat).stem, MAX_STEM_BYTES)
        parts = PurePosixPath(rel).parts
        update_prefix, notes_prefix = self.indexable_index_subdirs
        if len(parts) == 1:
            sub = "_root"
        elif rel.startswith(update_prefix + "/"):
            sub = "inbox"
        elif rel.startswith(notes_prefix + "/"):
            sub = "vision_notes"
        else:
            sub = parts[0].replace(" ", "_")
        return self.extracted_dir / sub / f"{stem}.{digest}.txt"

    def rel_to_root(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    # ------------------------------------------------------------ top folder
    def top_folder_for(self, rel: str) -> str:
        """Logical top-level grouping for dumps and reports."""
        update_prefix, notes_prefix = self.indexable_index_subdirs
        if rel.startswith(update_prefix + "/"):
            return "Update"
        if rel.startswith(notes_prefix + "/"):
            return "vision_notes"
        return rel.split("/", 1)[0] if "/" in rel else "_root"

    # ------------------------------------------------------------ marker I/O
    def save(self) -> None:
        self.config["index_dir"] = self.index_dir_name
        self.marker_path.write_text(
            json.dumps(self.config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def create(cls, root: Path, index_dir: str = DEFAULT_INDEX_DIR,
               wrapper: str = DEFAULT_WRAPPER, skip_dirs: Optional[list] = None) -> "Project":
        from docdex import __version__
        config = {
            "docdex_schema": 1,
            "created_with": __version__,
            "created_at": utc_now_iso(),
            "index_dir": index_dir,
            "wrapper": wrapper,
            "skip_dirs": sorted(skip_dirs or []),
        }
        return cls(root, config)

    @classmethod
    def load(cls, root: Path) -> "Project":
        marker = root / MARKER_NAME
        if not marker.is_file():
            raise NotAProject(f"no {MARKER_NAME} in {root}")
        try:
            config = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ConfigError(
                f"{MARKER_NAME} is corrupt and could not be read ({e}). "
                "Fix or recreate it with `docdex init`.")
        if not isinstance(config, dict):
            raise ConfigError(f"{MARKER_NAME} must contain a JSON object")
        return cls(root, config)

    @classmethod
    def discover(cls, start: Optional[Path] = None) -> "Project":
        """Walk upward from `start` (default: cwd) to find the project root."""
        cur = (start or Path.cwd()).resolve()
        for candidate in [cur, *cur.parents]:
            if (candidate / MARKER_NAME).is_file():
                return cls.load(candidate)
        raise NotAProject(
            f"no {MARKER_NAME} found at or above {cur}. "
            "Run `docdex init` in the project root, or pass --root."
        )


def ensure_state_dirs(project: Project) -> None:
    for d in (
        project.index_dir, project.update_dir, project.notes_dir,
        project.state_dir, project.extracted_dir, project.dumps_dir,
        project.vision_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
