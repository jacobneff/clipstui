from __future__ import annotations

import stat
from enum import Enum
from pathlib import Path

from rich.text import Text
from textual.widgets import Label, ListItem

_DIR_TEXT_STYLE = "#7dcfff"
_DIR_ICON_STYLE = "#7dcfff"
_FILE_TEXT_STYLE = "#c0caf5"
_FILE_ICON_STYLE = "#a9b1d6"
_HIDDEN_STYLE = "dim"

_FOLDER_ICON = ""
_FILE_ICON = ""
_TEXT_ICON = ""
_VIDEO_ICON = ""
_AUDIO_ICON = ""
_IMAGE_ICON = ""
_ARCHIVE_ICON = ""
_PDF_ICON = ""
_WINDOWS_ICON = ""

_VIDEO_STYLE = "#bb9af7"
_AUDIO_STYLE = "#9ece6a"
_IMAGE_STYLE = "#7aa2f7"
_ARCHIVE_STYLE = "#e0af68"
_DOC_STYLE = "#7dcfff"
_CODE_STYLE = "#7aa2f7"
_DATA_STYLE = "#2ac3de"
_EXEC_STYLE = "#f7768e"

_ICON_BY_EXT = {
    ".clip": _TEXT_ICON,
    ".txt": _TEXT_ICON,
    ".md": "",
    ".rst": _TEXT_ICON,
    ".json": "",
    ".toml": "",
    ".yaml": "",
    ".yml": "",
    ".py": "",
    ".ps1": "",
    ".bat": "",
    ".cmd": "",
    ".sh": "",
    ".js": "",
    ".jsx": "",
    ".ts": "",
    ".tsx": "",
    ".html": "",
    ".css": "",
    ".scss": "",
    ".csv": "",
    ".tsv": "",
    ".mp4": _VIDEO_ICON,
    ".mkv": _VIDEO_ICON,
    ".webm": _VIDEO_ICON,
    ".mov": _VIDEO_ICON,
    ".mp3": _AUDIO_ICON,
    ".wav": _AUDIO_ICON,
    ".flac": _AUDIO_ICON,
    ".jpg": _IMAGE_ICON,
    ".jpeg": _IMAGE_ICON,
    ".png": _IMAGE_ICON,
    ".gif": _IMAGE_ICON,
    ".webp": _IMAGE_ICON,
    ".zip": _ARCHIVE_ICON,
    ".7z": _ARCHIVE_ICON,
    ".rar": _ARCHIVE_ICON,
    ".exe": _WINDOWS_ICON,
    ".dll": _WINDOWS_ICON,
    ".pdf": _PDF_ICON,
}

_STYLE_BY_EXT = {
    ".clip": _DOC_STYLE,
    ".txt": _DOC_STYLE,
    ".md": _DOC_STYLE,
    ".rst": _DOC_STYLE,
    ".pdf": _DOC_STYLE,
    ".json": _DATA_STYLE,
    ".toml": _DATA_STYLE,
    ".yaml": _DATA_STYLE,
    ".yml": _DATA_STYLE,
    ".csv": _DATA_STYLE,
    ".tsv": _DATA_STYLE,
    ".py": _CODE_STYLE,
    ".ps1": _CODE_STYLE,
    ".bat": _CODE_STYLE,
    ".cmd": _CODE_STYLE,
    ".sh": _CODE_STYLE,
    ".js": _CODE_STYLE,
    ".jsx": _CODE_STYLE,
    ".ts": _CODE_STYLE,
    ".tsx": _CODE_STYLE,
    ".html": _CODE_STYLE,
    ".css": _CODE_STYLE,
    ".scss": _CODE_STYLE,
    ".mp4": _VIDEO_STYLE,
    ".mkv": _VIDEO_STYLE,
    ".webm": _VIDEO_STYLE,
    ".mov": _VIDEO_STYLE,
    ".mp3": _AUDIO_STYLE,
    ".wav": _AUDIO_STYLE,
    ".flac": _AUDIO_STYLE,
    ".jpg": _IMAGE_STYLE,
    ".jpeg": _IMAGE_STYLE,
    ".png": _IMAGE_STYLE,
    ".gif": _IMAGE_STYLE,
    ".webp": _IMAGE_STYLE,
    ".zip": _ARCHIVE_STYLE,
    ".7z": _ARCHIVE_STYLE,
    ".rar": _ARCHIVE_STYLE,
}

_EXECUTABLE_EXTS = {
    ".exe",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
}


class FileEntryKind(Enum):
    UP = "up"
    DIR = "dir"
    FILE = "file"


class FileListItem(ListItem):
    def __init__(self, path: Path, kind: FileEntryKind) -> None:
        self.path = path
        self.kind = kind
        super().__init__(Label(format_file_label(path, kind)))


def format_file_label(path: Path, kind: FileEntryKind) -> Text:
    icon = file_icon_for_kind(kind, path)
    name = ".." if kind == FileEntryKind.UP else path.name
    icon_style = file_icon_style_for_kind(kind, path)
    text_style = file_text_style_for_kind(kind)
    label = Text()
    label.append(icon, style=icon_style)
    label.append(" ")
    label.append(name, style=text_style)
    if kind != FileEntryKind.UP and is_hidden(path):
        label.stylize(_HIDDEN_STYLE)
    return label


def path_sort_key(path: Path) -> str:
    return path.name.casefold()


def file_icon_for_kind(kind: FileEntryKind, path: Path) -> str:
    if kind in {FileEntryKind.UP, FileEntryKind.DIR}:
        return _FOLDER_ICON
    return _ICON_BY_EXT.get(path.suffix.lower(), _FILE_ICON)


def file_icon_for_path(path: Path) -> str:
    try:
        if path.is_dir():
            return _FOLDER_ICON
    except OSError:
        return _FILE_ICON
    return _ICON_BY_EXT.get(path.suffix.lower(), _FILE_ICON)


def file_icon_style_for_kind(kind: FileEntryKind, path: Path) -> str:
    if kind in {FileEntryKind.UP, FileEntryKind.DIR}:
        return _DIR_ICON_STYLE
    ext = path.suffix.lower()
    if ext in _EXECUTABLE_EXTS:
        return _EXEC_STYLE
    return _STYLE_BY_EXT.get(ext, _FILE_ICON_STYLE)


def file_text_style_for_kind(kind: FileEntryKind) -> str:
    if kind in {FileEntryKind.UP, FileEntryKind.DIR}:
        return _DIR_TEXT_STYLE
    return _FILE_TEXT_STYLE


def is_hidden(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    try:
        attrs = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0))


def known_file_icons() -> set[str]:
    icons = set(_ICON_BY_EXT.values())
    icons.update(
        {
            _FOLDER_ICON,
            _FILE_ICON,
            _TEXT_ICON,
            _VIDEO_ICON,
            _AUDIO_ICON,
            _IMAGE_ICON,
            _ARCHIVE_ICON,
            _PDF_ICON,
            _WINDOWS_ICON,
        }
    )
    return icons
