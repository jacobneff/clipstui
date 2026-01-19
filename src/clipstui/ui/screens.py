from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static
from textual_image.widget import Image as PreviewImage

from .file_browser import (
    FileEntryKind,
    file_icon_for_path,
    file_icon_style_for_kind,
    file_text_style_for_kind,
    is_hidden,
)
from ..clip_time import coerce_time_input, looks_like_url, replace_url_time
from ..file_ops import normalize_drive_path
from ..parser import ClipSpec
from ..resolve import (
    DEFAULT_OUTPUT_TEMPLATE,
    ResolvedClip,
    format_output_basename,
    resolve_clip,
    validate_output_template,
)
from ..timeparse import format_seconds, get_seconds_from_url
from ..clip_utils import MergeSuggestion
from ..presets import PresetProfile


class HelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close"), ("?", "close", "Close")]

    CSS = """
    HelpScreen {
        align: center middle;
        background: $surface 80%;
    }

    #help_dialog {
        width: 70%;
        max-width: 80;
        height: 80%;
        max-height: 90%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #help_scroll {
        height: 1fr;
    }

    #help_text {
        width: 100%;
    }

    #help_close {
        color: $text;
        background: $panel;
        border: round $accent;
    }

    #help_close:hover {
        background: $boost;
        color: $text;
    }
    """

    def __init__(self, help_text: str) -> None:
        super().__init__()
        self._help_text = help_text

    def compose(self) -> ComposeResult:
        with Vertical(id="help_dialog"):
            with VerticalScroll(id="help_scroll"):
                yield Static(self._help_text, id="help_text", markup=False)
            yield Button("Close", id="help_close")

    def on_mount(self) -> None:
        self.query_one("#help_scroll", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        key = event.key
        character = event.character or ""
        if key == "escape" or key == "?" or character == "?":
            self.action_close()
            event.stop()
            return
        if key in {
            "up",
            "down",
            "left",
            "right",
            "tab",
            "shift+tab",
        }:
            return
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help_close":
            self.dismiss(None)


class ThumbnailScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close"), ("enter", "close", "Close")]

    CSS = """
    ThumbnailScreen {
        align: center middle;
        background: $surface 80%;
    }

    #thumb_full_dialog {
        width: 95%;
        height: 95%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #thumb_full_image {
        width: 100%;
        height: 1fr;
    }

    #thumb_full_message {
        width: 100%;
        height: 1fr;
        content-align: center middle;
        color: $text;
    }
    """

    def __init__(self, path: Path | None, message: str | None = None) -> None:
        super().__init__()
        self._path = path
        self._message = message or "Thumbnail unavailable."

    def compose(self) -> ComposeResult:
        with Vertical(id="thumb_full_dialog"):
            if self._path is not None:
                yield PreviewImage(self._path, id="thumb_full_image")
            else:
                yield Static(self._message, id="thumb_full_message")

    def action_close(self) -> None:
        self.dismiss(None)


class OutputDirScreen(ModalScreen[Path | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    OutputDirScreen {
        align: center middle;
        background: $surface 80%;
    }

    #output_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #output_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, current: Path | None) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="output_dialog"):
            yield Label("Output directory")
            yield Input(
                value=str(self._current) if self._current else "",
                placeholder="Path to output directory",
                id="output_input",
            )
            yield Label("", id="output_error")
            with Horizontal():
                yield Button("Set", id="output_set")
                yield Button("Cancel", id="output_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "output_cancel":
            self.dismiss(None)
        elif event.button.id == "output_set":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "output_input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#output_input", Input)
        error_label = self.query_one("#output_error", Label)
        value = input_widget.value.strip()
        if not value:
            error_label.update("Please enter a directory path.")
            return
        path = Path(value).expanduser()
        self.dismiss(path)


class TreeRootScreen(ModalScreen[Path | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    TreeRootScreen {
        align: center middle;
        background: $surface 80%;
    }

    #root_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #root_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, current: Path) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="root_dialog"):
            yield Label("File browser root")
            yield Input(
                value=str(self._current),
                placeholder="e.g. D:\\ or D:\\OneDrive",
                id="root_input",
            )
            yield Label("", id="root_error")
            with Horizontal():
                yield Button("Set", id="root_set")
                yield Button("Cancel", id="root_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "root_cancel":
            self.dismiss(None)
        elif event.button.id == "root_set":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "root_input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#root_input", Input)
        error_label = self.query_one("#root_error", Label)
        value = input_widget.value.strip()
        if not value:
            error_label.update("Please enter a directory path.")
            return
        path = normalize_drive_path(Path(value).expanduser())
        self.dismiss(path)


class ClipEditorScreen(ModalScreen[ClipSpec | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    ClipEditorScreen {
        align: center middle;
        background: $surface 80%;
    }

    #clip_dialog {
        width: 80%;
        max-width: 100;
        height: 80%;
        max-height: 90%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #clip_scroll {
        height: 1fr;
    }

    #clip_error {
        color: $error;
        height: 1;
    }

    #clip_hint {
        color: $text-muted;
        height: 8;
    }
    """

    def __init__(
        self,
        *,
        clip: ClipSpec | None,
        pad_before_default: int,
        pad_after_default: int,
        title: str,
    ) -> None:
        super().__init__()
        self._clip = clip
        self._pad_before_default = pad_before_default
        self._pad_after_default = pad_after_default
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="clip_dialog"):
            with VerticalScroll(id="clip_scroll"):
                yield Label(self._title)
                yield Label("Tag (optional)")
                yield Input(
                    value=self._clip.tag if self._clip and self._clip.tag else "",
                    placeholder="Optional tag",
                    id="clip_tag",
                )
                yield Label("Label (optional)")
                yield Input(
                    value=self._clip.label if self._clip and self._clip.label else "",
                    placeholder="K/B/A/D/S/E or custom",
                    id="clip_label",
                )
                yield Label("Score (optional)")
                yield Input(
                    value=self._clip.score if self._clip and self._clip.score else "",
                    placeholder="e.g. 22-20",
                    id="clip_score",
                )
                yield Label("Opponent (optional)")
                yield Input(
                    value=self._clip.opponent if self._clip and self._clip.opponent else "",
                    placeholder="Opponent team",
                    id="clip_opponent",
                )
                yield Label("Start URL")
                yield Input(
                    value=self._clip.start_url if self._clip else "",
                    placeholder="https://www.youtube.com/watch?v=...&t=10",
                    id="clip_start",
                )
                yield Label("End URL")
                yield Input(
                    value=self._clip.end_url if self._clip else "",
                    placeholder="https://www.youtube.com/watch?v=...&t=20",
                    id="clip_end",
                )
                yield Label("Pad before (seconds, optional)")
                yield Input(
                    value=_pad_value(self._clip.pad_before if self._clip else None),
                    placeholder=f"default {self._pad_before_default}",
                    id="clip_pad_before",
                )
                yield Label("Pad after (seconds, optional)")
                yield Input(
                    value=_pad_value(self._clip.pad_after if self._clip else None),
                    placeholder=f"default {self._pad_after_default}",
                    id="clip_pad_after",
                )
                yield Label("", id="clip_error")
                yield Static("", id="clip_hint")
            with Horizontal():
                yield Button("Save", id="clip_save")
                yield Button("Cancel", id="clip_cancel")

    def on_mount(self) -> None:
        start_input = self.query_one("#clip_start", Input)
        start_input.focus()
        self._refresh_hint()

    def on_key(self, event: events.Key) -> None:
        key = event.character or event.key
        if key in {"[", "]", "{", "}"}:
            delta = _nudge_delta(key)
            if delta is not None and self._nudge_active_input(delta):
                event.stop()
                return

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "clip_cancel":
            self.dismiss(None)
        elif event.button.id == "clip_save":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id and event.input.id.startswith("clip_"):
            self._submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("clip_"):
            self._refresh_hint()

    def _submit(self) -> None:
        error_label = self.query_one("#clip_error", Label)
        hint_label = self.query_one("#clip_hint", Static)
        clip, error, resolved = self._build_clip()
        if error:
            error_label.update(error)
            hint_label.update("")
            return
        error_label.update("")
        if resolved is not None:
            hint_label.update(_format_clip_hint(resolved))
        self.dismiss(clip)

    def _refresh_hint(self) -> None:
        error_label = self.query_one("#clip_error", Label)
        hint_label = self.query_one("#clip_hint", Static)
        clip, error, resolved = self._build_clip()
        if error:
            error_label.update(error)
            hint_label.update("")
            return
        error_label.update("")
        if resolved is not None:
            hint_label.update(_format_clip_hint(resolved))

    def _build_clip(self) -> tuple[ClipSpec | None, str | None, ResolvedClip | None]:
        tag_input = self.query_one("#clip_tag", Input)
        label_input = self.query_one("#clip_label", Input)
        score_input = self.query_one("#clip_score", Input)
        opponent_input = self.query_one("#clip_opponent", Input)
        start_input = self.query_one("#clip_start", Input)
        end_input = self.query_one("#clip_end", Input)
        pad_before_input = self.query_one("#clip_pad_before", Input)
        pad_after_input = self.query_one("#clip_pad_after", Input)

        tag = tag_input.value.strip() or None
        label = label_input.value.strip() or None
        score = score_input.value.strip() or None
        opponent = opponent_input.value.strip() or None
        start_text = start_input.value.strip()
        end_text = end_input.value.strip()
        if not start_text:
            return (None, "Start URL is required.", None)
        if not end_text:
            return (None, "End URL is required.", None)

        pad_before_text = pad_before_input.value.strip()
        pad_after_text = pad_after_input.value.strip()
        if not pad_before_text and not pad_after_text:
            pad_before = None
            pad_after = None
        else:
            try:
                pad_before = _parse_pad_seconds(pad_before_text or "0")
                pad_after = _parse_pad_seconds(pad_after_text or "0")
            except ValueError as exc:
                return (None, str(exc), None)

        end_is_url = looks_like_url(end_text)
        base_start_url = self._clip.start_url if self._clip else (end_text if end_is_url else None)
        try:
            start_url, _start_sec = coerce_time_input(start_text, base_url=base_start_url)
        except ValueError as exc:
            return (None, f"Start: {exc}", None)

        if self._clip and end_text.startswith(("+", "-")):
            base_end_url = self._clip.end_url
        else:
            base_end_url = None if end_is_url else start_url
            if base_end_url is None and self._clip:
                base_end_url = self._clip.end_url
        try:
            end_url, _end_sec = coerce_time_input(end_text, base_url=base_end_url)
        except ValueError as exc:
            return (None, f"End: {exc}", None)

        clip = ClipSpec(
            start_url=start_url,
            end_url=end_url,
            tag=tag,
            label=label,
            score=score,
            opponent=opponent,
            pad_before=pad_before,
            pad_after=pad_after,
        )
        try:
            resolved = resolve_clip(
                clip,
                pad_before=self._pad_before_default,
                pad_after=self._pad_after_default,
            )
        except ValueError as exc:
            return (None, str(exc), None)
        return (clip, None, resolved)

    def _nudge_active_input(self, delta: float) -> bool:
        start_input = self.query_one("#clip_start", Input)
        end_input = self.query_one("#clip_end", Input)
        target = None
        if start_input.has_focus:
            target = start_input
            fallback = self._clip.start_url if self._clip else end_input.value.strip()
        elif end_input.has_focus:
            target = end_input
            fallback = self._clip.end_url if self._clip else start_input.value.strip()
        if target is None:
            return False
        base_url = fallback if looks_like_url(fallback) else None
        try:
            current_url, current_sec = coerce_time_input(target.value, base_url=base_url)
        except ValueError:
            return False
        nudged = max(0.0, current_sec + delta)
        target.value = replace_url_time(current_url, nudged)
        return True


def _parse_pad_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise ValueError("Pad values must be whole seconds.") from exc
    if seconds < 0:
        raise ValueError("Pad values must be non-negative.")
    return seconds


def _pad_value(value: int | None) -> str:
    return "" if value is None else str(value)


def _format_clip_hint(resolved: ResolvedClip) -> str:
    parts = []
    if resolved.clip.label:
        parts.append(f"Label: {resolved.clip.label}")
    if resolved.clip.score:
        parts.append(f"Score: {resolved.clip.score}")
    if resolved.clip.opponent:
        parts.append(f"Opponent: {resolved.clip.opponent}")
    context = "\n".join(parts)

    def _fmt(val: float) -> str:
        return f"{int(val // 60)}:{int(val % 60):02}"

    return (
        f"Video: {resolved.video_id}\n"
        f"Start: {_fmt(resolved.start_sec)}  "
        f"End: {_fmt(resolved.end_sec)}  "
        f"Cut: {format_seconds(resolved.cut_start)}-{format_seconds(resolved.cut_end)}\n"
        f"Output: {resolved.output_name}"
        + (f"\n{context}" if context else "")
    )


class PadInputScreen(ModalScreen[tuple[int | None, int | None] | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    PadInputScreen {
        align: center middle;
        background: $surface 80%;
    }

    #pad_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #pad_error {
        color: $error;
        height: 1;
    }

    #pad_hint {
        color: $text-muted;
        height: 2;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        pad_before: int | None,
        pad_after: int | None,
        hint: str | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._pad_before = pad_before
        self._pad_after = pad_after
        self._hint = hint or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="pad_dialog"):
            yield Label(self._title)
            yield Label("Pad before (seconds)")
            yield Input(
                value=_pad_value(self._pad_before),
                placeholder="Leave blank to keep current",
                id="pad_before",
            )
            yield Label("Pad after (seconds)")
            yield Input(
                value=_pad_value(self._pad_after),
                placeholder="Leave blank to keep current",
                id="pad_after",
            )
            yield Label("", id="pad_error")
            yield Static(self._hint, id="pad_hint")
            with Horizontal():
                yield Button("Apply", id="pad_apply")
                yield Button("Cancel", id="pad_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#pad_before", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pad_cancel":
            self.dismiss(None)
        elif event.button.id == "pad_apply":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"pad_before", "pad_after"}:
            self._submit()

    def _submit(self) -> None:
        before_input = self.query_one("#pad_before", Input).value.strip()
        after_input = self.query_one("#pad_after", Input).value.strip()
        error_label = self.query_one("#pad_error", Label)
        try:
            pad_before = _parse_optional_pad(before_input)
            pad_after = _parse_optional_pad(after_input)
        except ValueError as exc:
            error_label.update(str(exc))
            return
        error_label.update("")
        self.dismiss((pad_before, pad_after))


class EndTimeScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    EndTimeScreen {
        align: center middle;
        background: $surface 80%;
    }

    #end_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #end_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, start_url: str) -> None:
        super().__init__()
        self._start_url = start_url
        try:
            start_sec = get_seconds_from_url(start_url)
            self._start_time = format_seconds(start_sec)
        except ValueError:
            self._start_time = "--"

    def compose(self) -> ComposeResult:
        with Vertical(id="end_dialog"):
            yield Label(f"End time (start {self._start_time}s)")
            yield Input(
                placeholder="mm:ss, hh:mm:ss, +2.5, or full URL",
                id="end_input",
            )
            yield Label("", id="end_error")
            with Horizontal():
                yield Button("Set", id="end_set")
                yield Button("Cancel", id="end_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#end_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "end_cancel":
            self.dismiss(None)
        elif event.button.id == "end_set":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "end_input":
            self._submit()

    def _submit(self) -> None:
        value = self.query_one("#end_input", Input).value.strip()
        if not value:
            self.query_one("#end_error", Label).update("Please enter an end time.")
            return
        self.dismiss(value)


class MergeAdjacentScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    MergeAdjacentScreen {
        align: center middle;
        background: $surface 80%;
    }

    #merge_dialog {
        width: 80%;
        max-width: 110;
        height: 70%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #merge_text {
        height: 1fr;
        overflow-y: auto;
    }
    """

    def __init__(self, suggestions: list[MergeSuggestion]) -> None:
        super().__init__()
        self._suggestions = suggestions

    def compose(self) -> ComposeResult:
        with Vertical(id="merge_dialog"):
            yield Label("Merge adjacent clips?")
            yield Static(_format_merge_suggestions(self._suggestions), id="merge_text")
            with Horizontal():
                yield Button("Merge", id="merge_apply")
                yield Button("Cancel", id="merge_cancel")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "merge_cancel":
            self.dismiss(False)
        elif event.button.id == "merge_apply":
            self.dismiss(True)


def _nudge_delta(key: str) -> float | None:
    if key == "[":
        return -0.1
    if key == "]":
        return 0.1
    if key == "{":
        return -0.5
    if key == "}":
        return 0.5
    return None


def _parse_optional_pad(value: str) -> int | None:
    if not value:
        return None
    return _parse_pad_seconds(value)


def _format_merge_suggestions(suggestions: list[MergeSuggestion]) -> str:
    lines: list[str] = []
    for suggestion in suggestions:
        first = suggestion.first
        second = suggestion.second
        first_tag = first.display_tag or "-"
        second_tag = second.display_tag or "-"
        gap = format_seconds(suggestion.gap_seconds)
        lines.append(
            f"{first_tag} {format_seconds(first.start_sec)}-{format_seconds(first.end_sec)} "
            f"-> {second_tag} {format_seconds(second.start_sec)}-{format_seconds(second.end_sec)} "
            f"(gap {gap}s)"
        )
    if not lines:
        return "No adjacent clips found."
    return "\n".join(lines)


class OutputFormatScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    OutputFormatScreen {
        align: center middle;
        background: $surface 80%;
    }

    #format_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #format_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="format_dialog"):
            yield Label("Output format (e.g. mp4)")
            yield Input(
                value=self._current,
                placeholder="mp4",
                id="format_input",
            )
            yield Label("", id="format_error")
            with Horizontal():
                yield Button("Set", id="format_set")
                yield Button("Cancel", id="format_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "format_cancel":
            self.dismiss(None)
        elif event.button.id == "format_set":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "format_input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#format_input", Input)
        error_label = self.query_one("#format_error", Label)
        value = input_widget.value.strip().lower().lstrip(".")
        if not value:
            error_label.update("Please enter an output format.")
            return
        if not _is_valid_output_format(value):
            error_label.update("Use only letters/numbers, e.g. mp4 or mkv.")
            return
        self.dismiss(value)


class OutputTemplateScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    OutputTemplateScreen {
        align: center middle;
        background: $surface 80%;
    }

    #template_dialog {
        width: 80%;
        max-width: 120;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #template_preview {
        height: 2;
        color: $text;
    }

    #template_hint {
        color: $text-muted;
    }

    #template_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, current: str, clip: ResolvedClip | None, title: str | None) -> None:
        super().__init__()
        self._current = current
        self._clip = clip
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="template_dialog"):
            yield Label("Output template")
            yield Input(
                value=self._current,
                placeholder=DEFAULT_OUTPUT_TEMPLATE,
                id="template_input",
            )
            yield Static("", id="template_preview")
            yield Static(
                "Tokens: {tag} {label} {score} {opponent} "
                "{videoid} {start} {end} {title}",
                id="template_hint",
            )
            yield Label("", id="template_error")
            with Horizontal():
                yield Button("Set", id="template_set")
                yield Button("Reset", id="template_reset")
                yield Button("Cancel", id="template_cancel")

    def on_mount(self) -> None:
        self._update_preview()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "template_cancel":
            self.dismiss(None)
        elif event.button.id == "template_reset":
            input_widget = self.query_one("#template_input", Input)
            input_widget.value = DEFAULT_OUTPUT_TEMPLATE
            self._update_preview()
        elif event.button.id == "template_set":
            self._submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "template_input":
            self._update_preview()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "template_input":
            self._submit()

    def _sample_clip(self) -> ResolvedClip:
        if self._clip is not None:
            return self._clip
        clip = ClipSpec(
            start_url="https://www.youtube.com/watch?v=abc123&t=10",
            end_url="https://www.youtube.com/watch?v=abc123&t=20",
            tag="C001",
            label="K",
            score="22-20",
            opponent="Sample Opponent",
        )
        return resolve_clip(clip, pad_before=0, pad_after=0)

    def _update_preview(self) -> None:
        input_widget = self.query_one("#template_input", Input)
        preview = self.query_one("#template_preview", Static)
        error_label = self.query_one("#template_error", Label)
        value = input_widget.value.strip()
        if not value:
            preview.update("Preview: --")
            error_label.update("Template cannot be empty.")
            return
        try:
            validate_output_template(value)
            sample = self._sample_clip()
            title = self._title or "Sample Title"
            output_name = format_output_basename(value, sample, title=title)
        except ValueError as exc:
            preview.update("Preview: --")
            error_label.update(str(exc))
            return
        preview.update(f"Preview: {output_name}")
        error_label.update("")

    def _submit(self) -> None:
        input_widget = self.query_one("#template_input", Input)
        error_label = self.query_one("#template_error", Label)
        value = input_widget.value.strip()
        try:
            validate_output_template(value)
        except ValueError as exc:
            error_label.update(str(exc))
            return
        self.dismiss(value)


class PresetListItem(ListItem):
    def __init__(self, preset: PresetProfile) -> None:
        self.preset = preset
        label = Label(f"{preset.name} - {preset.description}")
        super().__init__(label)


class PresetScreen(ModalScreen[PresetProfile | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    PresetScreen {
        align: center middle;
        background: $surface 80%;
    }

    #preset_dialog {
        width: 80%;
        max-width: 120;
        height: 60%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #preset_list {
        height: 1fr;
    }

    #preset_details {
        height: 4;
        color: $text-muted;
    }
    """

    def __init__(self, presets: list[PresetProfile]) -> None:
        super().__init__()
        self._presets = presets

    def compose(self) -> ComposeResult:
        with Vertical(id="preset_dialog"):
            yield Label("Preset profiles")
            yield ListView(id="preset_list")
            yield Static("", id="preset_details")
            with Horizontal():
                yield Button("Apply", id="preset_apply")
                yield Button("Cancel", id="preset_cancel")

    def on_mount(self) -> None:
        list_view = self.query_one("#preset_list", ListView)
        list_view.clear()
        for preset in self._presets:
            list_view.append(PresetListItem(preset))
        if self._presets:
            list_view.index = 0
            self._update_details(self._presets[0])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preset_cancel":
            self.dismiss(None)
        elif event.button.id == "preset_apply":
            preset = self._selected_preset()
            self.dismiss(preset)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, PresetListItem):
            self._update_details(event.item.preset)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, PresetListItem):
            self.dismiss(event.item.preset)

    def _selected_preset(self) -> PresetProfile | None:
        list_view = self.query_one("#preset_list", ListView)
        if list_view.index is None:
            return None
        items = [child for child in list_view.children if isinstance(child, PresetListItem)]
        if not items:
            return None
        index = max(0, min(list_view.index, len(items) - 1))
        return items[index].preset

    def _update_details(self, preset: PresetProfile) -> None:
        details = self.query_one("#preset_details", Static)
        parts = []
        if preset.pad_before is not None or preset.pad_after is not None:
            before = preset.pad_before if preset.pad_before is not None else "-"
            after = preset.pad_after if preset.pad_after is not None else "-"
            parts.append(f"Pad: {before}/{after}s")
        if preset.output_format:
            parts.append(f"Format: .{preset.output_format}")
        if preset.output_dir:
            parts.append(f"Output: {preset.output_dir}")
        if preset.output_template:
            parts.append(f"Template: {preset.output_template}")
        details.update(" | ".join(parts) if parts else "--")


@dataclass(frozen=True)
class CommandAction:
    action_id: str
    title: str
    description: str = ""
    keywords: str = ""
    shortcut: str = ""


class CommandPaletteItem(ListItem):
    def __init__(self, action: CommandAction) -> None:
        self.action = action
        label = Label(_format_command_label(action), markup=False)
        super().__init__(label)


class CommandPaletteScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    CommandPaletteScreen {
        align: center middle;
        background: $surface 80%;
    }

    #command_dialog {
        width: 80%;
        max-width: 120;
        height: 60%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #command_list {
        height: 1fr;
    }

    #command_status {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self, actions: list[CommandAction]) -> None:
        super().__init__()
        self._actions = actions

    def compose(self) -> ComposeResult:
        with Vertical(id="command_dialog"):
            yield Label("Command palette")
            yield Input(
                placeholder="Type to filter commands",
                id="command_input",
            )
            yield ListView(id="command_list")
            yield Label("", id="command_status")

    def on_mount(self) -> None:
        self._apply_filter()
        self.query_one("#command_input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "command_input":
            self._apply_filter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command_input":
            action = self._selected_action()
            if action is None:
                return
            self.dismiss(action.action_id)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, CommandPaletteItem):
            self.dismiss(event.item.action.action_id)

    def _selected_action(self) -> CommandAction | None:
        list_view = self.query_one("#command_list", ListView)
        if list_view.index is None:
            return None
        items = [child for child in list_view.children if isinstance(child, CommandPaletteItem)]
        if not items:
            return None
        index = max(0, min(list_view.index, len(items) - 1))
        return items[index].action

    def _apply_filter(self) -> None:
        input_widget = self.query_one("#command_input", Input)
        list_view = self.query_one("#command_list", ListView)
        status = self.query_one("#command_status", Label)
        query = input_widget.value.strip().lower()
        scored: list[tuple[int, CommandAction]] = []
        if query:
            for action in self._actions:
                haystack = _command_search_text(action)
                score = _fuzzy_score(haystack, query)
                if score is None:
                    continue
                scored.append((score, action))
            scored.sort(key=lambda item: (item[0], item[1].title.lower()))
            filtered = [action for _, action in scored]
        else:
            filtered = list(self._actions)
        list_view.clear()
        for action in filtered:
            list_view.append(CommandPaletteItem(action))
        if filtered:
            list_view.index = 0
            status.update(f"{len(filtered)} actions")
        else:
            status.update("No matches")


def _format_command_label(action: CommandAction) -> str:
    description = action.description
    if action.shortcut:
        suffix = f"[{action.shortcut}]"
        description = f"{description} {suffix}" if description else suffix
    if description:
        return f"{action.title} - {description}"
    return action.title


def _command_search_text(action: CommandAction) -> str:
    return (
        f"{action.title} {action.description} {action.keywords} {action.shortcut}".lower()
    )


@dataclass(frozen=True)
class SearchResult:
    path: Path
    query: str


class SearchScreen(ModalScreen[SearchResult | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    SearchScreen {
        align: center middle;
        background: $surface 80%;
    }

    #search_dialog {
        width: 80%;
        max-width: 120;
        height: 70%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #search_error {
        color: $error;
        height: 1;
    }

    #search_status {
        height: 1;
        color: $text-muted;
    }

    #search_results {
        height: 1fr;
    }
    """

    def __init__(self, root: Path, show_hidden: bool) -> None:
        super().__init__()
        self._root = root
        self._show_hidden = show_hidden
        self._candidates: list[Path] = []
        self._indexed = False
        self._index_error: str | None = None
        self._truncated = False
        self._max_entries = 50_000
        self._index_started = False

    def compose(self) -> ComposeResult:
        with Vertical(id="search_dialog"):
            yield Label(f"Search in {self._root}")
            yield Input(
                placeholder="Type to filter (fuzzy match)",
                id="search_input",
            )
            yield Label("Indexing...", id="search_status")
            yield ListView(id="search_results")
            yield Label("", id="search_error")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#search_input", Input).focus()
        self.call_later(self._start_indexing)

    def _start_indexing(self) -> None:
        if self._index_started:
            return
        self._index_started = True
        try:
            threading.Thread(target=self._build_index, daemon=True).start()
        except RuntimeError as exc:
            self._finish_index([], False, str(exc))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search_input":
            self._select_first_result()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self._update_results(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SearchResultItem):
            query = self.query_one("#search_input", Input).value
            self.dismiss(SearchResult(event.item.path, query))

    def _build_index(self) -> None:
        try:
            candidates, truncated = _build_search_index(
                self._root,
                self._max_entries,
                self._show_hidden,
            )
        except Exception as exc:
            self._call_from_thread(self._finish_index, [], False, str(exc))
            return
        self._call_from_thread(self._finish_index, candidates, truncated, None)

    def _call_from_thread(self, func, *args: object) -> None:
        app = self.app
        if app is None:
            func(*args)
        else:
            app.call_from_thread(func, *args)

    def _finish_index(
        self, candidates: list[Path], truncated: bool, error: str | None
    ) -> None:
        self._candidates = candidates
        self._indexed = True
        self._index_error = error
        self._truncated = truncated
        query = self.query_one("#search_input", Input).value
        self._update_results(query)

    def _update_results(self, query: str) -> None:
        status = self.query_one("#search_status", Label)
        results = self.query_one("#search_results", ListView)
        self.query_one("#search_error", Label).update("")
        results.clear()
        if not self._indexed:
            status.update("Indexing...")
            return
        if self._index_error:
            status.update(f"Index failed: {self._index_error}")
            return
        query = query.strip()
        if not query:
            message = f"Indexed {len(self._candidates)} items."
            if self._truncated:
                message += " [index truncated]"
            status.update(message)
            return

        scored: list[tuple[int, Path]] = []
        query_lower = query.lower()
        for path in self._candidates:
            score = _score_search_path(path, self._root, query_lower)
            if score is not None:
                scored.append((score, path))
        scored.sort(key=lambda item: item[0])

        max_results = 50
        for _, path in scored[:max_results]:
            label = _format_search_label(self._root, path)
            results.append(SearchResultItem(path, label))

        message = f"{len(scored)} matches"
        if len(scored) > max_results:
            message += f" (showing {max_results})"
        if self._truncated:
            message += " [index truncated]"
        status.update(message)

    def _select_first_result(self) -> None:
        results = self.query_one("#search_results", ListView)
        first_item = None
        for child in results.children:
            if isinstance(child, SearchResultItem):
                first_item = child
                break
        if first_item is None:
            self.query_one("#search_error", Label).update("No results to select.")
            return
        query = self.query_one("#search_input", Input).value
        self.dismiss(SearchResult(first_item.path, query))


class SearchResultItem(ListItem):
    def __init__(self, path: Path, label: Text) -> None:
        super().__init__(Label(label))
        self.path = path


class ClipFilterScreen(ModalScreen[tuple[str, str, bool] | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    ClipFilterScreen {
        align: center middle;
        background: $surface 80%;
    }

    #filter_dialog {
        width: 80%;
        max-width: 100;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #filter_sort {
        height: 1;
        color: $text;
    }

    #filter_hint {
        color: $text-muted;
        height: 2;
    }

    #filter_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(
        self,
        *,
        filter_text: str,
        sort_modes: list[tuple[str, str]],
        sort_mode: str,
        sort_reverse: bool,
    ) -> None:
        super().__init__()
        self._filter_text = filter_text
        self._sort_modes = sort_modes
        self._sort_index = self._find_sort_index(sort_mode)
        self._sort_reverse = sort_reverse

    def compose(self) -> ComposeResult:
        with Vertical(id="filter_dialog"):
            yield Label("Filter clips")
            yield Input(
                value=self._filter_text,
                placeholder="tag:Finals label:K video:abc123",
                id="filter_input",
            )
            yield Static("", id="filter_sort")
            yield Static(
                "Fields: tag: label: video: title: score: opponent:",
                id="filter_hint",
            )
            yield Label("", id="filter_error")
            with Horizontal():
                yield Button("Cycle sort", id="filter_sort_cycle")
                yield Button("Toggle order", id="filter_sort_order")
                yield Button("Clear", id="filter_clear")
                yield Button("Apply", id="filter_apply")
                yield Button("Cancel", id="filter_cancel")

    def on_mount(self) -> None:
        self._refresh_sort_label()
        input_widget = self.query_one("#filter_input", Input)
        input_widget.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "filter_cancel":
            self.dismiss(None)
        elif event.button.id == "filter_apply":
            self._submit()
        elif event.button.id == "filter_clear":
            input_widget = self.query_one("#filter_input", Input)
            input_widget.value = ""
            self._filter_text = ""
        elif event.button.id == "filter_sort_cycle":
            self._sort_index = (self._sort_index + 1) % len(self._sort_modes)
            self._refresh_sort_label()
        elif event.button.id == "filter_sort_order":
            self._sort_reverse = not self._sort_reverse
            self._refresh_sort_label()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter_input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#filter_input", Input)
        error_label = self.query_one("#filter_error", Label)
        text = input_widget.value.strip()
        if text.count(":") > 12:
            error_label.update("Filter looks too complex; simplify and try again.")
            return
        error_label.update("")
        sort_mode = self._sort_modes[self._sort_index][0]
        self.dismiss((text, sort_mode, self._sort_reverse))

    def _refresh_sort_label(self) -> None:
        label = self.query_one("#filter_sort", Static)
        _key, name = self._sort_modes[self._sort_index]
        order = "desc" if self._sort_reverse else "asc"
        label.update(f"Sort: {name} ({order})")

    def _find_sort_index(self, sort_mode: str) -> int:
        for index, (key, _name) in enumerate(self._sort_modes):
            if key == sort_mode:
                return index
        return 0


class CreateEntryScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    CreateEntryScreen {
        align: center middle;
        background: $surface 80%;
    }

    #create_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #create_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root

    def compose(self) -> ComposeResult:
        with Vertical(id="create_dialog"):
            yield Label(f"Create in {self._root}")
            yield Input(
                placeholder="name or path (append / for folder)",
                id="create_input",
            )
            yield Label("", id="create_error")
            with Horizontal():
                yield Button("Create", id="create_submit")
                yield Button("Cancel", id="create_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create_cancel":
            self.dismiss(None)
        elif event.button.id == "create_submit":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "create_input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#create_input", Input)
        error_label = self.query_one("#create_error", Label)
        value = input_widget.value.strip()
        if not value:
            error_label.update("Please enter a name.")
            return
        self.dismiss(value)


class RenameEntryScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    RenameEntryScreen {
        align: center middle;
        background: $surface 80%;
    }

    #rename_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #rename_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self._current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename_dialog"):
            yield Label(f"Rename {self._current_name} to:")
            yield Input(value=self._current_name, id="rename_input")
            yield Label("", id="rename_error")
            with Horizontal():
                yield Button("Rename", id="rename_submit")
                yield Button("Cancel", id="rename_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename_cancel":
            self.dismiss(None)
        elif event.button.id == "rename_submit":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "rename_input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#rename_input", Input)
        error_label = self.query_one("#rename_error", Label)
        value = input_widget.value.strip()
        if not value:
            error_label.update("Please enter a name.")
            return
        self.dismiss(value)


class MoveEntryScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    MoveEntryScreen {
        align: center middle;
        background: $surface 80%;
    }

    #move_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #move_error {
        color: $error;
        height: 1;
    }
    """

    def __init__(self, current_path: Path) -> None:
        super().__init__()
        self._current_path = current_path

    def compose(self) -> ComposeResult:
        with Vertical(id="move_dialog"):
            yield Label(f"Move {self._current_path.name} to:")
            yield Input(value=str(self._current_path), id="move_input")
            yield Label("", id="move_error")
            with Horizontal():
                yield Button("Move", id="move_submit")
                yield Button("Cancel", id="move_cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "move_cancel":
            self.dismiss(None)
        elif event.button.id == "move_submit":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "move_input":
            self._submit()

    def _submit(self) -> None:
        input_widget = self.query_one("#move_input", Input)
        error_label = self.query_one("#move_error", Label)
        value = input_widget.value.strip()
        if not value:
            error_label.update("Please enter a destination.")
            return
        self.dismiss(value)


class DeleteEntryScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    DeleteEntryScreen {
        align: center middle;
        background: $surface 80%;
    }

    #delete_dialog {
        width: 70%;
        max-width: 80;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }
    """

    def __init__(self, current_path: Path) -> None:
        super().__init__()
        self._current_path = current_path

    def compose(self) -> ComposeResult:
        with Vertical(id="delete_dialog"):
            yield Label(f"Delete {self._current_path.name}?")
            with Horizontal():
                yield Button("Delete", id="delete_confirm")
                yield Button("Cancel", id="delete_cancel")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "delete_cancel":
            self.dismiss(False)
        elif event.button.id == "delete_confirm":
            self.dismiss(True)


def _build_search_index(
    root: Path, max_entries: int, show_hidden: bool
) -> tuple[list[Path], bool]:
    candidates: list[Path] = []

    def onerror(_: OSError) -> None:
        return None

    for current_root, dirnames, filenames in os.walk(root, onerror=onerror):
        current_path = Path(current_root)
        if not show_hidden:
            dirnames[:] = [
                name
                for name in dirnames
                if not is_hidden(current_path / name)
            ]
            filenames = [
                name
                for name in filenames
                if not is_hidden(current_path / name)
            ]
        for name in dirnames:
            candidates.append(current_path / name)
            if len(candidates) >= max_entries:
                return (candidates, True)
        for name in filenames:
            candidates.append(current_path / name)
            if len(candidates) >= max_entries:
                return (candidates, True)
    return (candidates, False)


def _score_search_path(path: Path, root: Path, query_lower: str) -> int | None:
    rel_text = _relative_display(root, path).lower()
    name_lower = path.name.lower()
    rel_score = _fuzzy_score(rel_text, query_lower)
    name_score = _fuzzy_score(name_lower, query_lower)
    scores = [score for score in (rel_score, name_score) if score is not None]
    if not scores:
        return None
    best = min(scores)
    if name_score is not None:
        best -= 5
    return best


def _format_search_label(root: Path, path: Path) -> Text:
    icon = file_icon_for_path(path)
    kind = FileEntryKind.DIR if path.is_dir() else FileEntryKind.FILE
    icon_style = file_icon_style_for_kind(kind, path)
    text_style = file_text_style_for_kind(kind)
    label = Text()
    label.append(icon, style=icon_style)
    label.append(" ")
    label.append(_relative_display(root, path), style=text_style)
    if is_hidden(path):
        label.stylize("dim")
    return label


def _relative_display(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    return str(relative) if str(relative) else "."


def _fuzzy_score(candidate: str, query: str) -> int | None:
    if not query:
        return None
    pos = -1
    score = 0
    for char in query:
        idx = candidate.find(char, pos + 1)
        if idx < 0:
            return None
        score += idx
        if idx == pos + 1:
            score -= 5
        pos = idx
    score += len(candidate)
    return score


def _is_valid_output_format(value: str) -> bool:
    if not value:
        return False
    return value.isalnum() and len(value) <= 6
