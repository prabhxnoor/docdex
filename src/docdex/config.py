"""Project discovery, configuration, and on-disk layout.

A v2 docdex project keeps one hidden in-project home, `<root>/.docdex/`,
holding the config marker (`config.json`), the PDF-password `secrets.json`,
the `Update/` inbox, `vision_notes/`, and curated markdown. All big,
rebuildable state (extracted caches, the SQLite index, the semantic index)
lives OUTSIDE the project in a per-machine cache (see `cache_base`), so two
machines syncing the same folder never share — or corrupt — one database.

A legacy v1 project (a `.docdex.json` marker at the root and an in-project
`_index/_state/`) still loads and keeps using its in-project state until it is
migrated.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

LEGACY_MARKER_NAME = ".docdex.json"      # v1: marker at the project root
MARKER_NAME = LEGACY_MARKER_NAME         # back-compat alias for the v1 marker
DEFAULT_INDEX_DIR = ".docdex"            # v2: the single hidden in-project home
DEFAULT_WRAPPER = "ctx"
CONFIG_NAME = "config.json"              # v2: marker/config inside the home
SECRETS_NAME = "secrets.json"            # v2: PDF passwords inside the home
LEGACY_SECRETS_NAME = ".docdex.secrets.json"   # v1: at the project root
STATE_DIR = "_state"
UPDATE_DIR = "Update"
NOTES_DIR = "vision_notes"
META_NAME = "meta.json"                  # external cache: records its project root

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

# Default per-file extraction cap (MB). A supported text file larger than this
# is recorded as `skipped` rather than extracted, so one giant log/export can't
# balloon the index. Override per-project via `max_extract_mb`, or per-run with
# `--allow-large-text`. Set `max_extract_mb` to 0 to disable the cap.
DEFAULT_MAX_EXTRACT_MB = 50


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate_utf8(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def cache_base() -> Path:
    """Per-machine base dir for all docdex caches (rebuildable state).

    Deliberately OUTSIDE any project, so two machines syncing the same folder
    never share — and never corrupt — one index database. Resolution order:
    ``$DOCDEX_CACHE_DIR`` (explicit override), then ``$XDG_CACHE_HOME/docdex``,
    then ``~/.cache/docdex``.
    """
    env = os.environ.get("DOCDEX_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "docdex"
    return Path.home() / ".cache" / "docdex"


def project_cache_id(root: Path) -> str:
    """A stable, filesystem-safe id for a project's external cache dir.

    A readable slug (the folder name, sanitized) plus a sha256 digest of the
    resolved absolute path. The digest makes the mapping injective: two roots
    whose names sanitize identically (``A B`` vs ``A_B``) still get distinct
    ids, so they can never share a cache dir.
    """
    resolved = str(root.resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", root.name)[:40] or "project"
    return f"{slug}-{digest}"


def is_within(path: Path, base: Path) -> bool:
    """True iff ``path`` resolves to ``base`` or somewhere inside it.

    Resolves symlinks and ``..`` before comparing, so neither can steer a
    write or a delete outside ``base``. This is the single confinement check
    behind both the in-project home (base = project root) and the external
    cache (base = the docdex cache base).
    """
    try:
        p = path.resolve()
        b = base.resolve()
    except OSError:
        return False
    return p == b or b in p.parents


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
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ConfigError(
            f"index_dir may not contain control characters: {name!r}")
    if "~" in name:
        raise ConfigError(
            f"index_dir may not contain '~' (home expansion): {name!r}")
    if PurePosixPath(name).is_absolute() or Path(name).is_absolute():
        raise ConfigError(f"index_dir must be relative, not absolute: {name!r}")
    return name


class Project:
    def __init__(self, root: Path, config: dict, legacy: bool = False):
        self.root = root.resolve()
        self.config = config
        # legacy (v1): marker at the root, state in-project under the index dir.
        # New (v2): marker inside the hidden home, state in the external cache.
        self.legacy: bool = bool(legacy)
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

    def index_confinement_error(self) -> Optional[str]:
        """Why the index dir is unsafe to operate through, or None if it's safe.

        The shared confinement check behind both the state-*write* guard
        (`ensure_state_dirs`, which raises) and the state-*delete* guard
        (`purge`, which refuses and returns nonzero). A pre-planted symlink
        named like the index dir — or any index dir that resolves outside the
        root — must never steer a `_state/` write, or a later delete, outside
        the project. Source files are never touched regardless.
        """
        idx = self.index_dir
        if idx.is_symlink():
            return (f"index dir {self.index_dir_name!r} is a symlink; refusing "
                    "to operate through it (it could point outside the project)")
        if idx.exists() and not self.is_within_root(idx):
            return (f"index dir {self.index_dir_name!r} resolves outside the "
                    "project root; refusing to operate")
        return None

    def state_confinement_error(self) -> Optional[str]:
        """Why the external cache dir is unsafe to operate through, or None.

        The v2 state lives under the cache base; refuse to write — or, in
        `purge`, delete — if the per-project cache dir is a symlink or resolves
        outside the base, so a tampered cache can never steer an operation
        elsewhere. The external analogue of `index_confinement_error`.
        """
        base = cache_base()
        cdir = self.cache_dir
        if cdir.is_symlink():
            return (f"cache dir {cdir} is a symlink; refusing to operate "
                    "through it (it could point outside the cache base)")
        if cdir.exists() and not is_within(cdir, base):
            return (f"cache dir {cdir} resolves outside the cache base {base}; "
                    "refusing to operate")
        return None

    @property
    def max_extract_bytes(self) -> int:
        """Per-file extraction cap in bytes (0 = no cap)."""
        try:
            mb = int(self.config.get("max_extract_mb", DEFAULT_MAX_EXTRACT_MB))
        except (TypeError, ValueError):
            mb = DEFAULT_MAX_EXTRACT_MB
        return max(0, mb) * 1024 * 1024

    # ---------------------------------------------------------------- layout
    @property
    def config_path(self) -> Path:
        """v2 marker/config — lives inside the hidden home."""
        return self.index_dir / CONFIG_NAME

    @property
    def legacy_marker_path(self) -> Path:
        """v1 marker — at the project root."""
        return self.root / LEGACY_MARKER_NAME

    @property
    def marker_path(self) -> Path:
        """The active marker file for this project's layout."""
        return self.legacy_marker_path if self.legacy else self.config_path

    @property
    def secrets_path(self) -> Path:
        """PDF-password map: inside the home (v2), or at the root (legacy)."""
        if self.legacy:
            return self.root / LEGACY_SECRETS_NAME
        return self.index_dir / SECRETS_NAME

    @property
    def cache_dir(self) -> Path:
        """Per-machine external cache dir for this project's rebuildable state."""
        return cache_base() / project_cache_id(self.root)

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
        # v1 keeps state in-project (under the index dir) until migrated; v2
        # stores it in the per-machine cache, OUTSIDE the project, so two
        # machines syncing the same folder never share one database.
        if self.legacy:
            return self.index_dir / STATE_DIR
        return self.cache_dir / STATE_DIR

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
    def scaffold_fingerprint_path(self) -> Path:
        return self.state_dir / "scaffold.json"

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
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
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
        return cls(root, config, legacy=False)

    @classmethod
    def load(cls, root: Path) -> "Project":
        root = root.resolve()
        v2 = root / DEFAULT_INDEX_DIR / CONFIG_NAME
        v1 = root / LEGACY_MARKER_NAME
        if v2.is_file():
            marker, legacy = v2, False
        elif v1.is_file():
            marker, legacy = v1, True
        else:
            raise NotAProject(
                f"no docdex project at {root} (looked for "
                f"{DEFAULT_INDEX_DIR}/{CONFIG_NAME} and {LEGACY_MARKER_NAME})")
        try:
            config = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ConfigError(
                f"{marker.name} is corrupt and could not be read ({e}). "
                "Fix or recreate it with `docdex init`.")
        if not isinstance(config, dict):
            raise ConfigError(f"{marker.name} must contain a JSON object")
        return cls(root, config, legacy=legacy)

    @classmethod
    def discover(cls, start: Optional[Path] = None) -> "Project":
        """Walk upward from `start` (default: cwd) to find the project root."""
        cur = (start or Path.cwd()).resolve()
        for candidate in [cur, *cur.parents]:
            if (candidate / DEFAULT_INDEX_DIR / CONFIG_NAME).is_file() \
                    or (candidate / LEGACY_MARKER_NAME).is_file():
                return cls.load(candidate)
        raise NotAProject(
            f"no docdex project found at or above {cur} (looked for "
            f"{DEFAULT_INDEX_DIR}/{CONFIG_NAME} and {LEGACY_MARKER_NAME}). "
            "Run `docdex init` in the project root, or pass --root."
        )


def ensure_state_dirs(project: Project) -> None:
    # Boundary guards (defense in depth): even though names passed validation,
    # refuse to write if the in-project home or the external cache is a symlink
    # or resolves outside its base. Shared with `purge` so the write path and
    # the delete path can never disagree about what is in-bounds.
    err = project.index_confinement_error()
    if err:
        raise ConfigError(err)
    if not project.legacy:
        serr = project.state_confinement_error()
        if serr:
            raise ConfigError(serr)
    for d in (project.index_dir, project.update_dir, project.notes_dir):
        d.mkdir(parents=True, exist_ok=True)
    for d in (project.state_dir, project.extracted_dir, project.dumps_dir,
              project.vision_dir):
        d.mkdir(parents=True, exist_ok=True)
    if not project.legacy:
        meta = project.cache_dir / META_NAME
        if not meta.exists():
            from docdex import __version__
            meta.write_text(json.dumps({
                "root": str(project.root),
                "created_with": __version__,
                "created_at": utc_now_iso(),
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
