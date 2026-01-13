from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.text import Text
from textual.widgets import TextArea

from ..file_ops import resolve_user_path
from ..fileops_plan import DELETE_MARKER, is_delete_marker_line
from .file_browser import (
    FileEntryKind,
    file_icon_style_for_kind,
    file_text_style_for_kind,
    is_hidden,
    known_file_icons,
)

_ICON_SET = known_file_icons()


def strip_icon_prefix(text: str) -> str:
    if len(text) >= 2 and text[0] in _ICON_SET and text[1] == " ":
        return text[2:]
    return text


class FileBufferTextArea(TextArea):
    def __init__(self, *, root: Path | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.root = root
        self.cursor_mode = "normal"
        self.cursor_blink = True
        self.visual_range: tuple[int, int] | None = None
        self.visual_line_mode = False
        self.visual_anchor: tuple[int, int] | None = None
        self.visual_cursor: tuple[int, int] | None = None

    def set_root(self, root: Path) -> None:
        self.root = root

    def set_cursor_mode(self, mode: str) -> None:
        self.cursor_mode = mode

    def set_visual_range(
        self,
        start: int,
        end: int,
        line_mode: bool,
        anchor: tuple[int, int] | None = None,
        cursor: tuple[int, int] | None = None,
    ) -> None:
        self.visual_range = (min(start, end), max(start, end))
        self.visual_line_mode = line_mode
        self.visual_anchor = anchor
        self.visual_cursor = cursor
        self.refresh()

    def clear_visual_range(self) -> None:
        self.visual_range = None
        self.visual_line_mode = False
        self.visual_anchor = None
        self.visual_cursor = None
        self.refresh()

    def move_cursor(
        self,
        location: tuple[int, int],
        select: bool = False,
        center: bool = False,
        record_width: bool = True,
    ) -> None:
        row, col = location
        min_col = self._min_column_for_row(row)
        if col < min_col:
            col = min_col
        super().move_cursor(
            (row, col),
            select=select,
            center=center,
            record_width=record_width,
        )

    def action_delete_left(self) -> None:
        row, col = self.cursor_location
        if col <= self._min_column_for_row(row):
            return
        super().action_delete_left()

    def get_line(self, line_index: int) -> Text:
        line_string = self.document.get_line(line_index)
        display_line, cursor_mark, insert_index = self._cursor_display_line(
            line_index, line_string
        )
        text = Text(display_line, end="", no_wrap=True)
        path_start, path_end, icon_index = self._line_positions(line_string)
        shift_start = self._shift_start_index(insert_index)
        shift_end = self._shift_end_index(insert_index)
        if path_start is not None:
            path_text = line_string[path_start:path_end].strip()
            if path_text:
                kind, path = self._resolve_kind(path_text)
                if icon_index is not None:
                    icon_index = shift_start(icon_index)
                    icon_style = file_icon_style_for_kind(kind, path or Path(path_text))
                    text.stylize(icon_style, icon_index, icon_index + 1)
                text_style = file_text_style_for_kind(kind)
                if path_start < path_end:
                    text.stylize(text_style, shift_start(path_start), shift_end(path_end))
                if path is not None and is_hidden(path):
                    text.stylize("dim")
        self._apply_visual_selection(text, line_index, line_string, insert_index)
        if cursor_mark is not None:
            self._apply_cursor_mark_style(text, cursor_mark)
        return text

    def _apply_visual_selection(
        self,
        text: Text,
        line_index: int,
        line_string: str,
        insert_index: int | None,
    ) -> None:
        if self.visual_range is None:
            return
        start_line, end_line = self.visual_range
        if line_index < start_line or line_index > end_line:
            return
        plain = text.plain
        if not plain:
            plain = " "
            text._text = [plain]
        if self.visual_line_mode or self.visual_anchor is None or self.visual_cursor is None:
            start_col = 0
            if insert_index is not None and start_col >= insert_index:
                start_col += 1
            if start_col >= len(plain):
                return
            text.stylize("on #2b3344", start_col, len(plain))
            return

        anchor_row, anchor_col = self.visual_anchor
        cursor_row, cursor_col = self.visual_cursor
        if line_index < min(anchor_row, cursor_row) or line_index > max(anchor_row, cursor_row):
            return
        min_col = self._min_column_for_row(line_index)
        anchor_col = max(min_col, anchor_col)
        cursor_col = max(min_col, cursor_col)
        if anchor_row == cursor_row == line_index:
            start_col = min(anchor_col, cursor_col)
            end_col = max(anchor_col, cursor_col) + 1
        elif line_index == anchor_row:
            if anchor_row < cursor_row:
                start_col = anchor_col
                end_col = len(plain)
            else:
                start_col = 0
                end_col = anchor_col + 1
        elif line_index == cursor_row:
            if cursor_row < anchor_row:
                start_col = cursor_col
                end_col = len(plain)
            else:
                start_col = 0
                end_col = cursor_col + 1
        else:
            start_col = 0
            end_col = len(plain)
        if insert_index is not None:
            if start_col >= insert_index:
                start_col += 1
            if end_col >= insert_index:
                end_col += 1
        if start_col >= len(plain):
            return
        end_col = max(start_col + 1, min(len(plain), end_col))
        text.stylize("on #2b3344", start_col, end_col)

    def _cursor_display_line(
        self, line_index: int, line_string: str
    ) -> tuple[str, int | None, int | None]:
        if self.cursor_mode != "insert":
            return line_string, None, None
        if not self.has_focus:
            return line_string, None, None
        if not self._cursor_should_draw():
            return line_string, None, None
        row, col = self.cursor_location
        if row != line_index:
            return line_string, None, None
        if col < 0:
            col = 0
        padded = line_string
        if col > len(padded):
            padded = padded + (" " * (col - len(padded)))
        cursor_mark = "\u20d2"
        if col >= len(padded):
            base = padded + " "
            mark_index = len(base)
            display = base + cursor_mark
            return display, mark_index, mark_index
        mark_index = col + 1
        display = f"{padded[:mark_index]}{cursor_mark}{padded[mark_index:]}"
        return display, mark_index, mark_index

    def _apply_cursor_mark_style(self, text: Text, mark_index: int) -> None:
        if mark_index < 0 or mark_index >= len(text):
            return
        cursor_style = self.get_component_rich_style("text-area--cursor")
        if cursor_style:
            text.stylize(cursor_style, mark_index, mark_index + 1)

    def _cursor_should_draw(self) -> bool:
        if not self.has_focus:
            return False
        if not self.cursor_blink:
            return True
        return self._cursor_visible

    @property
    def _draw_cursor(self) -> bool:
        if self.cursor_mode == "insert":
            return False
        return super()._draw_cursor

    def _shift_start_index(self, insert_index: int | None) -> Callable[[int], int]:
        if insert_index is None:
            return lambda value: value
        return lambda value: value + (1 if insert_index <= value else 0)

    def _shift_end_index(self, insert_index: int | None) -> Callable[[int], int]:
        if insert_index is None:
            return lambda value: value
        return lambda value: value + (1 if insert_index < value else 0)

    def _min_column_for_row(self, row: int) -> int:
        if row < 0 or row >= self.document.line_count:
            return 0
        line = self.document.get_line(row)
        path_start, _path_end, _icon_index = self._line_positions(line)
        if path_start is None:
            return 0
        return path_start

    def _line_positions(self, line: str) -> tuple[int | None, int, int | None]:
        if not line.strip():
            return (None, 0, None)
        marker_end = 0
        if is_delete_marker_line(line):
            marker_index = line.upper().find(DELETE_MARKER)
            if marker_index >= 0:
                marker_end = marker_index + len(DELETE_MARKER)
                while marker_end < len(line) and line[marker_end].isspace():
                    marker_end += 1
        path_start = marker_end
        icon_index = None
        if marker_end + 1 < len(line):
            if line[marker_end] in _ICON_SET and line[marker_end + 1] == " ":
                icon_index = marker_end
                path_start = marker_end + 2
        path_end = len(line.rstrip())
        if path_end <= path_start:
            return (None, path_end, icon_index)
        return (path_start, path_end, icon_index)

    def _resolve_kind(self, path_text: str) -> tuple[FileEntryKind, Path | None]:
        stripped = path_text.strip()
        if stripped == "..":
            return (FileEntryKind.UP, self.root.parent if self.root else None)
        is_dir_hint = stripped.endswith(("/", "\\"))
        trimmed = stripped.rstrip("/\\")
        if not trimmed:
            return (FileEntryKind.DIR, self.root)
        path = resolve_user_path(self.root, trimmed) if self.root else Path(trimmed)
        is_dir = False
        if path.exists():
            try:
                is_dir = path.is_dir()
            except OSError:
                is_dir = False
        if not is_dir:
            is_dir = is_dir_hint
        kind = FileEntryKind.DIR if is_dir else FileEntryKind.FILE
        return (kind, path)
