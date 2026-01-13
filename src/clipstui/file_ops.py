from __future__ import annotations

from pathlib import Path


def resolve_user_path(root: Path, value: str) -> Path | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
    except OSError:
        return None
    if not path.is_absolute():
        path = root / path
    return path


def resolve_new_entry(root: Path, value: str) -> tuple[Path | None, bool]:
    raw = value.strip()
    if not raw:
        return (None, False)
    is_dir = raw.endswith(("/", "\\"))
    raw = raw.rstrip("/\\")
    if not raw:
        return (None, is_dir)
    path = resolve_user_path(root, raw)
    return (path, is_dir)


def is_valid_name(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return "/" not in value and "\\" not in value


def normalize_drive_path(path: Path) -> Path:
    value = str(path)
    if len(value) == 2 and value[1] == ":":
        return Path(f"{value}\\")
    return path


def is_clip_file(path: Path) -> bool:
    return path.suffix.lower() in {".clip", ".txt"}
