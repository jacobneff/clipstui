from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import stat
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass
from string import ascii_uppercase
from enum import Enum
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Static,
    TextArea,
)
from textual_image.widget import Image as PreviewImage

from .clip_time import coerce_time_input, extract_youtube_urls
from .clip_utils import (
    AutoTagOptions,
    analyze_overlaps,
    group_clips_by_video,
    plan_adjacent_merges,
    resolve_clips,
    ClipGroup,
    MergeSuggestion,
    OverlapFinding,
    OverlapKind,
)
from .download_queue import DownloadStatus, QueueItem
from .file_ops import (
    is_clip_file,
    is_valid_name,
    normalize_drive_path,
    resolve_new_entry,
    resolve_user_path,
)
from .fileops_apply import apply_plan
from .fileops_plan import (
    DELETE_MARKER,
    OperationPlan,
    OperationType,
    PathEntry,
    collect_confirmations,
    compute_plan,
    is_delete_marker_line,
    strip_delete_marker,
    validate_plan,
)
from .metadata import VideoMetadata, get_metadata
from .parser import ClipSpec, format_clip_file, parse_clip_file
from .presets import PresetProfile, find_preset, list_presets
from .resolve import (
    DEFAULT_OUTPUT_TEMPLATE,
    ResolvedClip,
    format_output_basename,
    resolve_clip,
    validate_output_template,
)
from .exports import (
    build_concat_list,
    build_manifest_entries,
    manifest_to_csv,
    manifest_to_json,
)
from .timeparse import format_seconds
from .thumbs import download_thumbnail, generate_clip_thumbnail
from .ui.edit_buffer import PlanPreviewScreen
from .ui.file_browser import FileEntryKind, file_icon_for_kind, is_hidden, path_sort_key
from .ui.file_buffer import FileBufferTextArea, strip_icon_prefix
from .ui.screens import (
    CommandAction,
    CommandPaletteScreen,
    CreateEntryScreen,
    DeleteEntryScreen,
    HelpScreen,
    MoveEntryScreen,
    OutputDirScreen,
    OutputFormatScreen,
    OutputTemplateScreen,
    PresetScreen,
    RenameEntryScreen,
    SearchResult,
    SearchScreen,
    ClipEditorScreen,
    TreeRootScreen,
    EndTimeScreen,
    PadInputScreen,
    MergeAdjacentScreen,
)
from .ytdlp_runner import DownloadResult, ProgressUpdate, run_ytdlp_with_progress

DEFAULT_PAD_BEFORE = 0
DEFAULT_PAD_AFTER = 0
DEFAULT_MERGE_GAP = 1.0
DEFAULT_OVERLAP_RATIO = 0.8
TIP_TEXT = "Tip: press ? for help (and / to search)"
HELP_TEXT = """Keyboard shortcuts
q  quit (global)
r  reload clip file
ctrl+p  command palette
h  toggle hidden files (when picker not focused)
m  set output format
T  set output template
o  set output directory
L  load preset profile
O  open output in player
Y  open YouTube at clip start
E  export manifest (csv/json)
C  export concat list (ffmpeg)
B  create rally pack folder
f  retry failed downloads
F  retry failed for current video
t  toggle auto-tag prefix
P  set global pad defaults
V  set pad for current video
S  set pad for selected clips
N  normalize pad overrides
g  merge adjacent clips
/  search files/folders (fuzzy)
?  help

File picker (NORMAL mode)
j/k  move up/down
gg/G  top/bottom
h/l  parent/enter
n/N  next/prev search match
enter  open directory / file
dd  delete line (stage delete)
u  undo
i/a  insert (at/after cursor)
o/O  new line below/above + insert
v/V  visual mode (char/line)
:  command mode (:w, :wq, :q, :q!)
q  close file picker

File picker (VISUAL mode)
j/k  expand selection up/down
gg/G  top/bottom
d  delete selection (stage delete)
esc  return to NORMAL

File picker (INSERT mode)
esc  return to NORMAL

Navigation
arrow keys to move between clips

Clip list
d/enter  download current clip (or selected clips)
space  toggle clip selection
p  paste clip from clipboard
K/B/A/D/S/E  label selected clips (kill/block/ace/dig/set/error)
enter/space on group header  expand/collapse group
e  edit clip
a  add clip

Clip editor
[ / ]  nudge active time by 0.1s
{ / }  nudge active time by 0.5s

Queue list
space  toggle queue selection
p  pause/resume selected
x  cancel selected
ctrl+up/down  move queue item
"""

LABEL_KEY_MAP = {
    "K": "K",
    "B": "B",
    "A": "A",
    "D": "D",
    "S": "S",
    "E": "E",
}

TOKYO_NIGHT_THEME = Theme(
    name="tokyo-night",
    primary="#7aa2f7",
    secondary="#7dcfff",
    accent="#bb9af7",
    warning="#e0af68",
    error="#f7768e",
    success="#9ece6a",
    foreground="#c0caf5",
    background="#1a1b26",
    surface="#1f2335",
    panel="#24283b",
    boost="#2f334d",
    variables={
        "block-cursor-background": "#7aa2f7",
        "block-cursor-foreground": "#1a1b26",
        "footer-key-foreground": "#7aa2f7",
        "input-selection-background": "#7aa2f7 30%",
        "button-color-foreground": "#1a1b26",
        "button-focus-text-style": "bold",
    },
)


@dataclass(frozen=True)
class _ClipLoadResult:
    resolved: list[ResolvedClip]
    groups: list[ClipGroup]
    overlap_index: dict[ResolvedClip, list[OverlapFinding]]
    merge_suggestions: list[MergeSuggestion]
    merge_index: dict[ResolvedClip, list[MergeSuggestion]]
    select_index: int | None


class FileBufferMode(Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    COMMAND = "COMMAND"
    VISUAL = "VISUAL"
    VISUAL_LINE = "VISUAL LINE"


class ClipListItem(ListItem):
    def __init__(
        self,
        resolved: ResolvedClip,
        selected: bool = False,
        warning: bool = False,
    ) -> None:
        self.resolved = resolved
        self._selected = selected
        self._warning = warning
        self._label = Label(_format_list_label(resolved, selected, warning))
        super().__init__(self._label, classes="clip-item")

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._label.update(_format_list_label(self.resolved, selected, self._warning))

    def set_warning(self, warning: bool) -> None:
        if self._warning == warning:
            return
        self._warning = warning
        self._label.update(_format_list_label(self.resolved, self._selected, warning))


class ClipGroupItem(ListItem):
    def __init__(self, group: ClipGroup, collapsed: bool, title: str | None) -> None:
        self.group = group
        self._collapsed = collapsed
        self._title = title
        self._label = Label(_format_group_label(group, collapsed, title))
        super().__init__(self._label, classes="clip-group")

    @property
    def video_id(self) -> str:
        return self.group.video_id

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self.refresh_label()

    def set_title(self, title: str | None) -> None:
        if self._title == title:
            return
        self._title = title
        self.refresh_label()

    def refresh_label(self) -> None:
        self._label.update(_format_group_label(self.group, self._collapsed, self._title))


class QueueListItem(ListItem):
    _STATUS_CLASSES = {
        DownloadStatus.QUEUED: "status-queued",
        DownloadStatus.DOWNLOADING: "status-downloading",
        DownloadStatus.PAUSED: "status-paused",
        DownloadStatus.DONE: "status-done",
        DownloadStatus.FAILED: "status-failed",
        DownloadStatus.CANCELED: "status-canceled",
    }

    def __init__(self, item: QueueItem, selected: bool = False) -> None:
        self.item = item
        self._selected = selected
        self._label = Label(_format_queue_label(item, selected), classes="queue_item_label")
        self._bar = ProgressBar(
            total=100,
            show_percentage=False,
            show_eta=False,
            classes="queue_item_bar",
        )
        self._sync_status_classes()
        percent = _progress_value(item)
        self._bar.update(total=100, progress=percent)
        super().__init__(Horizontal(self._label, self._bar), classes="queue_item")

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.refresh_label()

    def _sync_status_classes(self) -> None:
        status_class = self._STATUS_CLASSES.get(self.item.status, "status-queued")
        for class_name in self._STATUS_CLASSES.values():
            self._bar.remove_class(class_name)
        self._bar.add_class(status_class)

    def refresh_label(self) -> None:
        self._label.update(_format_queue_label(self.item, self._selected))
        self._sync_status_classes()
        percent = _progress_value(self.item)
        self._bar.update(total=100, progress=percent)


class ClipstuiApp(App):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reload", "Reload"),
        ("ctrl+p", "command_palette", "Command Palette"),
        ("c", "create_entry", "Create"),
        ("R", "rename_entry", "Rename"),
        ("M", "move_entry", "Move"),
        ("X", "delete_entry", "Delete"),
        ("h", "toggle_hidden", "Hidden"),
        ("o", "output_dir", "Output Dir"),
        ("m", "output_format", "Output Format"),
        ("T", "output_template", "Output Template"),
        ("L", "preset", "Preset"),
        ("O", "open_in_player", "Open in Player"),
        ("Y", "open_youtube", "Open YouTube"),
        ("E", "export_manifest", "Export Manifest"),
        ("C", "export_concat", "Export Concat"),
        ("B", "rally_pack", "Rally Pack"),
        ("f", "retry_failed", "Retry Failed"),
        ("F", "retry_failed_video", "Retry Failed (Video)"),
        ("t", "toggle_tag_prefix", "Tag Prefix"),
        ("P", "pad_global", "Pad Global"),
        ("V", "pad_video", "Pad Video"),
        ("S", "pad_selected", "Pad Selected"),
        ("N", "normalize_pads", "Normalize Pads"),
        ("g", "merge_adjacent", "Merge Adjacent"),
        ("/", "search", "Search"),
        ("?", "help", "Help"),
    ]

    CSS = """
    Screen {
        background: $background;
        color: $text;
    }

    #root {
        height: 100%;
    }

    #main {
        height: 1fr;
        padding: 1 1;
    }

    #left, #middle, #right {
        padding: 1 1;
        background: $surface;
    }

    #left {
        width: 30%;
        border: round $secondary;
    }

    #middle {
        width: 35%;
        border: round $primary;
        background: $panel;
    }

    #right {
        width: 35%;
        border: round $accent;
    }

    #file_buffer, #clip_list {
        height: 1fr;
    }

    #file_buffer {
        border: none;
        background: $surface;
        color: $text;
    }

    #file_buffer.mode-normal .text-area--cursor {
        background: $primary;
        color: $background;
    }

    #file_buffer.mode-insert .text-area--cursor {
        background: transparent;
        color: $primary;
    }

    #file_command {
        height: 1;
        width: 100%;
        border: round $boost;
        background: $panel;
        color: $text;
        padding: 0 1;
    }

    #file_command .input--text,
    #file_command .input--content {
        color: $text;
    }

    #file_command .input--cursor {
        background: $primary;
        color: $background;
    }

    #file_command .input--placeholder {
        color: $text-muted;
    }

    #file_buffer .text-area--cursor-line {
        background: $boost;
    }

    ListView {
        background: transparent;
    }

    ListView > .list-item {
        padding: 0 1;
    }

    ListView > .list-item.-hovered {
        background: #2a2f43;
        color: $text;
    }

    ListView > .list-item.-highlight {
        background: #3b4261;
        color: #e6e9ff;
        text-style: bold;
    }

    ListView > .list-item.-highlight.-hovered {
        background: #4b5173;
        color: #f0f2ff;
    }

    ListView > .list-item.-highlight Label {
        color: #e6e9ff;
    }

    ListView > .clip-group {
        color: #c0caf5;
    }

    ListView > .clip-group.-highlight {
        background: #3b4261;
        color: #f0f2ff;
    }

    ListView > .clip-group.-highlight Label {
        color: #f0f2ff;
    }


    #thumb_image {
        height: 12;
        width: auto;
        border: round $secondary;
    }

    #thumb_fallback {
        height: 12;
        width: 100%;
        border: round $secondary;
        background: $panel;
        padding: 0 1;
        overflow: hidden;
    }

    #preview_text {
        height: 1fr;
        padding: 1 1;
        background: $surface;
        border: round $boost;
        overflow-y: auto;
    }

    #queue_list {
        height: 8;
    }

    #file_status, #clips_label, #queue_label {
        height: 1;
        text-style: bold;
    }

    #file_status {
        color: $text-muted;
        text-style: none;
    }

    #clips_label, #queue_label {
        color: $primary;
    }

    #vim_status {
        height: 1;
        color: $text-muted;
    }

    .queue_item_label {
        width: 1fr;
        height: 1;
    }

    .queue_item_bar {
        width: 16;
        height: 1;
    }

    #queue_list .queue_item {
        height: 1;
        padding: 0 1;
    }

    ProgressBar {
        background: $panel;
        color: $primary;
    }

    ProgressBar.status-queued > Bar > .bar--bar,
    ProgressBar.status-queued > Bar > .bar--complete {
        color: #3b4261;
        background: #3b4261;
    }

    ProgressBar.status-downloading > Bar > .bar--bar,
    ProgressBar.status-downloading > Bar > .bar--complete {
        color: $secondary;
        background: $secondary;
    }

    ProgressBar.status-done > Bar > .bar--bar,
    ProgressBar.status-done > Bar > .bar--complete {
        color: $success;
        background: $success;
    }

    ProgressBar.status-paused > Bar > .bar--bar,
    ProgressBar.status-paused > Bar > .bar--complete {
        color: $warning;
        background: $warning;
    }

    ProgressBar.status-failed > Bar > .bar--bar,
    ProgressBar.status-failed > Bar > .bar--complete {
        color: $error;
        background: $error;
    }

    ProgressBar.status-canceled > Bar > .bar--bar,
    ProgressBar.status-canceled > Bar > .bar--complete {
        color: #565f89;
        background: #565f89;
    }

    #tip_bar {
        height: 1;
        padding: 0 1;
        content-align: left middle;
        color: $text-muted;
        background: $panel;
        text-style: italic;
    }

    .hidden {
        display: none;
    }
    """

    def __init__(
        self,
        clip_path: Path | None = None,
        output_dir: Path | None = None,
        output_format: str | None = None,
        output_template: str | None = None,
        preset: PresetProfile | None = None,
    ) -> None:
        super().__init__()
        self.register_theme(TOKYO_NIGHT_THEME)
        self.theme = TOKYO_NIGHT_THEME.name
        self.clip_path = clip_path
        self.output_dir = None
        self.output_format = _normalize_output_format("mp4")
        self.output_template = DEFAULT_OUTPUT_TEMPLATE
        self._output_format_override = False
        self._output_dir_override = False
        self._output_template_override = False
        self._tree_root = self._resolve_tree_root(clip_path)
        self._clips: list[ResolvedClip] = []
        self._clip_groups: list[ClipGroup] = []
        self._queue_items: list[QueueItem] = []
        self._queue_widgets: dict[int, QueueListItem] = {}
        self._queue_selection: set[int] = set()
        self._queue_ui_state: dict[
            int, tuple[float, float | None, float | None, int | None, DownloadStatus]
        ] = {}
        self._queue_preview_last = 0.0
        self._download_queue: queue.Queue[QueueItem] | None = None
        self._download_pending: set[int] = set()
        self._download_workers_started = False
        self._max_parallel_downloads = 2
        self._selected: ResolvedClip | None = None
        self._selected_group_video_id: str | None = None
        self._clip_list_index = 0
        self._queue_list_index = 0
        self._clip_selection: set[ResolvedClip] = set()
        self._collapsed_groups: set[str] = set()
        self._overlap_index: dict[ResolvedClip, list[OverlapFinding]] = {}
        self._merge_suggestions: list[MergeSuggestion] = []
        self._merge_index: dict[ResolvedClip, list[MergeSuggestion]] = {}
        self._auto_tag_prefix_video = False
        self._pad_before_default = DEFAULT_PAD_BEFORE
        self._pad_after_default = DEFAULT_PAD_AFTER
        self._last_search_query: str | None = None
        self._last_search_matches: list[Path] = []
        self._last_search_index = -1
        self._file_status: Label | None = None
        self._vim_status: Label | None = None
        self._file_buffer: FileBufferTextArea | None = None
        self._file_command: Input | None = None
        self._file_mode = FileBufferMode.NORMAL
        self._file_pending = ""
        self._file_visual_start: int | None = None
        self._file_visual_end: int | None = None
        self._file_visual_line_mode = False
        self._file_visual_anchor: tuple[int, int] | None = None
        self._file_visual_cursor: tuple[int, int] | None = None
        self._file_buffer_original_text = ""
        self._file_buffer_original_entries: list[PathEntry] = []
        self._file_listing_signature: tuple[object, ...] | None = None
        self._drive_picker_active = False
        self._drive_picker_return_root: Path | None = None
        self._clip_list: ListView | None = None
        self._queue_list: ListView | None = None
        self._preview_text: Static | None = None
        self._thumb_image: PreviewImage | None = None
        self._thumb_fallback: Static | None = None
        self._metadata_cache: dict[str, VideoMetadata] = {}
        self._metadata_errors: dict[str, str] = {}
        self._metadata_loading: set[str] = set()
        self._preview_timer = None
        self._pending_preview: ResolvedClip | None = None
        self._thumb_cache: dict[tuple[str, str], Path] = {}
        self._thumb_errors: dict[tuple[str, str], str] = {}
        self._thumb_loading: set[tuple[str, str]] = set()
        self._thumb_fallback_cache: dict[tuple[str, str], Text] = {}
        self._video_thumb_cache: dict[str, Path] = {}
        self._video_thumb_errors: dict[str, str] = {}
        self._video_thumb_loading: set[str] = set()
        self._video_thumb_queue: queue.Queue[tuple[str, str]] | None = None
        self._video_thumb_worker_started = False
        self._thumb_queue: queue.Queue[tuple[tuple[str, str], str, str, float]] | None = None
        self._thumb_worker_started = False
        self._metadata_queue: queue.Queue[tuple[str, str]] | None = None
        self._metadata_worker_started = False
        self._chafa_path: str | None = None
        self._player_command: str | None = None
        self._show_hidden = False
        self._clip_load_generation = 0
        if preset is not None:
            self._apply_preset_profile(preset, show_message=False)
        if output_dir is not None:
            self.output_dir = output_dir
            self._output_dir_override = True
        if output_format is not None:
            normalized = _normalize_output_format(output_format)
            if not _is_valid_output_format(normalized):
                raise ValueError(f"Invalid output format: {output_format}")
            self.output_format = normalized
            self._output_format_override = True
        if output_template is not None:
            validate_output_template(output_template)
            self.output_template = output_template
            self._output_template_override = True

    def compose(self) -> ComposeResult:
        with Vertical(id="root"):
            with Horizontal(id="main"):
                with Vertical(id="left"):
                    yield Label("", id="file_status")
                    yield Label("", id="vim_status")
                    yield FileBufferTextArea(
                        id="file_buffer",
                        read_only=True,
                        soft_wrap=False,
                        tab_behavior="focus",
                    )
                    yield Input(
                        placeholder=":w :wq :q :q!",
                        id="file_command",
                        classes="hidden",
                        compact=True,
                        select_on_focus=False,
                    )
                with Vertical(id="middle"):
                    yield Label("Clips", id="clips_label")
                    yield ListView(id="clip_list")
                    yield Label("Queue", id="queue_label")
                    yield ListView(id="queue_list")
                with Vertical(id="right"):
                    yield PreviewImage(None, id="thumb_image")
                    yield Static("Thumbnail: loading...", id="thumb_fallback", classes="hidden")
                    yield Static("Select a clip to preview.", id="preview_text")
            yield Static(TIP_TEXT, id="tip_bar")

    def on_mount(self) -> None:
        self._file_status = self.query_one("#file_status", Label)
        self._vim_status = self.query_one("#vim_status", Label)
        self._file_buffer = self.query_one("#file_buffer", FileBufferTextArea)
        self._file_command = self.query_one("#file_command", Input)
        self._clip_list = self.query_one("#clip_list", ListView)
        self._queue_list = self.query_one("#queue_list", ListView)
        self._preview_text = self.query_one("#preview_text", Static)
        self._thumb_image = self.query_one("#thumb_image", PreviewImage)
        self._thumb_fallback = self.query_one("#thumb_fallback", Static)
        self._update_left_status()
        if self._file_buffer is not None:
            self._file_buffer.set_root(self._tree_root)
            self._file_buffer.highlight_cursor_line = False
        self._set_file_mode(FileBufferMode.NORMAL)
        self.call_later(self._populate_file_buffer)
        self.set_interval(2.0, self._refresh_file_buffer_if_needed)
        if self.clip_path and self.clip_path.is_file():
            self.call_later(self._load_clip_on_startup)

    def action_help(self) -> None:
        self.push_screen(HelpScreen(HELP_TEXT))

    def action_output_dir(self) -> None:
        current = self._output_dir()
        self.push_screen(OutputDirScreen(current), self._handle_output_dir)

    def action_output_format(self) -> None:
        self.push_screen(
            OutputFormatScreen(self.output_format),
            self._handle_output_format,
        )

    def action_output_template(self) -> None:
        clip = self._selected or (self._clips[0] if self._clips else None)
        title = None
        if clip is not None:
            metadata = self._metadata_cache.get(clip.video_id)
            title = metadata.title if metadata and metadata.title else None
        self.push_screen(
            OutputTemplateScreen(self.output_template, clip, title),
            self._handle_output_template,
        )

    def action_command_palette(self) -> None:
        actions = self._command_palette_entries()
        if not actions:
            self._set_preview_message("No commands available.")
            return
        commands = [action for action, _ in actions]
        handlers = {action.action_id: handler for action, handler in actions}
        self.push_screen(
            CommandPaletteScreen(commands),
            lambda result: self._handle_command_palette(result, handlers),
        )

    def action_preset(self) -> None:
        presets = list_presets()
        if not presets:
            self._set_preview_message("No preset profiles available.")
            return
        self.push_screen(PresetScreen(presets), self._handle_preset)

    def action_open_in_player(self) -> None:
        target = self._resolve_action_target()
        if target is None:
            self._set_preview_message("No clip selected.")
            return
        clip, queue_item = target
        output_path = self._resolve_output_path(clip, queue_item)
        if output_path is None:
            return
        if not output_path.exists():
            self._set_preview_message(f"Output not found:\n{output_path}")
            return
        player = self._ensure_player_command()
        if not player:
            self._set_preview_message("Missing command: mpv or vlc")
            return
        try:
            subprocess.Popen([player, str(output_path)])
        except OSError as exc:
            self._set_preview_message(f"Failed to open player:\n{exc}")
            return
        self._set_preview_message(f"Opened in player:\n{output_path}")

    def action_open_youtube(self) -> None:
        target = self._resolve_action_target()
        if target is None:
            self._set_preview_message("No clip selected.")
            return
        clip, _ = target
        url = clip.clip.start_url
        if not url:
            self._set_preview_message("Clip has no start URL.")
            return
        opened = webbrowser.open(url)
        if not opened:
            self._set_preview_message("Failed to open browser.")

    def action_export_manifest(self) -> None:
        clips, scope = self._export_target_clips()
        if not clips:
            return
        output_dir = self._output_dir()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_preview_message(f"Failed to create output dir:\n{exc}")
            return
        entries = build_manifest_entries(
            clips,
            output_dir=output_dir,
            output_format=self.output_format,
            output_template=self.output_template,
            metadata=self._metadata_cache,
        )
        base_name = self._default_export_basename("clips")
        base_name = self._unique_manifest_basename(output_dir, base_name)
        csv_path = output_dir / f"{base_name}.csv"
        json_path = output_dir / f"{base_name}.json"
        try:
            csv_path.write_text(manifest_to_csv(entries), encoding="utf-8")
            json_path.write_text(
                manifest_to_json(
                    entries,
                    output_dir=output_dir,
                    output_format=self.output_format,
                    output_template=self.output_template,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            self._set_preview_message(f"Failed to write manifest:\n{exc}")
            return
        self._set_preview_message(
            f"Exported {len(entries)} clips ({scope}) to:\n{csv_path}\n{json_path}"
        )

    def action_export_concat(self) -> None:
        clips, scope = self._export_target_clips()
        if not clips:
            return
        output_dir = self._output_dir()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_preview_message(f"Failed to create output dir:\n{exc}")
            return
        entries = build_manifest_entries(
            clips,
            output_dir=output_dir,
            output_format=self.output_format,
            output_template=self.output_template,
            metadata=self._metadata_cache,
        )
        concat_paths = [entry.output_path.resolve() for entry in entries]
        base_name = self._default_export_basename("concat")
        concat_path = self._unique_export_path(
            output_dir / f"{base_name}.txt", is_dir=False
        )
        try:
            concat_path.write_text(build_concat_list(concat_paths), encoding="utf-8")
        except OSError as exc:
            self._set_preview_message(f"Failed to write concat list:\n{exc}")
            return
        self._set_preview_message(
            f"Exported concat list ({scope}):\n{concat_path}\nRun ffmpeg from:\n{output_dir}"
        )

    def action_rally_pack(self) -> None:
        clips, scope = self._export_target_clips()
        if not clips:
            return
        output_dir = self._output_dir()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_preview_message(f"Failed to create output dir:\n{exc}")
            return
        entries = build_manifest_entries(
            clips,
            output_dir=output_dir,
            output_format=self.output_format,
            output_template=self.output_template,
            metadata=self._metadata_cache,
        )
        pack_name = self._default_export_basename("rally_pack")
        pack_dir = self._unique_export_path(output_dir / pack_name, is_dir=True)
        try:
            pack_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_preview_message(f"Failed to create rally pack:\n{exc}")
            return
        copied: list[Path] = []
        missing: list[Path] = []
        warnings: list[str] = []
        for entry in entries:
            source = entry.output_path
            if not source.exists():
                missing.append(source)
                continue
            target = self._unique_child_path(pack_dir, source.name)
            try:
                shutil.copy2(source, target)
            except OSError:
                missing.append(source)
                continue
            copied.append(target)
        if copied:
            concat_text = build_concat_list([Path(path.name) for path in copied])
            try:
                (pack_dir / "concat.txt").write_text(concat_text, encoding="utf-8")
            except OSError as exc:
                warnings.append(f"Concat list failed: {exc}")
        if missing:
            missing_text = "\n".join(str(path) for path in missing) + "\n"
            try:
                (pack_dir / "missing.txt").write_text(missing_text, encoding="utf-8")
            except OSError as exc:
                warnings.append(f"Missing list failed: {exc}")
        message = (
            f"Rally pack ({scope}):\n{pack_dir}\nCopied {len(copied)} clip(s)"
        )
        if missing:
            message += f"\nMissing {len(missing)} clip(s)"
        if warnings:
            message += "\n" + "\n".join(warnings)
        self._set_preview_message(message)

    def action_tree_root(self) -> None:
        self.push_screen(TreeRootScreen(self._tree_root), self._handle_tree_root)

    def action_search(self) -> None:
        self.push_screen(
            SearchScreen(self._tree_root, self._show_hidden),
            self._handle_search,
        )

    def action_toggle_hidden(self) -> None:
        self._show_hidden = not self._show_hidden
        state = "on" if self._show_hidden else "off"
        self._set_preview_message(f"Hidden files: {state}")
        self._populate_file_buffer()

    def action_create_entry(self) -> None:
        if not self._file_list_ready():
            return
        self.push_screen(CreateEntryScreen(self._tree_root), self._handle_create_entry)

    def action_rename_entry(self) -> None:
        item = self._selected_file_entry()
        if item is None:
            return
        self.push_screen(RenameEntryScreen(item.path.name), self._handle_rename_entry)

    def action_move_entry(self) -> None:
        item = self._selected_file_entry()
        if item is None:
            return
        self.push_screen(MoveEntryScreen(item.path), self._handle_move_entry)

    def action_delete_entry(self) -> None:
        item = self._selected_file_entry()
        if item is None:
            return
        self.push_screen(DeleteEntryScreen(item.path), self._handle_delete_entry)

    def action_retry_failed(self) -> None:
        retry_items = [item for item in self._queue_items if item.status == DownloadStatus.FAILED]
        if not retry_items:
            self._set_preview_message("No failed downloads to retry.")
            return

        for item in retry_items:
            self._prepare_queue_item_for_restart(item)
            self._start_queue_item(item)

    def action_reload(self) -> None:
        if self.clip_path:
            self.load_clip_file(self.clip_path)

    def action_download(self) -> None:
        self._download_selected_clips()

    def action_download_all(self) -> None:
        if not self._clips:
            self._set_preview_message("No clips loaded.")
            return
        self._enqueue_clips(self._clips)

    def action_add_clip(self) -> None:
        if self.clip_path is None:
            self._set_preview_message("No clip file loaded.")
            return
        self.push_screen(
            ClipEditorScreen(
                clip=None,
                pad_before_default=self._pad_before_default,
                pad_after_default=self._pad_after_default,
                title="Add clip",
            ),
            self._handle_add_clip,
        )

    def action_edit_clip(self) -> None:
        if self.clip_path is None:
            self._set_preview_message("No clip file loaded.")
            return
        if not self._clips:
            self._set_preview_message("No clips loaded.")
            return
        item = self._current_clip_list_item()
        if item is None:
            self._set_preview_message("No clip selected.")
            return
        clip = item.resolved.clip
        try:
            index = self._clips.index(item.resolved)
        except ValueError:
            index = max(0, min(self._clip_list_index, len(self._clips) - 1))
        self.push_screen(
            ClipEditorScreen(
                clip=clip,
                pad_before_default=self._pad_before_default,
                pad_after_default=self._pad_after_default,
                title="Edit clip",
            ),
            lambda result: self._handle_edit_clip(result, index),
        )

    def action_clipboard_ingest(self) -> None:
        if self.clip_path is None:
            self._set_preview_message("No clip file loaded.")
            return
        text = _read_system_clipboard()
        if not text:
            self._set_preview_message("Clipboard is empty.")
            return
        urls = extract_youtube_urls(text)
        if len(urls) < 1:
            self._set_preview_message("Clipboard has no YouTube URLs.")
            return
        if len(urls) >= 2:
            self._append_clip_from_urls(urls[0], urls[1])
            if len(urls) > 2:
                self._set_preview_message("Added clip from first two URLs (extra URLs ignored).")
            return
        start_url = urls[0]
        try:
            start_url, _ = coerce_time_input(start_url, base_url=None)
        except ValueError as exc:
            self._set_preview_message(f"Start URL error:\n{exc}")
            return
        self.push_screen(
            EndTimeScreen(start_url),
            lambda result: self._handle_clipboard_end_time(start_url, result),
        )

    def action_toggle_tag_prefix(self) -> None:
        self._auto_tag_prefix_video = not self._auto_tag_prefix_video
        state = "on" if self._auto_tag_prefix_video else "off"
        self._set_preview_message(f"Auto-tag prefix by video: {state}")
        if self.clip_path:
            self.load_clip_file(self.clip_path, select_index=self._clip_list_index)

    def action_pad_global(self) -> None:
        self.push_screen(
            PadInputScreen(
                title="Set global pad defaults",
                pad_before=self._pad_before_default,
                pad_after=self._pad_after_default,
            ),
            self._handle_pad_global,
        )

    def action_pad_video(self) -> None:
        if not self._clips:
            self._set_preview_message("No clips loaded.")
            return
        target = self._current_clip_or_group_video()
        if target is None:
            self._set_preview_message("No clip or group selected.")
            return
        self.push_screen(
            PadInputScreen(
                title=f"Set pad for video {target}",
                pad_before=None,
                pad_after=None,
                hint="Leave blank to keep existing values.",
            ),
            lambda result: self._handle_pad_video(target, result),
        )

    def action_pad_selected(self) -> None:
        if not self._clips:
            self._set_preview_message("No clips loaded.")
            return
        self.push_screen(
            PadInputScreen(
                title="Set pad for selected clips",
                pad_before=None,
                pad_after=None,
                hint="Leave blank to keep existing values.",
            ),
            self._handle_pad_selected,
        )

    def action_normalize_pads(self) -> None:
        specs = self._load_clip_specs()
        if specs is None:
            return
        updated = _normalize_pad_overrides(
            specs,
            self._pad_before_default,
            self._pad_after_default,
        )
        if not updated:
            self._set_preview_message("No pad overrides to normalize.")
            return
        self._write_clip_specs(specs, select_index=self._clip_list_index)

    def action_retry_failed_video(self) -> None:
        video_id = self._current_queue_or_clip_video()
        if video_id is None:
            self._set_preview_message("No clip or queue item selected.")
            return
        retry_items = [
            item
            for item in self._queue_items
            if item.status == DownloadStatus.FAILED and item.resolved.video_id == video_id
        ]
        if not retry_items:
            self._set_preview_message("No failed downloads for this video.")
            return
        for item in retry_items:
            self._prepare_queue_item_for_restart(item)
            self._start_queue_item(item)

    def action_download_failed(self) -> None:
        if not self._clips:
            self._set_preview_message("No clips loaded.")
            return
        if self._clip_selection:
            clips = [clip for clip in self._clips if clip in self._clip_selection]
        else:
            item = self._current_clip_list_item()
            if item is None:
                self._set_preview_message("No clip selected.")
                return
            clips = [item.resolved]
        retry_items: list[QueueItem] = []
        for clip in clips:
            queue_item = self._latest_queue_item_for_clip(clip)
            if queue_item is not None and queue_item.status == DownloadStatus.FAILED:
                retry_items.append(queue_item)
        if not retry_items:
            self._set_preview_message("No failed downloads for selected clips.")
            return
        for item in retry_items:
            self._prepare_queue_item_for_restart(item)
            self._start_queue_item(item)

    def action_merge_adjacent(self) -> None:
        if not self._merge_suggestions:
            self._set_preview_message("No adjacent clips to merge.")
            return
        video_id = self._current_clip_or_group_video()
        suggestions = self._merge_suggestions
        if video_id is not None:
            suggestions = [
                suggestion
                for suggestion in suggestions
                if suggestion.video_id == video_id
            ]
        if not suggestions:
            self._set_preview_message("No adjacent clips to merge.")
            return
        self.push_screen(
            MergeAdjacentScreen(suggestions),
            lambda result: self._handle_merge_adjacent(result, suggestions),
        )

    def _handle_add_clip(self, clip: ClipSpec | None) -> None:
        if clip is None:
            return
        specs = self._load_clip_specs()
        if specs is None:
            return
        specs.append(clip)
        self._write_clip_specs(specs, select_index=len(specs) - 1)

    def _handle_edit_clip(self, clip: ClipSpec | None, index: int) -> None:
        if clip is None:
            return
        specs = self._load_clip_specs()
        if specs is None:
            return
        if index < 0 or index >= len(specs):
            self._set_preview_message("Clip index out of range.")
            return
        specs[index] = clip
        self._write_clip_specs(specs, select_index=index)

    def _handle_pad_global(self, result: tuple[int | None, int | None] | None) -> None:
        if result is None:
            return
        pad_before, pad_after = result
        if pad_before is None and pad_after is None:
            self._set_preview_message("No pad changes applied.")
            return
        if pad_before is not None:
            self._pad_before_default = pad_before
        if pad_after is not None:
            self._pad_after_default = pad_after
        if self.clip_path:
            self.load_clip_file(self.clip_path, select_index=self._clip_list_index)

    def _handle_pad_video(
        self,
        video_id: str,
        result: tuple[int | None, int | None] | None,
    ) -> None:
        if result is None:
            return
        pad_before, pad_after = result
        if pad_before is None and pad_after is None:
            self._set_preview_message("No pad changes applied.")
            return
        specs = self._load_clip_specs()
        if specs is None:
            return
        indices = [index for index, clip in enumerate(self._clips) if clip.video_id == video_id]
        if not indices:
            self._set_preview_message("No clips found for selected video.")
            return
        _apply_pad_updates(specs, indices, pad_before, pad_after)
        self._write_clip_specs(specs, select_index=min(indices))

    def _handle_pad_selected(self, result: tuple[int | None, int | None] | None) -> None:
        if result is None:
            return
        pad_before, pad_after = result
        if pad_before is None and pad_after is None:
            self._set_preview_message("No pad changes applied.")
            return
        specs = self._load_clip_specs()
        if specs is None:
            return
        if self._clip_selection:
            indices = [
                index
                for index, clip in enumerate(self._clips)
                if clip in self._clip_selection
            ]
        else:
            current = self._current_clip_list_item()
            if current is None:
                self._set_preview_message("No clip selected.")
                return
            indices = [self._clips.index(current.resolved)]
        _apply_pad_updates(specs, indices, pad_before, pad_after)
        self._write_clip_specs(specs, select_index=min(indices))

    def _apply_label_to_selection(self, label: str) -> None:
        specs = self._load_clip_specs()
        if specs is None:
            return
        if self._clip_selection:
            indices = [
                index
                for index, clip in enumerate(self._clips)
                if clip in self._clip_selection
            ]
        else:
            current = self._current_clip_list_item()
            if current is None:
                self._set_preview_message("No clip selected.")
                return
            indices = [self._clips.index(current.resolved)]
        if not indices:
            self._set_preview_message("No clip selected.")
            return
        for index in indices:
            spec = specs[index]
            specs[index] = ClipSpec(
                start_url=spec.start_url,
                end_url=spec.end_url,
                tag=spec.tag,
                label=label,
                rotation=spec.rotation,
                score=spec.score,
                opponent=spec.opponent,
                serve_target=spec.serve_target,
                pad_before=spec.pad_before,
                pad_after=spec.pad_after,
            )
        self._write_clip_specs(specs, select_index=min(indices))

    def _append_clip_from_urls(self, start_url: str, end_url: str) -> None:
        specs = self._load_clip_specs()
        if specs is None:
            return
        try:
            start_url, _ = coerce_time_input(start_url, base_url=None)
            end_url, _ = coerce_time_input(end_url, base_url=None)
        except ValueError as exc:
            self._set_preview_message(f"Clipboard URL error:\n{exc}")
            return
        clip = ClipSpec(start_url=start_url, end_url=end_url, tag=None)
        try:
            resolve_clip(clip, self._pad_before_default, self._pad_after_default)
        except ValueError as exc:
            self._set_preview_message(f"Clip error:\n{exc}")
            return
        specs.append(clip)
        self._write_clip_specs(specs, select_index=len(specs) - 1)

    def _handle_clipboard_end_time(self, start_url: str, result: str | None) -> None:
        if result is None:
            return
        try:
            end_url, _ = coerce_time_input(result, base_url=start_url)
        except ValueError as exc:
            self._set_preview_message(f"End time error:\n{exc}")
            return
        self._append_clip_from_urls(start_url, end_url)

    def _handle_merge_adjacent(
        self,
        confirmed: bool | None,
        suggestions: list[MergeSuggestion],
    ) -> None:
        if not confirmed:
            return
        specs = self._load_clip_specs()
        if specs is None:
            return
        index_map = {clip: index for index, clip in enumerate(self._clips)}
        merge_indices: set[int] = set()
        for suggestion in suggestions:
            first_index = index_map.get(suggestion.first)
            second_index = index_map.get(suggestion.second)
            if first_index is None or second_index is None:
                continue
            if second_index != first_index + 1:
                continue
            merge_indices.add(first_index)
        if not merge_indices:
            self._set_preview_message("No adjacent clips to merge.")
            return
        merged: list[ClipSpec] = []
        index = 0
        while index < len(specs):
            if index in merge_indices and index + 1 < len(specs):
                first = specs[index]
                second = specs[index + 1]
                merged.append(
                    ClipSpec(
                        start_url=first.start_url,
                        end_url=second.end_url,
                        tag=first.tag or second.tag,
                        label=first.label or second.label,
                        rotation=first.rotation or second.rotation,
                        score=first.score or second.score,
                        opponent=first.opponent or second.opponent,
                        serve_target=first.serve_target or second.serve_target,
                        pad_before=first.pad_before,
                        pad_after=second.pad_after,
                    )
                )
                index += 2
                continue
            merged.append(specs[index])
            index += 1
        self._write_clip_specs(merged, select_index=min(merge_indices))

    def _load_clip_specs(self) -> list[ClipSpec] | None:
        if self.clip_path is None:
            self._set_preview_message("No clip file loaded.")
            return None
        try:
            text = self.clip_path.read_text(encoding="utf-8")
        except OSError as exc:
            self._set_preview_message(f"Failed to read clip file:\n{exc}")
            return None
        try:
            return parse_clip_file(text)
        except ValueError as exc:
            self._set_preview_message(f"Parse error:\n{exc}")
            return None

    def _clear_clip_state(self) -> None:
        self._clips = []
        self._clip_groups = []
        self._overlap_index = {}
        self._merge_suggestions = []
        self._merge_index = {}
        self._clip_selection.clear()
        self._selected = None
        self._selected_group_video_id = None

    def _write_clip_specs(self, specs: list[ClipSpec], *, select_index: int | None) -> None:
        if self.clip_path is None:
            self._set_preview_message("No clip file loaded.")
            return
        text = format_clip_file(specs)
        try:
            self.clip_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self._set_preview_message(f"Failed to write clip file:\n{exc}")
            return
        self.load_clip_file(self.clip_path, select_index=select_index)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, ClipListItem):
            try:
                self._clip_list_index = self._clip_list_items().index(event.item)
            except ValueError:
                self._clip_list_index = 0
            self._schedule_preview(event.item.resolved)
        elif isinstance(event.item, ClipGroupItem):
            self._set_group_preview(event.item.group)
        elif isinstance(event.item, QueueListItem):
            try:
                self._queue_list_index = self._queue_list_items().index(event.item)
            except ValueError:
                self._queue_list_index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ClipListItem):
            self._apply_preview_now(event.item.resolved)

    def on_focus(self, event: events.Focus) -> None:
        if event.widget in {self._file_buffer, self._file_command}:
            self._update_mode_status()

    def on_blur(self, event: events.Blur) -> None:
        if event.widget in {self._file_buffer, self._file_command}:
            self._update_mode_status()

    def on_key(self, event: events.Key) -> None:
        if self._file_command is not None and self._file_command.has_focus:
            if event.key == "escape":
                self._exit_command_mode()
                event.stop()
            return
        if self._file_buffer is not None and self._file_buffer.has_focus:
            if self._file_mode == FileBufferMode.NORMAL:
                if event.key in {"escape", "ctrl+c"}:
                    self._file_pending = ""
                    return
                key = event.character or event.key
                if key == ":" or event.key in {"colon", ":"}:
                    self._enter_command_mode()
                    event.stop()
                    return
                if self._handle_file_normal_key(key):
                    event.stop()
                    return
                blocked_keys = {
                    "q",
                    "r",
                    "ctrl+p",
                    "d",
                    "A",
                    "f",
                    "F",
                    "t",
                    "P",
                    "V",
                    "S",
                    "N",
                    "g",
                    "L",
                    "O",
                    "T",
                    "Y",
                    "E",
                    "C",
                    "B",
                    "c",
                    "R",
                    "M",
                    "X",
                    "h",
                    "o",
                    "m",
                    "/",
                    "?",
                }
                if key in blocked_keys:
                    event.stop()
                return
            if self._file_mode in {FileBufferMode.VISUAL, FileBufferMode.VISUAL_LINE}:
                if event.key in {"escape", "ctrl+c"}:
                    self._exit_visual_mode()
                    event.stop()
                    return
                key = event.character or event.key
                if key == ":" or event.key in {"colon", ":"}:
                    self._enter_command_mode()
                    event.stop()
                    return
                if self._handle_file_visual_key(key):
                    event.stop()
                    return
                blocked_keys = {
                    "q",
                    "r",
                    "ctrl+p",
                    "d",
                    "A",
                    "f",
                    "F",
                    "t",
                    "P",
                    "V",
                    "S",
                    "N",
                    "g",
                    "L",
                    "O",
                    "T",
                    "Y",
                    "E",
                    "C",
                    "B",
                    "c",
                    "R",
                    "M",
                    "X",
                    "h",
                    "o",
                    "m",
                    "/",
                    "?",
                }
                if key in blocked_keys:
                    event.stop()
                return
            if self._file_mode == FileBufferMode.INSERT:
                if event.key == "escape":
                    self._set_file_mode(FileBufferMode.NORMAL)
                    event.stop()
                return
        if self._clip_list is not None and self._clip_list.has_focus:
            current_item = self._current_clip_list_widget()
            label = LABEL_KEY_MAP.get(event.key)
            if label is not None:
                self._apply_label_to_selection(label)
                event.stop()
                return
            if event.key in {"enter", "return"}:
                if isinstance(current_item, ClipGroupItem):
                    self._toggle_clip_group(current_item.video_id)
                else:
                    self._download_selected_clips()
                event.stop()
                return
            if event.key == "d":
                self._download_selected_clips()
                event.stop()
                return
            if event.key in {"space", " "}:
                if isinstance(current_item, ClipGroupItem):
                    self._toggle_clip_group(current_item.video_id)
                else:
                    item = self._current_clip_list_item()
                    if item is not None:
                        self._toggle_clip_selection(item)
                event.stop()
                return
            if event.key == "e":
                self.action_edit_clip()
                event.stop()
                return
            if event.key == "a":
                self.action_add_clip()
                event.stop()
                return
            if event.key == "p":
                self.action_clipboard_ingest()
                event.stop()
                return
        if self._queue_list is not None and self._queue_list.has_focus:
            if event.key in {"space", " "}:
                item = self._current_queue_list_item()
                if item is not None:
                    self._toggle_queue_selection(item)
                event.stop()
                return
            if event.key == "p":
                self._toggle_queue_pause()
                event.stop()
                return
            if event.key in {"x", "delete"}:
                self._cancel_queue_items()
                event.stop()
                return
            if event.key in {"ctrl+up", "alt+up"}:
                self._move_queue_items(-1)
                event.stop()
                return
            if event.key in {"ctrl+down", "alt+down"}:
                self._move_queue_items(1)
                event.stop()
                return

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "file_command":
            return
        command = event.value
        event.input.value = ""
        self._handle_file_command(command)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self._file_buffer:
            self._update_mode_status()

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area is self._file_buffer and self._file_mode in {
            FileBufferMode.VISUAL,
            FileBufferMode.VISUAL_LINE,
        }:
            self._update_visual_selection()

    def load_clip_file(self, path: Path, *, select_index: int | None = None) -> None:
        self.clip_path = path
        if not self._output_dir_override:
            self.output_dir = path.parent
        if self._tree_root != path.parent:
            self._set_tree_root(path.parent)
        self._update_left_status()
        list_view = self._clip_list
        if list_view is None:
            return
        self._clip_load_generation += 1
        generation = self._clip_load_generation
        self._clear_clip_state()
        self._clip_list_index = 0
        list_view.clear()
        list_view.append(ListItem(Label("Loading clips...")))
        list_view.index = 0
        self._set_preview_message("Loading clips...")
        self._show_thumbnail_message("Thumbnail: loading...")
        self._pending_preview = None
        if self._preview_timer is not None:
            self._preview_timer.stop()
            self._preview_timer = None

        def worker() -> None:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                self.call_from_thread(
                    self._apply_clip_load_error,
                    generation,
                    f"Failed to read file:\n{exc}",
                )
                return

            try:
                specs = parse_clip_file(text)
            except ValueError as exc:
                self.call_from_thread(
                    self._apply_clip_load_error,
                    generation,
                    f"Parse error:\n{exc}",
                )
                return

            try:
                resolved = resolve_clips(
                    specs,
                    self._pad_before_default,
                    self._pad_after_default,
                    auto_tag=AutoTagOptions(prefix_by_video=self._auto_tag_prefix_video),
                )
            except ValueError as exc:
                self.call_from_thread(
                    self._apply_clip_load_error,
                    generation,
                    f"Resolve error:\n{exc}",
                )
                return

            groups = group_clips_by_video(resolved)
            overlap_index = _index_overlaps(
                analyze_overlaps(resolved, heavy_overlap_ratio=DEFAULT_OVERLAP_RATIO)
            )
            merge_suggestions = plan_adjacent_merges(
                resolved, gap_threshold=DEFAULT_MERGE_GAP
            )
            merge_index = _index_merges(merge_suggestions)
            if resolved:
                if select_index is None:
                    selected_index = 0
                else:
                    selected_index = max(0, min(select_index, len(resolved) - 1))
            else:
                selected_index = 0
            result = _ClipLoadResult(
                resolved=resolved,
                groups=groups,
                overlap_index=overlap_index,
                merge_suggestions=merge_suggestions,
                merge_index=merge_index,
                select_index=selected_index,
            )
            self.call_from_thread(self._apply_clip_load_result, generation, result)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_clip_load_error(self, generation: int, message: str) -> None:
        if generation != self._clip_load_generation:
            return
        if self._clip_list is not None:
            self._clip_list.clear()
            self._clip_list.index = None
        self._set_preview_message(message)
        self._show_thumbnail_message("Thumbnail: --")
        self._clear_clip_state()

    def _apply_clip_load_result(self, generation: int, result: _ClipLoadResult) -> None:
        if generation != self._clip_load_generation:
            return
        self._clips = result.resolved
        self._clip_groups = result.groups
        self._collapsed_groups.intersection_update(
            {group.video_id for group in self._clip_groups}
        )
        self._overlap_index = result.overlap_index
        self._merge_suggestions = result.merge_suggestions
        self._merge_index = result.merge_index
        self._clip_selection.clear()
        selected_index = result.select_index or 0
        self._clip_list_index = selected_index
        self._render_clip_list(selected_index)

        if self._clips:
            self._set_preview(self._clips[selected_index])
        else:
            self._set_preview_message("No clips found.")
            self._show_thumbnail_message("Thumbnail: --")
            self._selected = None

    def _populate_file_buffer(
        self, select_mode: str = "keep", focus_path: Path | None = None
    ) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        if self._drive_picker_active:
            lines: list[str] = []
            drives = self._list_drive_roots()
            lines.extend(_format_drive_line(drive) for drive in drives)
            text = "\n".join(lines)
            buffer.text = text
            self._file_buffer_original_text = text
            self._file_buffer_original_entries = []
            self._file_listing_signature = self._signature_for_drives(drives)
            self._file_pending = ""
            self._set_file_mode(FileBufferMode.NORMAL)
            if self._drive_picker_return_root is not None:
                self._focus_drive_line(self._drive_picker_return_root)
            elif lines:
                self._set_buffer_cursor_line(0)
            self._set_search_matches(self._last_search_query)
            return
        current_path = None
        if select_mode == "keep":
            current_path = self._current_buffer_path()
        elif select_mode in {"up", "path"}:
            current_path = focus_path
        try:
            entries = self._list_file_entries()
        except OSError as exc:
            self._set_preview_message(f"Failed to read directory:\n{exc}")
            return
        lines: list[str] = []
        parent_line = _format_parent_line(self._tree_root)
        if parent_line:
            lines.append(parent_line)
        lines.extend(_format_entry_line(self._tree_root, entry) for entry in entries)
        text = "\n".join(lines)
        buffer.text = text
        self._file_buffer_original_text = text
        self._file_buffer_original_entries = entries
        self._file_listing_signature = self._signature_for_entries(entries)
        self._file_pending = ""
        self._set_file_mode(FileBufferMode.NORMAL)
        if select_mode == "first" and entries:
            current_path = entries[0].path
        if current_path:
            self._focus_file_buffer_path(current_path)
        elif lines:
            self._set_buffer_cursor_line(0)
        self._set_search_matches(self._last_search_query)

    def _list_file_entries(self) -> list[PathEntry]:
        entries: list[PathEntry] = []
        with os.scandir(self._tree_root) as iterator:
            for entry in iterator:
                if not self._show_hidden and _entry_is_hidden(entry):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                entries.append(PathEntry(Path(entry.path), is_dir))
        directories = [entry for entry in entries if entry.is_dir]
        files = [entry for entry in entries if not entry.is_dir]
        directories.sort(key=lambda item: path_sort_key(item.path))
        files.sort(key=lambda item: path_sort_key(item.path))
        return directories + files

    def _list_drive_roots(self) -> list[Path]:
        drives: list[Path] = []
        for letter in ascii_uppercase:
            path = Path(f"{letter}:\\")
            try:
                if path.exists():
                    drives.append(path)
            except OSError:
                continue
        return drives

    def _signature_for_entries(self, entries: list[PathEntry]) -> tuple[object, ...]:
        items = tuple((entry.path.name, entry.is_dir) for entry in entries)
        return ("dir", str(self._tree_root), self._show_hidden, items)

    def _signature_for_drives(self, drives: list[Path]) -> tuple[object, ...]:
        items = tuple(str(drive) for drive in drives)
        return ("drives", items)

    def _file_buffer_lines(self) -> list[str]:
        buffer = self._file_buffer
        if buffer is None:
            return []
        return [buffer.document[idx] for idx in range(buffer.document.line_count)]

    def _file_buffer_line_count(self) -> int:
        buffer = self._file_buffer
        if buffer is None:
            return 0
        return buffer.document.line_count

    def _current_buffer_line(self) -> str:
        buffer = self._file_buffer
        if buffer is None:
            return ""
        lines = self._file_buffer_lines()
        if not lines:
            return ""
        row, _ = buffer.cursor_location
        row = max(0, min(row, len(lines) - 1))
        return lines[row]

    def _set_buffer_cursor_line(self, index: int, column: int | None = None) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        lines = self._file_buffer_lines()
        if not lines:
            return
        index = max(0, min(index, len(lines) - 1))
        line = lines[index]
        if column is None:
            _, current_column = buffer.cursor_location
            column = min(current_column, len(line))
        else:
            column = max(0, min(column, len(line)))
        buffer.move_cursor((index, column), record_width=False)

    def _file_buffer_dirty(self) -> bool:
        buffer = self._file_buffer
        if buffer is None:
            return False
        return buffer.text != self._file_buffer_original_text

    def _should_auto_refresh_file_buffer(self) -> bool:
        buffer = self._file_buffer
        if buffer is None:
            return False
        if not buffer.has_focus and not self._drive_picker_active:
            return False
        if self._file_buffer_dirty():
            return False
        if buffer.has_focus and self._file_mode != FileBufferMode.NORMAL:
            return False
        return True

    def _refresh_file_buffer_if_needed(self) -> None:
        if not self._should_auto_refresh_file_buffer():
            return
        try:
            if self._drive_picker_active:
                drives = self._list_drive_roots()
                signature = self._signature_for_drives(drives)
            else:
                entries = self._list_file_entries()
                signature = self._signature_for_entries(entries)
        except OSError:
            return
        if signature != self._file_listing_signature:
            self._populate_file_buffer(select_mode="keep")

    def _download_queue_item(self, item: QueueItem) -> None:
        if not (item.pause_requested or item.cancel_requested or item.cancel_event.is_set()):
            self.call_from_thread(
                self._update_queue_item,
                item,
                DownloadStatus.DOWNLOADING,
                None,
                None,
            )
        output_dir = self._output_dir()

        def on_progress(update: ProgressUpdate) -> None:
            self.call_from_thread(self._apply_progress_update, item, update)

        result = run_ytdlp_with_progress(
            item.resolved,
            output_dir,
            item.output_format,
            on_progress,
            item.cancel_event,
            output_name=item.output_name,
        )
        self.call_from_thread(self._apply_download_result, item, result)

    def _queue_refresh_interval(self, item_id: int) -> float:
        if self._queue_list is not None and self._queue_list.has_focus:
            return 0.12
        if item_id in self._queue_selection:
            return 0.2
        return 0.5

    def _maybe_refresh_preview_from_queue(
        self, item: QueueItem, now: float, *, force: bool = False
    ) -> None:
        if self._selected is None:
            return
        if item.resolved != self._selected:
            return
        interval = 0.12 if self._queue_list is not None and self._queue_list.has_focus else 0.2
        if force or now - self._queue_preview_last >= interval:
            self._queue_preview_last = now
            self._refresh_preview_text(self._selected, item)

    def _apply_download_result(self, item: QueueItem, result: DownloadResult) -> None:
        if result.status == DownloadStatus.CANCELED:
            if item.pause_requested:
                item.pause_requested = False
                item.cancel_event.clear()
                self._update_queue_item(item, DownloadStatus.PAUSED, None, result.output_path)
                self._set_preview_message("Download paused.")
                return
            item.cancel_requested = False
            item.cancel_event.clear()
            self._update_queue_item(item, DownloadStatus.CANCELED, "Canceled", result.output_path)
            self._set_preview_message("Download canceled.")
            return
        item.pause_requested = False
        item.cancel_requested = False
        item.cancel_event.clear()
        if result.status == DownloadStatus.DONE:
            item.progress = 100.0
        self._update_queue_item(item, result.status, result.error, result.output_path)
        if result.status == DownloadStatus.DONE and result.output_path:
            sidecar_error = self._write_clip_sidecar(item.resolved, result.output_path)
            message = f"Download complete:\n{result.output_path}"
            if sidecar_error:
                message = f"{message}\nSidecar error: {sidecar_error}"
            self._set_preview_message(message)
        elif result.status == DownloadStatus.FAILED and result.error:
            self._set_preview_message(f"Download failed:\n{result.error}")

    def _write_clip_sidecar(self, clip: ResolvedClip, output_path: Path) -> str | None:
        if not _clip_has_context(clip):
            return None
        metadata = self._metadata_cache.get(clip.video_id)
        payload = _build_clip_sidecar_payload(clip, output_path, metadata)
        sidecar_path = _sidecar_path(output_path)
        try:
            sidecar_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return str(exc)
        return None

    def _apply_progress_update(self, item: QueueItem, update: ProgressUpdate) -> None:
        if item.pause_requested or item.cancel_requested:
            return
        if item.status in {DownloadStatus.PAUSED, DownloadStatus.CANCELED}:
            return
        if update.percent is not None:
            item.progress = update.percent
        if update.speed_bps is not None:
            item.speed_bps = update.speed_bps
        if update.eta_seconds is not None:
            item.eta_seconds = update.eta_seconds
        if item.status != DownloadStatus.DOWNLOADING:
            item.status = DownloadStatus.DOWNLOADING
        now = time.monotonic()
        item_id = id(item)
        last_state = self._queue_ui_state.get(item_id)
        if last_state is None:
            last_time = 0.0
            last_percent = None
            last_speed = None
            last_eta = None
            last_status = item.status
        else:
            last_time, last_percent, last_speed, last_eta, last_status = last_state
        status_changed = last_state is None or last_status != item.status
        metrics_changed = (
            last_percent != item.progress
            or last_speed != item.speed_bps
            or last_eta != item.eta_seconds
        )
        if not status_changed and not metrics_changed:
            return
        interval = self._queue_refresh_interval(item_id)
        if not status_changed and now - last_time < interval:
            self._maybe_refresh_preview_from_queue(item, now)
            return
        self._queue_ui_state[item_id] = (
            now,
            item.progress,
            item.speed_bps,
            item.eta_seconds,
            item.status,
        )
        widget = self._queue_widgets.get(id(item))
        if widget is not None:
            widget.refresh_label()
        self._maybe_refresh_preview_from_queue(item, now)

    def _update_queue_item(
        self,
        item: QueueItem,
        status: DownloadStatus,
        error: str | None,
        output_path: Path | None,
    ) -> None:
        if status == DownloadStatus.DOWNLOADING and (item.pause_requested or item.cancel_requested):
            return
        item.status = status
        item.error = error
        item.output_path = output_path
        widget = self._queue_widgets.get(id(item))
        if widget is not None:
            widget.refresh_label()
        now = time.monotonic()
        self._queue_ui_state[id(item)] = (
            now,
            item.progress,
            item.speed_bps,
            item.eta_seconds,
            item.status,
        )
        self._maybe_refresh_preview_from_queue(item, now, force=True)

    def _set_preview(self, clip: ResolvedClip) -> None:
        if self._preview_text is None:
            return
        self._selected = clip
        self._selected_group_video_id = None
        self._ensure_metadata(clip)
        queue_item = self._latest_queue_item_for_clip(clip)
        self._refresh_preview_text(clip, queue_item)
        self._refresh_thumbnail(clip)

    def _set_group_preview(self, group: ClipGroup) -> None:
        if self._preview_text is None:
            return
        self._selected = None
        self._selected_group_video_id = group.video_id
        if group.clips:
            self._ensure_metadata(group.clips[0])
        metadata = self._metadata_cache.get(group.video_id)
        title = _group_title(metadata)
        self._set_preview_message(_format_group_preview(group, title))
        self._show_group_thumbnail(group.video_id)

    def _schedule_preview(self, clip: ResolvedClip) -> None:
        self._pending_preview = clip
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.12, self._apply_pending_preview)

    def _apply_preview_now(self, clip: ResolvedClip) -> None:
        self._pending_preview = None
        if self._preview_timer is not None:
            self._preview_timer.stop()
            self._preview_timer = None
        self._set_preview(clip)

    def _apply_pending_preview(self) -> None:
        clip = self._pending_preview
        self._pending_preview = None
        if clip is None:
            return
        self._set_preview(clip)

    def _set_preview_message(self, message: str) -> None:
        if self._preview_text is None:
            return
        self._preview_text.update(message)

    def _update_left_status(self) -> None:
        if self._file_status is None:
            return
        output_dir = self._output_dir()
        if self.clip_path:
            self._file_status.update(
                f"Root:\n{self._tree_root}\n\nFile:\n{self.clip_path}\n\nOutput:\n{output_dir}\n\nFormat:\n.{self.output_format}\n\nTemplate:\n{self.output_template}"
            )
        else:
            self._file_status.update(
                f"Root:\n{self._tree_root}\n\nFile:\n(none)\n\nOutput:\n{output_dir}\n\nFormat:\n.{self.output_format}\n\nTemplate:\n{self.output_template}"
            )

    def _update_mode_status(self, message: str | None = None) -> None:
        if self._vim_status is None:
            return
        focus = ""
        if self._file_buffer is not None and self._file_buffer.has_focus:
            focus = " (picker)"
        elif self._file_command is not None and self._file_command.has_focus:
            focus = " (command)"
        dirty = "*" if self._file_buffer_dirty() else ""
        suffix = f" | {message}" if message else ""
        self._vim_status.update(f"Mode: {self._file_mode.value}{dirty}{focus}{suffix}")

    def _resolve_tree_root(self, clip_path: Path | None) -> Path:
        base = clip_path if clip_path is not None else Path.cwd()
        if base.is_file():
            base = base.parent
        return base

    def _handle_output_dir(self, result: Path | None) -> None:
        if result is None:
            return
        try:
            result.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._set_preview_message(f"Failed to set output dir:\n{exc}")
            return
        self.output_dir = result
        self._output_dir_override = True
        self._update_left_status()
        self._set_preview_message(f"Output directory set:\n{result}")

    def _handle_output_format(self, result: str | None) -> None:
        if result is None:
            return
        normalized = _normalize_output_format(result)
        if not _is_valid_output_format(normalized):
            self._set_preview_message(f"Invalid output format:\n{result}")
            return
        self.output_format = normalized
        self._update_left_status()
        if self._selected is not None:
            self._set_preview(self._selected)
        self._set_preview_message(f"Output format set: .{self.output_format}")

    def _handle_output_template(self, result: str | None) -> None:
        if result is None:
            return
        self.output_template = result
        self._output_template_override = True
        self._update_left_status()
        if self._selected is not None:
            self._set_preview(self._selected)
        self._set_preview_message("Output template updated.")

    def _handle_command_palette(
        self,
        action_id: str | None,
        handlers: dict[str, Callable[[], None]],
    ) -> None:
        if action_id is None:
            return
        handler = handlers.get(action_id)
        if handler is None:
            self._set_preview_message("Command not found.")
            return
        handler()

    def _handle_preset(self, preset: PresetProfile | None) -> None:
        if preset is None:
            return
        self._apply_preset_profile(preset, show_message=True)

    def _handle_tree_root(self, result: Path | None) -> None:
        if result is None:
            return
        if not result.exists() or not result.is_dir():
            self._set_preview_message(f"Invalid root directory:\n{result}")
            return
        self._set_tree_root(result, select_mode="first")

    def _handle_create_entry(self, value: str | None) -> None:
        if value is None:
            return
        path, is_dir = resolve_new_entry(self._tree_root, value)
        if path is None:
            self._set_preview_message("Invalid name for new entry.")
            return
        if path.exists():
            self._set_preview_message(f"Entry already exists:\n{path}")
            return
        try:
            if is_dir:
                path.mkdir(parents=True, exist_ok=False)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(exist_ok=False)
        except OSError as exc:
            self._set_preview_message(f"Failed to create:\n{exc}")
            return
        self._populate_file_buffer()
        self._set_preview_message(f"Created:\n{path}")

    def _handle_rename_entry(self, value: str | None) -> None:
        item = self._selected_file_entry()
        if item is None or value is None:
            return
        new_name = value.strip()
        if not new_name:
            self._set_preview_message("Rename cancelled: empty name.")
            return
        if not is_valid_name(new_name):
            self._set_preview_message("Invalid name (no path separators).")
            return
        target = item.path.with_name(new_name)
        if target.exists():
            self._set_preview_message(f"Target already exists:\n{target}")
            return
        try:
            item.path.rename(target)
        except OSError as exc:
            self._set_preview_message(f"Rename failed:\n{exc}")
            return
        self._populate_file_buffer()
        self._set_preview_message(f"Renamed to:\n{target}")

    def _handle_move_entry(self, value: str | None) -> None:
        item = self._selected_file_entry()
        if item is None or value is None:
            return
        dest = resolve_user_path(self._tree_root, value)
        if dest is None:
            self._set_preview_message("Invalid destination path.")
            return
        if dest.exists() and dest.is_dir():
            dest = dest / item.path.name
        if dest.exists():
            self._set_preview_message(f"Destination exists:\n{dest}")
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.path), str(dest))
        except OSError as exc:
            self._set_preview_message(f"Move failed:\n{exc}")
            return
        self._populate_file_buffer()
        self._set_preview_message(f"Moved to:\n{dest}")

    def _handle_delete_entry(self, confirmed: bool) -> None:
        item = self._selected_file_entry()
        if item is None or not confirmed:
            return
        try:
            if item.path.is_dir():
                shutil.rmtree(item.path)
            else:
                item.path.unlink()
        except OSError as exc:
            self._set_preview_message(f"Delete failed:\n{exc}")
            return
        self._populate_file_buffer()
        self._set_preview_message(f"Deleted:\n{item.path}")

    def _handle_search(self, result: SearchResult | None) -> None:
        if result is None:
            return
        path = result.path
        if not path.exists():
            self._set_preview_message(f"Search result no longer exists:\n{path}")
            return
        self._last_search_query = result.query.strip() or None
        self._set_search_matches(self._last_search_query)
        if self._last_search_query and path in self._last_search_matches:
            self._last_search_index = self._last_search_matches.index(path)
        else:
            self._last_search_index = -1
        if path.is_dir():
            self._jump_to_path(path)
            self._set_preview_message(f"Found folder:\n{path}")
            return
        self._jump_to_path(path)
        if is_clip_file(path):
            self.load_clip_file(path)
            return
        self._set_preview_message(f"Found file:\n{path}")

    def _set_search_matches(self, query: str | None) -> None:
        if not query:
            self._last_search_matches = []
            self._last_search_index = -1
            return
        matches: list[tuple[int, Path]] = []
        query_lower = query.lower()
        for entry in self._file_buffer_original_entries:
            score = _score_search_path(entry.path, self._tree_root, query_lower)
            if score is not None:
                matches.append((score, entry.path))
        matches.sort(key=lambda item: item[0])
        self._last_search_matches = [path for _, path in matches]
        self._last_search_index = -1

    def _file_list_ready(self) -> bool:
        if self._file_buffer is None:
            return False
        if not self._file_buffer.has_focus:
            self._set_preview_message("Focus the file picker to run file actions.")
            return False
        return True

    def _selected_file_entry(self) -> PathEntry | None:
        if not self._file_list_ready():
            return None
        line = self._current_buffer_line()
        if not line.strip():
            self._set_preview_message("No file selected.")
            return None
        if is_delete_marker_line(line):
            self._set_preview_message("Entry is marked for delete.")
            return None
        if self._is_parent_line(line):
            self._set_preview_message("Parent entry is not selectable.")
            return None
        path = self._parse_buffer_path(line)
        if path is None:
            self._set_preview_message("Selected entry is invalid.")
            return None
        if not path.exists():
            self._set_preview_message(f"Entry no longer exists:\n{path}")
            return None
        return PathEntry(path, path.is_dir())

    def _handle_file_normal_key(self, key: str) -> bool:
        buffer = self._file_buffer
        if buffer is None:
            return False
        if self._file_pending:
            if self._file_pending == "g" and key == "g":
                self._file_pending = ""
                self._set_buffer_cursor_line(0)
                return True
            if self._file_pending == "d" and key == "d":
                self._file_pending = ""
                self._delete_current_line()
                return True
            self._file_pending = ""

        if key == "g":
            self._file_pending = "g"
            return True
        if key == "d":
            self._file_pending = "d"
            return True
        if key == "G":
            self._set_buffer_cursor_line(max(0, self._file_buffer_line_count() - 1))
            return True
        if key == "j":
            buffer.action_cursor_down()
            return True
        if key == "k":
            buffer.action_cursor_up()
            return True
        if key == "h":
            self._go_parent_dir()
            return True
        if key == "l":
            self._open_current_buffer_line()
            return True
        if key in {"enter", "return"}:
            self._open_current_buffer_line()
            return True
        if key == "/":
            self.action_search()
            return True
        if key == "?":
            self.action_help()
            return True
        if key == "n":
            self._jump_search_match(True)
            return True
        if key == "N":
            self._jump_search_match(False)
            return True
        if key == "q":
            self._close_file_picker()
            return True
        if key == "i":
            self._set_file_mode(FileBufferMode.INSERT)
            return True
        if key == "a":
            row, col = buffer.cursor_location
            line = self._current_buffer_line()
            target_col = min(col + 1, len(line))
            buffer.move_cursor((row, target_col), record_width=False)
            self._set_file_mode(FileBufferMode.INSERT)
            return True
        if key == "o":
            self._insert_line_below()
            return True
        if key == "O":
            self._insert_line_above()
            return True
        if key == "u":
            buffer.action_undo()
            return True
        if key == "v":
            self._enter_visual_mode(line_mode=False)
            return True
        if key == "V":
            self._enter_visual_mode(line_mode=True)
            return True
        return False

    def _handle_file_visual_key(self, key: str) -> bool:
        buffer = self._file_buffer
        if buffer is None:
            return False
        if self._file_pending:
            if self._file_pending == "g" and key == "g":
                self._file_pending = ""
                self._set_buffer_cursor_line(0)
                self._update_visual_selection()
                return True
            self._file_pending = ""

        if key == "g":
            self._file_pending = "g"
            return True
        if key == "G":
            self._set_buffer_cursor_line(max(0, self._file_buffer_line_count() - 1))
            self._update_visual_selection()
            return True
        if key == "j":
            row, _ = buffer.cursor_location
            self._set_buffer_cursor_line(row + 1)
            self._update_visual_selection()
            return True
        if key == "k":
            row, _ = buffer.cursor_location
            self._set_buffer_cursor_line(row - 1)
            self._update_visual_selection()
            return True
        if key in {"v", "V"}:
            self._exit_visual_mode()
            return True
        if key == "d":
            self._delete_visual_selection()
            return True
        if key == "q":
            self._exit_visual_mode()
            self._close_file_picker()
            return True
        if key == "/":
            self._exit_visual_mode()
            self.action_search()
            return True
        if key == "?":
            self._exit_visual_mode()
            self.action_help()
            return True
        return False

    def _enter_visual_mode(self, *, line_mode: bool) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        row, _ = buffer.cursor_location
        self._file_visual_anchor = buffer.cursor_location
        self._file_visual_cursor = buffer.cursor_location
        self._file_pending = ""
        self._file_visual_start = row
        self._file_visual_end = row
        self._file_visual_line_mode = line_mode
        mode = FileBufferMode.VISUAL_LINE if line_mode else FileBufferMode.VISUAL
        self._set_file_mode(mode)
        self._update_visual_selection()

    def _exit_visual_mode(self) -> None:
        self._file_pending = ""
        self._set_file_mode(FileBufferMode.NORMAL)

    def _clear_visual_selection(self) -> None:
        self._file_visual_start = None
        self._file_visual_end = None
        self._file_visual_line_mode = False
        self._file_visual_anchor = None
        self._file_visual_cursor = None
        if self._file_buffer is not None:
            self._file_buffer.clear_visual_range()

    def _update_visual_selection(self) -> None:
        buffer = self._file_buffer
        if buffer is None or self._file_visual_start is None:
            return
        row, _ = buffer.cursor_location
        self._file_visual_end = row
        self._file_visual_cursor = buffer.cursor_location
        start = min(self._file_visual_start, row)
        end = max(self._file_visual_start, row)
        buffer.set_visual_range(
            start,
            end,
            self._file_visual_line_mode,
            anchor=self._file_visual_anchor,
            cursor=self._file_visual_cursor,
        )

    def _visual_line_range(self) -> tuple[int, int] | None:
        if self._file_visual_start is None:
            return None
        if self._file_visual_end is None:
            if self._file_buffer is None:
                return None
            end = self._file_buffer.cursor_location[0]
        else:
            end = self._file_visual_end
        start = self._file_visual_start
        return (min(start, end), max(start, end))

    def _delete_visual_selection(self) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        if self._drive_picker_active:
            self._update_mode_status("Cannot delete drive entries.")
            self._exit_visual_mode()
            return
        selection = self._visual_line_range()
        if selection is None:
            self._update_mode_status("No selection to delete.")
            self._exit_visual_mode()
            return
        start, end = selection
        lines = self._file_buffer_lines()
        to_delete: list[int] = []
        skipped_parent = False
        for idx in range(start, min(end + 1, len(lines))):
            line = lines[idx]
            if self._is_parent_line(line):
                skipped_parent = True
                continue
            to_delete.append(idx)
        if not to_delete:
            if skipped_parent:
                self._update_mode_status("Cannot delete parent entry.")
            else:
                self._update_mode_status("No entry to delete.")
            self._exit_visual_mode()
            return
        for idx in reversed(to_delete):
            buffer.delete((idx, 0), (idx + 1, 0))
        if buffer.document.line_count == 0:
            buffer.text = ""
            self._exit_visual_mode()
            return
        new_row = min(start, buffer.document.line_count - 1)
        buffer.move_cursor((new_row, 0), record_width=False)
        self._exit_visual_mode()
        if skipped_parent:
            self._update_mode_status("Skipped parent entry.")

    def _strip_buffer_line_for_path(self, line: str) -> str:
        text = line.strip()
        if not text:
            return ""
        if is_delete_marker_line(text):
            text = strip_delete_marker(text).strip()
        text = strip_icon_prefix(text)
        return text.strip()

    def _is_parent_line(self, line: str) -> bool:
        return self._strip_buffer_line_for_path(line) == ".."

    def _clean_buffer_line_for_plan(self, line: str) -> str:
        text = line.strip()
        if not text:
            return ""
        if is_delete_marker_line(text):
            remainder = strip_delete_marker(text).strip()
            remainder = strip_icon_prefix(remainder).strip()
            if not remainder:
                return DELETE_MARKER
            return f"{DELETE_MARKER} {remainder}"
        return strip_icon_prefix(text)

    def _current_buffer_path(self) -> Path | None:
        if self._drive_picker_active:
            return None
        line = self._current_buffer_line()
        if not line.strip():
            return None
        if is_delete_marker_line(line):
            return None
        return self._parse_buffer_path(line)

    def _focus_file_buffer_path(self, path: Path) -> None:
        if self._drive_picker_active:
            return
        lines = self._file_buffer_lines()
        target_key = _path_key(path)
        for idx, line in enumerate(lines):
            line_path = self._parse_buffer_path(line)
            if line_path is None:
                continue
            if _path_key(line_path) == target_key:
                self._set_buffer_cursor_line(idx, column=0)
                return

    def _focus_drive_line(self, drive: Path) -> None:
        lines = self._file_buffer_lines()
        target = str(normalize_drive_path(drive)).casefold()
        for idx, line in enumerate(lines):
            text = self._strip_buffer_line_for_path(line)
            if not text or text == "..":
                continue
            try:
                path = normalize_drive_path(Path(text))
            except OSError:
                continue
            if str(path).casefold() == target:
                self._set_buffer_cursor_line(idx, column=0)
                return

    def _parse_buffer_path(self, line: str) -> Path | None:
        text = self._strip_buffer_line_for_path(line)
        if not text:
            return None
        if text == "..":
            return None
        text = text.rstrip("/\\")
        if not text:
            return None
        path = resolve_user_path(self._tree_root, text)
        if path is None:
            return None
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            return None
        try:
            resolved.relative_to(self._tree_root.resolve())
        except ValueError:
            return None
        return resolved

    def _delete_current_line(self) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        if self._drive_picker_active:
            self._update_mode_status("Cannot delete drive entries.")
            return
        line = self._current_buffer_line()
        if not line.strip():
            self._update_mode_status("No entry to delete.")
            return
        if self._is_parent_line(line):
            self._update_mode_status("Cannot delete parent entry.")
            return
        row, _ = buffer.cursor_location
        buffer.delete((row, 0), (row + 1, 0))
        if buffer.document.line_count == 0:
            buffer.text = ""
            return
        new_row = min(row, buffer.document.line_count - 1)
        buffer.move_cursor((new_row, 0), record_width=False)

    def _insert_line_below(self) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        row, _ = buffer.cursor_location
        line = self._current_buffer_line()
        buffer.insert("\n", location=(row, len(line)))
        buffer.move_cursor((row + 1, 0), record_width=False)
        self._set_file_mode(FileBufferMode.INSERT)

    def _insert_line_above(self) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        row, _ = buffer.cursor_location
        buffer.insert("\n", location=(row, 0))
        buffer.move_cursor((row, 0), record_width=False)
        self._set_file_mode(FileBufferMode.INSERT)

    def _enter_command_mode(self) -> None:
        command = self._file_command
        if command is None:
            return
        self._set_file_mode(FileBufferMode.COMMAND)
        command.remove_class("hidden")
        command.value = ":"
        command.cursor_position = len(command.value)
        command.focus()

    def _exit_command_mode(self) -> None:
        command = self._file_command
        if command is None:
            return
        command.add_class("hidden")
        command.value = ""
        self._set_file_mode(FileBufferMode.NORMAL)
        if self._file_buffer is not None:
            self._file_buffer.focus()

    def _set_file_mode(self, mode: FileBufferMode) -> None:
        buffer = self._file_buffer
        if mode not in {FileBufferMode.VISUAL, FileBufferMode.VISUAL_LINE}:
            self._clear_visual_selection()
        self._file_mode = mode
        if buffer is not None:
            buffer.read_only = mode != FileBufferMode.INSERT
            buffer.remove_class("mode-normal", "mode-insert")
            if mode == FileBufferMode.INSERT:
                buffer.add_class("mode-insert")
                buffer.set_cursor_mode("insert")
                buffer.cursor_blink = True
            else:
                buffer.add_class("mode-normal")
                buffer.set_cursor_mode("normal")
                if mode in {FileBufferMode.VISUAL, FileBufferMode.VISUAL_LINE}:
                    buffer.cursor_blink = False
                else:
                    buffer.cursor_blink = True
        self._update_mode_status()

    def _handle_file_command(self, command: str) -> None:
        text = command.strip()
        if not text:
            self._exit_command_mode()
            return
        if text.startswith(":"):
            text = text.lstrip(":").strip()
        if text in {"w", "write"}:
            self._exit_command_mode()
            self._apply_file_buffer(exit_after=False)
            return
        if text in {"wq", "x"}:
            self._exit_command_mode()
            self._apply_file_buffer(exit_after=True)
            return
        if text == "q":
            if self._file_buffer_dirty():
                self._exit_command_mode()
                self._update_mode_status("Unsaved changes. Use :q! to discard.")
                return
            self._exit_command_mode()
            return
        if text == "q!":
            self._populate_file_buffer(select_mode="keep")
            self._exit_command_mode()
            return
        self._exit_command_mode()
        self._update_mode_status(f"Unknown command: :{text}")

    def _apply_file_buffer(self, *, exit_after: bool) -> None:
        buffer = self._file_buffer
        if buffer is None:
            return
        if self._drive_picker_active:
            self._update_mode_status("Select a drive before applying changes.")
            return
        lines = [
            self._clean_buffer_line_for_plan(line)
            for line in buffer.text.splitlines()
            if not self._is_parent_line(line)
        ]
        plan = compute_plan(self._tree_root, self._file_buffer_original_entries, lines)
        errors = validate_plan(plan)
        if errors:
            first = errors[0]
            message = first.message
            if first.path:
                message = f"{message} ({first.path})"
            self._update_mode_status(message)
            return
        if not plan.operations:
            self._populate_file_buffer(select_mode="keep")
            self._update_mode_status("No changes to apply.")
            if exit_after:
                self._close_file_picker()
            return
        confirmations = collect_confirmations(plan)
        self.call_later(
            self.push_screen,
            PlanPreviewScreen(self._tree_root, plan, confirmations),
            lambda confirmed: self._apply_plan_confirmed(plan, confirmed, exit_after),
        )

    def _apply_plan_confirmed(
        self, plan: OperationPlan, confirmed: bool | None, exit_after: bool
    ) -> None:
        if not confirmed:
            self._update_mode_status("Apply canceled.")
            self._populate_file_buffer(select_mode="keep")
            return
        report = apply_plan(plan)
        self._update_mode_status(
            f"Applied: {report.ok_count} ok, "
            f"{report.skipped_count} skipped, "
            f"{report.error_count} failed."
        )
        self._populate_file_buffer(select_mode="keep")
        if exit_after:
            self._close_file_picker()

    def _clip_list_items(self) -> list[ClipListItem]:
        if self._clip_list is None:
            return []
        return [child for child in self._clip_list.children if isinstance(child, ClipListItem)]

    def _current_clip_list_widget(self) -> ListItem | None:
        if self._clip_list is None:
            return None
        children = list(self._clip_list.children)
        if not children:
            return None
        index = self._clip_list.index
        if index is None or index < 0 or index >= len(children):
            return None
        widget = children[index]
        if isinstance(widget, (ClipListItem, ClipGroupItem)):
            return widget
        return None

    def _current_clip_list_item(self) -> ClipListItem | None:
        widget = self._current_clip_list_widget()
        if isinstance(widget, ClipListItem):
            return widget
        items = self._clip_list_items()
        if not items:
            return None
        index = max(0, min(self._clip_list_index, len(items) - 1))
        return items[index]

    def _render_clip_list(
        self,
        selected_index: int | None = None,
        *,
        highlight_clip: ResolvedClip | None = None,
        highlight_video: str | None = None,
    ) -> None:
        list_view = self._clip_list
        if list_view is None:
            return
        if highlight_clip is None and selected_index is not None and self._clips:
            highlight_clip = self._clips[selected_index]
        if highlight_clip is not None:
            self._collapsed_groups.discard(highlight_clip.video_id)
        items: list[ListItem] = []
        highlight_index: int | None = None
        for group in self._clip_groups:
            collapsed = group.video_id in self._collapsed_groups
            metadata = self._metadata_cache.get(group.video_id)
            title = _group_title(metadata)
            if group.clips:
                self._ensure_metadata(group.clips[0])
            group_item = ClipGroupItem(group, collapsed, title)
            items.append(group_item)
            if highlight_video == group.video_id and highlight_clip is None:
                highlight_index = len(items) - 1
            if collapsed:
                continue
            for clip in group.clips:
                warning = bool(self._overlap_index.get(clip))
                item = ClipListItem(
                    clip,
                    selected=clip in self._clip_selection,
                    warning=warning,
                )
                items.append(item)
                if highlight_clip == clip:
                    highlight_index = len(items) - 1
        if highlight_index is None:
            for idx, child in enumerate(items):
                if isinstance(child, ClipListItem):
                    highlight_index = idx
                    break
            if highlight_index is None and items:
                highlight_index = 0
        list_view.clear()
        if items:
            list_view.extend(items)
            if highlight_index is not None:
                list_view.index = highlight_index
        else:
            list_view.index = None
        if highlight_clip is not None:
            try:
                self._clip_list_index = self._clips.index(highlight_clip)
            except ValueError:
                pass
        elif highlight_index is not None and 0 <= highlight_index < len(items):
            child = items[highlight_index]
            if isinstance(child, ClipListItem):
                try:
                    self._clip_list_index = self._clips.index(child.resolved)
                except ValueError:
                    pass

    def _toggle_clip_group(self, video_id: str) -> None:
        if video_id in self._collapsed_groups:
            self._collapsed_groups.remove(video_id)
        else:
            self._collapsed_groups.add(video_id)
        current = self._current_clip_list_item()
        highlight_clip = current.resolved if current is not None else None
        self._render_clip_list(highlight_clip=highlight_clip, highlight_video=video_id)

    def _refresh_group_label(self, video_id: str) -> None:
        if self._clip_list is None:
            return
        title = _group_title(self._metadata_cache.get(video_id))
        for child in self._clip_list.children:
            if isinstance(child, ClipGroupItem) and child.video_id == video_id:
                child.set_title(title)
                current = self._current_clip_list_widget()
                if current is child:
                    self._set_preview_message(_format_group_preview(child.group, title))
                return

    def _current_clip_or_group_video(self) -> str | None:
        widget = self._current_clip_list_widget()
        if isinstance(widget, ClipGroupItem):
            return widget.video_id
        if isinstance(widget, ClipListItem):
            return widget.resolved.video_id
        if self._selected is not None:
            return self._selected.video_id
        return None

    def _current_queue_or_clip_video(self) -> str | None:
        if self._queue_list is not None and self._queue_list.has_focus:
            item = self._current_queue_list_item()
            if item is not None:
                return item.item.resolved.video_id
        return self._current_clip_or_group_video()

    def _toggle_clip_selection(self, item: ClipListItem) -> None:
        clip = item.resolved
        if clip in self._clip_selection:
            self._clip_selection.remove(clip)
            item.set_selected(False)
        else:
            self._clip_selection.add(clip)
            item.set_selected(True)

    def _queue_list_items(self) -> list[QueueListItem]:
        if self._queue_list is None:
            return []
        return [child for child in self._queue_list.children if isinstance(child, QueueListItem)]

    def _current_queue_list_item(self) -> QueueListItem | None:
        items = self._queue_list_items()
        if not items:
            return None
        index = max(0, min(self._queue_list_index, len(items) - 1))
        return items[index]

    def _toggle_queue_selection(self, item: QueueListItem) -> None:
        item_id = id(item.item)
        if item_id in self._queue_selection:
            self._queue_selection.remove(item_id)
            item.set_selected(False)
        else:
            self._queue_selection.add(item_id)
            item.set_selected(True)

    def _queue_action_items(self) -> list[QueueItem]:
        if self._queue_selection:
            return [item for item in self._queue_items if id(item) in self._queue_selection]
        current = self._current_queue_list_item()
        if current is None:
            return []
        return [current.item]

    def _rebuild_queue_list(self, *, highlight_item: QueueItem | None = None) -> None:
        queue_list = self._queue_list
        if queue_list is None:
            return
        selected_ids = set(self._queue_selection)
        queue_list.clear()
        self._queue_widgets.clear()
        highlight_index: int | None = None
        for idx, item in enumerate(self._queue_items):
            widget = QueueListItem(item, selected=id(item) in selected_ids)
            self._queue_widgets[id(item)] = widget
            queue_list.append(widget)
            if highlight_item is item:
                highlight_index = idx
        if highlight_index is None and self._queue_items:
            highlight_index = min(self._queue_list_index, len(self._queue_items) - 1)
        if highlight_index is not None:
            queue_list.index = highlight_index
            self._queue_list_index = highlight_index

    def _move_queue_items(self, delta: int) -> None:
        items = self._queue_action_items()
        if not items:
            self._set_preview_message("No queue item selected.")
            return
        indices = [self._queue_items.index(item) for item in items]
        if delta > 0:
            indices.sort(reverse=True)
        else:
            indices.sort()
        for index in indices:
            new_index = index + delta
            if new_index < 0 or new_index >= len(self._queue_items):
                continue
            item = self._queue_items.pop(index)
            self._queue_items.insert(new_index, item)
        self._rebuild_queue_list(highlight_item=items[0])

    def _reset_queue_metrics(
        self, item: QueueItem, *, clear_error: bool = True, clear_output: bool = True
    ) -> None:
        if clear_error:
            item.error = None
        if clear_output:
            item.output_path = None
        item.progress = None
        item.speed_bps = None
        item.eta_seconds = None

    def _prepare_queue_item_for_restart(self, item: QueueItem) -> None:
        item.status = DownloadStatus.QUEUED
        item.pause_requested = False
        item.cancel_requested = False
        item.cancel_event.clear()
        self._reset_queue_metrics(item, clear_error=True, clear_output=True)
        widget = self._queue_widgets.get(id(item))
        if widget is not None:
            widget.refresh_label()

    def _ensure_download_workers(self) -> None:
        if self._download_workers_started:
            return
        self._download_queue = queue.Queue()
        for _ in range(self._max_parallel_downloads):
            threading.Thread(target=self._download_worker, daemon=True).start()
        self._download_workers_started = True

    def _download_worker(self) -> None:
        if self._download_queue is None:
            return
        while True:
            item = self._download_queue.get()
            self._download_pending.discard(id(item))
            if (
                item.status != DownloadStatus.QUEUED
                or item.pause_requested
                or item.cancel_requested
                or item.cancel_event.is_set()
            ):
                self._download_queue.task_done()
                continue
            self._download_queue_item(item)
            self._download_queue.task_done()

    def _start_queue_item(self, item: QueueItem) -> None:
        if item.status != DownloadStatus.QUEUED:
            return
        item_id = id(item)
        if item_id in self._download_pending:
            return
        self._ensure_download_workers()
        if self._download_queue is None:
            return
        self._download_pending.add(item_id)
        self._download_queue.put(item)

    def _pause_queue_item(self, item: QueueItem) -> None:
        if item.status not in {DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING}:
            return
        item.pause_requested = True
        item.cancel_requested = False
        item.cancel_event.set()
        item.status = DownloadStatus.PAUSED
        item.error = None
        item.speed_bps = None
        item.eta_seconds = None
        widget = self._queue_widgets.get(id(item))
        if widget is not None:
            widget.refresh_label()
        self._refresh_preview_if_selected(item)

    def _toggle_queue_pause(self) -> None:
        items = self._queue_action_items()
        if not items:
            self._set_preview_message("No queue item selected.")
            return
        for item in items:
            if item.pause_requested or item.cancel_requested:
                continue
            if item.status == DownloadStatus.PAUSED:
                self._prepare_queue_item_for_restart(item)
                self._start_queue_item(item)
            else:
                self._pause_queue_item(item)

    def _cancel_queue_item(self, item: QueueItem) -> None:
        if item.status in {DownloadStatus.DONE, DownloadStatus.FAILED, DownloadStatus.CANCELED}:
            return
        item.cancel_requested = True
        item.pause_requested = False
        item.cancel_event.set()
        if item.status in {DownloadStatus.QUEUED, DownloadStatus.PAUSED}:
            item.status = DownloadStatus.CANCELED
            item.error = "Canceled"
            self._reset_queue_metrics(item, clear_error=False, clear_output=False)
            widget = self._queue_widgets.get(id(item))
            if widget is not None:
                widget.refresh_label()
            self._refresh_preview_if_selected(item)

    def _cancel_queue_items(self) -> None:
        items = self._queue_action_items()
        if not items:
            self._set_preview_message("No queue item selected.")
            return
        for item in items:
            self._cancel_queue_item(item)

    def _download_selected_clips(self) -> None:
        if self._clip_list is None:
            return
        if self._clip_selection:
            clips = [clip for clip in self._clips if clip in self._clip_selection]
        else:
            item = self._current_clip_list_item()
            if item is None:
                self._set_preview_message("No clip selected.")
                return
            clips = [item.resolved]
        self._enqueue_clips(clips)

    def _enqueue_clips(self, clips: list[ResolvedClip]) -> None:
        if not clips:
            self._set_preview_message("No clip selected.")
            return
        for clip in clips:
            self._enqueue_clip(clip)

    def _enqueue_clip(self, clip: ResolvedClip) -> None:
        queue_list = self._queue_list
        if queue_list is None:
            return
        metadata = self._metadata_cache.get(clip.video_id)
        title = metadata.title if metadata and metadata.title else None
        output_name = self._output_basename_for_clip(clip, title)
        item = QueueItem(resolved=clip, output_name=output_name, output_format=self.output_format)
        self._queue_items.append(item)
        widget = QueueListItem(item, selected=False)
        self._queue_widgets[id(item)] = widget
        queue_list.append(widget)
        self._start_queue_item(item)

    def _jump_search_match(self, forward: bool) -> None:
        if not self._last_search_matches:
            self._set_preview_message("No search matches.")
            return
        if self._last_search_index < 0:
            self._last_search_index = 0 if forward else len(self._last_search_matches) - 1
        else:
            delta = 1 if forward else -1
            self._last_search_index = (
                self._last_search_index + delta
            ) % len(self._last_search_matches)
        target = self._last_search_matches[self._last_search_index]
        self._focus_file_buffer_path(target)

    def _open_current_buffer_line(self) -> None:
        if self._drive_picker_active:
            self._open_drive_picker_line()
            return
        line = self._current_buffer_line()
        if not line.strip():
            self._set_preview_message("No entry selected.")
            return
        if is_delete_marker_line(line):
            self._set_preview_message("Entry is marked for delete.")
            return
        if self._is_parent_line(line):
            self._go_parent_dir()
            return
        path = self._parse_buffer_path(line)
        if path is None:
            self._set_preview_message("Selected entry is invalid.")
            return
        if not path.exists():
            self._set_preview_message(f"Entry no longer exists:\n{path}")
            return
        if path.is_dir():
            self._set_tree_root(path, select_mode="first")
            return
        if not is_clip_file(path):
            self._set_preview_message(f"Not a clip file:\n{path}")
            return
        self.load_clip_file(path)

    def _open_drive_picker_line(self) -> None:
        line = self._current_buffer_line()
        if not line.strip():
            self._set_preview_message("No drive selected.")
            return
        if self._is_parent_line(line):
            self._exit_drive_picker()
            return
        text = self._strip_buffer_line_for_path(line)
        if not text:
            self._set_preview_message("Selected drive is invalid.")
            return
        try:
            drive = normalize_drive_path(Path(text))
        except OSError:
            self._set_preview_message("Selected drive is invalid.")
            return
        if not drive.exists() or not drive.is_dir():
            self._set_preview_message(f"Drive not available:\n{drive}")
            return
        self._set_tree_root(drive, select_mode="first")

    def _enter_drive_picker(self) -> None:
        self._drive_picker_active = True
        self._drive_picker_return_root = self._tree_root
        self._populate_file_buffer(select_mode="first")

    def _exit_drive_picker(self) -> None:
        target = self._drive_picker_return_root or self._tree_root
        self._set_tree_root(target, select_mode="first")

    def _go_parent_dir(self) -> None:
        if self._drive_picker_active:
            self._exit_drive_picker()
            return
        parent = self._tree_root.parent
        if parent == self._tree_root:
            self._enter_drive_picker()
            return
        self._set_tree_root(parent, select_mode="up", focus_path=self._tree_root)

    def _close_file_picker(self) -> None:
        if self._clip_list is not None:
            self._clip_list.focus()

    def _output_dir(self) -> Path:
        if self.output_dir:
            return self.output_dir
        if self.clip_path:
            return self.clip_path.parent
        return Path.cwd()

    def _load_clip_on_startup(self) -> None:
        if self.clip_path and self.clip_path.is_file():
            self.load_clip_file(self.clip_path)

    def _ensure_player_command(self) -> str | None:
        if self._player_command is None:
            self._player_command = shutil.which("mpv") or shutil.which("vlc")
        return self._player_command

    def _ensure_chafa_path(self) -> str | None:
        if self._chafa_path is None:
            self._chafa_path = shutil.which("chafa")
        return self._chafa_path

    def _export_target_clips(self) -> tuple[list[ResolvedClip], str]:
        if not self._clips:
            self._set_preview_message("No clips loaded.")
            return ([], "none")
        if self._clip_selection:
            clips = [clip for clip in self._clips if clip in self._clip_selection]
            if not clips:
                self._set_preview_message("No selected clips found.")
                return ([], "none")
            return (clips, "selected")
        return (list(self._clips), "all")

    def _default_export_basename(self, suffix: str) -> str:
        if self.clip_path:
            stem = self.clip_path.stem.strip()
            if stem:
                suffix_lower = suffix.lower()
                if stem.lower().endswith(f"_{suffix_lower}"):
                    return stem
                return f"{stem}_{suffix}"
        return suffix

    def _unique_manifest_basename(self, output_dir: Path, base_name: str) -> str:
        candidate = base_name
        for index in range(1000):
            csv_path = output_dir / f"{candidate}.csv"
            json_path = output_dir / f"{candidate}.json"
            if not csv_path.exists() and not json_path.exists():
                return candidate
            candidate = f"{base_name}_{index + 1}"
        return base_name

    def _unique_export_path(self, path: Path, *, is_dir: bool) -> Path:
        if not path.exists():
            return path
        stem = path.name if is_dir else path.stem
        suffix = "" if is_dir else path.suffix
        for index in range(1, 1000):
            candidate = path.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                return candidate
        return path

    def _unique_child_path(self, root: Path, filename: str) -> Path:
        path = root / filename
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        for index in range(1, 1000):
            candidate = root / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
        return path

    def _resolve_action_target(self) -> tuple[ResolvedClip, QueueItem | None] | None:
        if self._queue_list is not None and self._queue_list.has_focus:
            queue_widget = self._current_queue_list_item()
            if queue_widget is not None:
                return (queue_widget.item.resolved, queue_widget.item)
        item = self._current_clip_list_item()
        if item is not None:
            clip = item.resolved
            return (clip, self._latest_queue_item_for_clip(clip))
        if self._selected is not None:
            return (self._selected, self._latest_queue_item_for_clip(self._selected))
        return None

    def _resolve_output_path(
        self, clip: ResolvedClip, queue_item: QueueItem | None
    ) -> Path | None:
        if queue_item is not None and queue_item.output_path:
            return queue_item.output_path
        output_format = queue_item.output_format if queue_item else self.output_format
        if queue_item is not None:
            output_name = queue_item.output_name
        else:
            metadata = self._metadata_cache.get(clip.video_id)
            title = metadata.title if metadata and metadata.title else None
            output_name = self._output_basename_for_clip(clip, title)
        ext = output_format.lower().lstrip(".")
        return self._output_dir() / f"{output_name}.{ext}"

    def _command_palette_entries(self) -> list[tuple[CommandAction, Callable[[], None]]]:
        entries: list[tuple[CommandAction, Callable[[], None]]] = [
            (
                CommandAction(
                    "download_selected",
                    "Download selected clips",
                    "Queue selected or current clip",
                    "download queue",
                ),
                self.action_download,
            ),
            (
                CommandAction(
                    "download_all",
                    "Download all clips",
                    "Queue every clip in file",
                    "download queue",
                ),
                self.action_download_all,
            ),
            (
                CommandAction(
                    "download_failed",
                    "Download failed clips",
                    "Queue failed for selection",
                    "download failed",
                ),
                self.action_download_failed,
            ),
            (
                CommandAction(
                    "retry_failed",
                    "Retry failed downloads",
                    "Restart failed queue items",
                    "retry failed",
                ),
                self.action_retry_failed,
            ),
            (
                CommandAction(
                    "retry_failed_video",
                    "Retry failed for current video",
                    "Restart failed for current video",
                    "retry failed",
                ),
                self.action_retry_failed_video,
            ),
            (
                CommandAction(
                    "open_output",
                    "Open output in player",
                    "Open downloaded clip via mpv/vlc",
                    "player mpv vlc",
                ),
                self.action_open_in_player,
            ),
            (
                CommandAction(
                    "open_youtube",
                    "Open YouTube at clip start",
                    "Open in browser",
                    "youtube browser",
                ),
                self.action_open_youtube,
            ),
            (
                CommandAction(
                    "export_manifest",
                    "Export manifest (CSV/JSON)",
                    "Write clip list to output dir",
                    "export manifest csv json",
                ),
                self.action_export_manifest,
            ),
            (
                CommandAction(
                    "export_concat",
                    "Export concat list",
                    "ffmpeg concat file in output dir",
                    "concat ffmpeg",
                ),
                self.action_export_concat,
            ),
            (
                CommandAction(
                    "rally_pack",
                    "Create rally pack",
                    "Copy outputs + concat list",
                    "rally pack",
                ),
                self.action_rally_pack,
            ),
            (
                CommandAction(
                    "output_dir",
                    "Set output directory",
                    "Change download folder",
                    "output directory",
                ),
                self.action_output_dir,
            ),
            (
                CommandAction(
                    "output_format",
                    "Set output format",
                    "mp4/mkv/webm",
                    "output format",
                ),
                self.action_output_format,
            ),
            (
                CommandAction(
                    "output_template",
                    "Set output template",
                    "Customize filenames",
                    "output template",
                ),
                self.action_output_template,
            ),
            (
                CommandAction(
                    "preset",
                    "Load preset profile",
                    "Apply pad/format/template defaults",
                    "preset profile",
                ),
                self.action_preset,
            ),
            (
                CommandAction(
                    "pad_global",
                    "Set global pad",
                    "Defaults for all clips",
                    "pad",
                ),
                self.action_pad_global,
            ),
            (
                CommandAction(
                    "pad_video",
                    "Set pad for current video",
                    "Apply to current video",
                    "pad",
                ),
                self.action_pad_video,
            ),
            (
                CommandAction(
                    "pad_selected",
                    "Set pad for selected clips",
                    "Apply to selected clips",
                    "pad",
                ),
                self.action_pad_selected,
            ),
            (
                CommandAction(
                    "normalize_pads",
                    "Normalize pad overrides",
                    "Clear overrides matching defaults",
                    "pad normalize",
                ),
                self.action_normalize_pads,
            ),
            (
                CommandAction(
                    "merge_adjacent",
                    "Merge adjacent clips",
                    "Merge when close in time",
                    "merge",
                ),
                self.action_merge_adjacent,
            ),
            (
                CommandAction(
                    "toggle_tag_prefix",
                    "Toggle auto-tag prefix",
                    "Prefix tags by video",
                    "tag",
                ),
                self.action_toggle_tag_prefix,
            ),
            (
                CommandAction(
                    "search",
                    "Search files",
                    "Fuzzy search in file tree",
                    "search files",
                ),
                self.action_search,
            ),
            (
                CommandAction(
                    "reload",
                    "Reload clip file",
                    "Re-parse current clip file",
                    "reload",
                ),
                self.action_reload,
            ),
            (
                CommandAction(
                    "help",
                    "Show help",
                    "Open help screen",
                    "help",
                ),
                self.action_help,
            ),
            (
                CommandAction(
                    "quit",
                    "Quit",
                    "Exit the application",
                    "quit exit",
                ),
                self.exit,
            ),
        ]
        return entries

    def _resolve_preset_output_dir(self, target: Path) -> Path:
        target = Path(target).expanduser()
        if target.is_absolute():
            return target
        base = self.clip_path.parent if self.clip_path else Path.cwd()
        return base / target

    def _output_basename_for_clip(self, clip: ResolvedClip, title: str | None) -> str:
        try:
            return format_output_basename(self.output_template, clip, title=title)
        except ValueError:
            return clip.output_name

    def _apply_preset_profile(self, preset: PresetProfile, *, show_message: bool) -> None:
        pad_changed = False
        errors: list[str] = []
        if preset.pad_before is not None:
            self._pad_before_default = preset.pad_before
            pad_changed = True
        if preset.pad_after is not None:
            self._pad_after_default = preset.pad_after
            pad_changed = True
        if preset.output_format:
            normalized = _normalize_output_format(preset.output_format)
            if _is_valid_output_format(normalized):
                self.output_format = normalized
                self._output_format_override = True
            else:
                errors.append(f"Invalid format: {preset.output_format}")
        if preset.output_template:
            try:
                validate_output_template(preset.output_template)
                self.output_template = preset.output_template
                self._output_template_override = True
            except ValueError as exc:
                errors.append(str(exc))
        if preset.output_dir:
            try:
                resolved_dir = self._resolve_preset_output_dir(preset.output_dir)
                resolved_dir.mkdir(parents=True, exist_ok=True)
                self.output_dir = resolved_dir
                self._output_dir_override = True
            except OSError as exc:
                errors.append(f"Output dir error: {exc}")

        self._update_left_status()
        if pad_changed and self.clip_path:
            self.load_clip_file(self.clip_path, select_index=self._clip_list_index)
        elif self._selected is not None:
            self._set_preview(self._selected)
        if show_message:
            message = f"Preset applied: {preset.name}"
            if errors:
                message = f"{message}\n" + "\n".join(errors)
            self._set_preview_message(message)

    def _latest_queue_item_for_clip(self, clip: ResolvedClip) -> QueueItem | None:
        for item in reversed(self._queue_items):
            if item.resolved == clip:
                return item
        return None

    def _refresh_preview_text(self, clip: ResolvedClip, item: QueueItem | None) -> None:
        if self._preview_text is None:
            return
        metadata = self._metadata_cache.get(clip.video_id)
        metadata_error = self._metadata_errors.get(clip.video_id)
        warnings = self._overlap_index.get(clip, [])
        merges = self._merge_index.get(clip, [])
        title = metadata.title if metadata and metadata.title else None
        output_name = item.output_name if item else self._output_basename_for_clip(clip, title)
        self._preview_text.update(
            _format_preview(
                clip,
                item,
                output_name,
                self.output_format,
                metadata,
                metadata_error,
                warnings,
                merges,
            )
        )

    def _ensure_metadata(self, clip: ResolvedClip) -> None:
        video_id = clip.video_id
        if video_id in self._metadata_cache or video_id in self._metadata_loading:
            return
        self._metadata_loading.add(video_id)
        self._ensure_metadata_worker()
        if self._metadata_queue is None:
            return
        self._metadata_queue.put((video_id, clip.clip.start_url))

    def _ensure_metadata_worker(self) -> None:
        if self._metadata_worker_started:
            return
        self._metadata_queue = queue.Queue()
        threading.Thread(target=self._metadata_worker, daemon=True).start()
        self._metadata_worker_started = True

    def _metadata_worker(self) -> None:
        if self._metadata_queue is None:
            return
        while True:
            video_id, start_url = self._metadata_queue.get()
            try:
                metadata = get_metadata(start_url)
                self.call_from_thread(self._apply_metadata, video_id, metadata, None)
            except Exception as exc:
                self.call_from_thread(self._apply_metadata, video_id, None, str(exc))
            self._metadata_queue.task_done()

    def _apply_metadata(
        self,
        video_id: str,
        metadata: VideoMetadata | None,
        error: str | None,
    ) -> None:
        self._metadata_loading.discard(video_id)
        if metadata is not None:
            self._metadata_cache[video_id] = metadata
            self._metadata_errors.pop(video_id, None)
            self._ensure_video_thumbnail(video_id, metadata.thumbnail_url)
        if error:
            self._metadata_errors[video_id] = error
        self._refresh_group_label(video_id)
        if self._selected_group_video_id == video_id:
            group = next(
                (entry for entry in self._clip_groups if entry.video_id == video_id),
                None,
            )
            title = _group_title(metadata)
            if group is not None:
                self._set_preview_message(_format_group_preview(group, title))
            self._show_group_thumbnail(video_id)
        if self._selected is None or self._selected.video_id != video_id:
            return
        queue_item = self._latest_queue_item_for_clip(self._selected)
        self._refresh_preview_text(self._selected, queue_item)
        self._refresh_thumbnail(self._selected)

    def _ensure_video_thumbnail(self, video_id: str, thumbnail_url: str | None) -> None:
        if not thumbnail_url:
            return
        cached = self._video_thumb_cache.get(video_id)
        if cached is not None and not cached.exists():
            self._video_thumb_cache.pop(video_id, None)
        if (
            video_id in self._video_thumb_cache
            or video_id in self._video_thumb_loading
            or video_id in self._video_thumb_errors
        ):
            return
        self._video_thumb_loading.add(video_id)
        self._ensure_video_thumb_worker()
        if self._video_thumb_queue is None:
            return
        self._video_thumb_queue.put((video_id, thumbnail_url))

    def _ensure_video_thumb_worker(self) -> None:
        if self._video_thumb_worker_started:
            return
        self._video_thumb_queue = queue.LifoQueue()
        threading.Thread(target=self._video_thumb_worker, daemon=True).start()
        self._video_thumb_worker_started = True

    def _video_thumb_worker(self) -> None:
        if self._video_thumb_queue is None:
            return
        while True:
            video_id, url = self._video_thumb_queue.get()
            try:
                path = download_thumbnail(url, video_id)
                self.call_from_thread(self._apply_video_thumbnail, video_id, path, None)
            except Exception as exc:
                self.call_from_thread(self._apply_video_thumbnail, video_id, None, str(exc))
            self._video_thumb_queue.task_done()

    def _apply_video_thumbnail(
        self, video_id: str, path: Path | None, error: str | None
    ) -> None:
        self._video_thumb_loading.discard(video_id)
        if path is not None:
            self._video_thumb_cache[video_id] = path
            self._video_thumb_errors.pop(video_id, None)
        if error:
            self._video_thumb_errors[video_id] = error
        self._refresh_group_thumbnail(video_id)

    def _refresh_group_thumbnail(self, video_id: str) -> None:
        if self._selected_group_video_id != video_id:
            return
        self._show_group_thumbnail(video_id)

    def _show_group_thumbnail(self, video_id: str) -> None:
        if self._thumb_image is None or self._thumb_fallback is None:
            return
        thumb_error = self._video_thumb_errors.get(video_id)
        if thumb_error:
            self._show_thumbnail_message(f"Thumbnail error: {_short_error(thumb_error)}")
            return
        thumb_path = self._video_thumb_cache.get(video_id)
        if thumb_path is not None and thumb_path.exists():
            try:
                _update_image_widget(self._thumb_image, thumb_path)
            except Exception as exc:
                message = str(exc)
                self._video_thumb_errors[video_id] = message
                self._show_thumbnail_message(f"Thumbnail error: {_short_error(message)}")
                return
            self._thumb_image.remove_class("hidden")
            self._thumb_fallback.add_class("hidden")
            return
        metadata = self._metadata_cache.get(video_id)
        if metadata is not None:
            self._ensure_video_thumbnail(video_id, metadata.thumbnail_url)
            if metadata.thumbnail_url:
                self._show_thumbnail_message("Thumbnail: loading...")
            else:
                self._show_thumbnail_message("Thumbnail: --")
        else:
            self._show_thumbnail_message("Thumbnail: --")

    def _refresh_thumbnail(self, clip: ResolvedClip) -> None:
        if self._thumb_image is None or self._thumb_fallback is None:
            return
        key = _thumb_key(clip)
        thumb_error = self._thumb_errors.get(key)
        if thumb_error:
            self._show_thumbnail_message(f"Thumbnail error: {_short_error(thumb_error)}")
            return
        thumb_path = self._thumb_cache.get(key)
        if thumb_path is not None and thumb_path.exists():
            if self._show_thumbnail_image(key, thumb_path):
                return
            chafa_path = self._ensure_chafa_path()
            if chafa_path and self._show_chafa_thumbnail(key, thumb_path):
                return
            if chafa_path:
                self._show_thumbnail_message("Thumbnail: unavailable (chafa failed)")
            else:
                self._show_thumbnail_message("Thumbnail: unavailable (install chafa)")
            return
        if key not in self._thumb_loading:
            self._show_thumbnail_message("Thumbnail: generating...")
            self._ensure_thumbnail(clip)
        else:
            self._show_thumbnail_message("Thumbnail: generating...")

    def _ensure_thumbnail(self, clip: ResolvedClip) -> None:
        key = _thumb_key(clip)
        if key in self._thumb_cache or key in self._thumb_loading:
            return
        self._thumb_loading.add(key)
        self._ensure_thumb_worker()
        if self._thumb_queue is None:
            return
        self._thumb_queue.put((key, clip.clip.start_url, clip.video_id, clip.start_sec))

    def _ensure_thumb_worker(self) -> None:
        if self._thumb_worker_started:
            return
        self._thumb_queue = queue.LifoQueue()
        threading.Thread(target=self._thumb_worker, daemon=True).start()
        self._thumb_worker_started = True

    def _thumb_worker(self) -> None:
        if self._thumb_queue is None:
            return
        while True:
            key, url, video_id, start_sec = self._thumb_queue.get()
            try:
                path = generate_clip_thumbnail(url, video_id, start_sec)
                self.call_from_thread(self._apply_thumbnail, key, path, None)
            except Exception as exc:
                self.call_from_thread(self._apply_thumbnail, key, None, str(exc))
            self._thumb_queue.task_done()

    def _apply_thumbnail(
        self, key: tuple[str, str], path: Path | None, error: str | None
    ) -> None:
        self._thumb_loading.discard(key)
        if path is not None:
            self._thumb_cache[key] = path
            self._thumb_errors.pop(key, None)
        if error:
            self._thumb_errors[key] = error
        if self._selected is not None and _thumb_key(self._selected) == key:
            self._refresh_thumbnail(self._selected)

    def _set_tree_root(
        self,
        target: Path,
        *,
        select_mode: str = "keep",
        focus_path: Path | None = None,
    ) -> None:
        self._tree_root = target
        self._drive_picker_active = False
        self._drive_picker_return_root = None
        self._file_pending = ""
        if self._file_buffer is not None:
            self._file_buffer.set_root(target)
        self._update_left_status()
        self._populate_file_buffer(select_mode=select_mode, focus_path=focus_path)

    def _jump_to_path(self, target: Path) -> None:
        if target.is_file():
            self._set_tree_root(
                target.parent,
                select_mode="path",
                focus_path=target,
            )
            return
        self._set_tree_root(target, select_mode="first")

    def _show_thumbnail_message(self, message: str) -> None:
        if self._thumb_fallback is None or self._thumb_image is None:
            return
        self._thumb_fallback.update(message)
        self._thumb_fallback.remove_class("hidden")
        self._thumb_image.add_class("hidden")

    def _show_thumbnail_image(self, key: tuple[str, str], path: Path) -> bool:
        if self._thumb_image is None or self._thumb_fallback is None:
            return False
        try:
            _update_image_widget(self._thumb_image, path)
        except Exception as exc:
            self._thumb_errors[key] = str(exc)
            return False
        self._thumb_image.remove_class("hidden")
        self._thumb_fallback.add_class("hidden")
        return True

    def _show_chafa_thumbnail(self, key: tuple[str, str], path: Path) -> bool:
        if self._thumb_fallback is None or self._thumb_image is None:
            return False
        chafa_path = self._ensure_chafa_path()
        if not chafa_path:
            return False
        cached = self._thumb_fallback_cache.get(key)
        if cached is None:
            cached = _render_chafa_output(chafa_path, path)
            if cached is None:
                return False
            self._thumb_fallback_cache[key] = cached
        self._thumb_fallback.update(cached)
        self._thumb_fallback.remove_class("hidden")
        self._thumb_image.add_class("hidden")
        return True

    def _refresh_preview_if_selected(self, item: QueueItem) -> None:
        if self._selected is None:
            return
        if item.resolved != self._selected:
            return
        self._refresh_preview_text(self._selected, item)


_SELECTED_ICON = ""
_UNSELECTED_ICON = ""


def _format_list_label(clip: ResolvedClip, selected: bool, warning: bool) -> Text:
    tag = clip.display_tag or "-"
    label_tag = f" [{clip.clip.label}]" if clip.clip.label else ""
    warning_icon = "!" if warning else " "
    label = Text()
    label.append(_SELECTED_ICON if selected else _UNSELECTED_ICON, style=_clip_selection_style(selected))
    label.append(" ")
    label.append(warning_icon, style="#e0af68" if warning else "dim")
    label.append(" ")
    label.append(
        f"{tag}{label_tag} | {clip.video_id} | "
        f"{format_seconds(clip.start_sec)}-{format_seconds(clip.end_sec)}"
    )
    return label


def _clip_selection_style(selected: bool) -> str:
    return "bold #9ece6a" if selected else "#a9b1d6"


def _format_queue_label(item: QueueItem, selected: bool = False) -> str:
    marker = "(x)" if selected else "( )"
    status = item.status.value.upper()
    filename = _format_output_name(item.output_name, item.output_format)
    label = f"{marker} {status:11} {filename}"
    meta_parts = []
    percent = _format_percent(item.progress)
    if percent:
        meta_parts.append(percent)
    speed = _format_speed(item.speed_bps)
    if speed:
        meta_parts.append(speed)
    eta = _format_eta(item.eta_seconds)
    if eta:
        meta_parts.append(eta)
    if meta_parts:
        label = f"{label} | {' '.join(meta_parts)}"
    if item.status == DownloadStatus.FAILED and item.error:
        label = f"{label} | {_short_error(item.error)}"
    return label


def _format_group_label(group: ClipGroup, collapsed: bool, title: str | None) -> Text:
    icon = "+" if collapsed else "-"
    count = len(group.clips)
    duration = _format_total_duration(group.total_duration)
    label = Text()
    label.append(icon, style="bold #7dcfff")
    label.append(" ")
    label.append(group.video_id, style="bold #e0af68")
    if title:
        label.append(f" | {title}", style="#c0caf5")
    label.append(f" | {count} clips | {duration}", style="#a9b1d6")
    return label


def _format_group_preview(group: ClipGroup, title: str | None) -> str:
    lines = [
        f"Group: {group.video_id}",
        f"Title: {title or '--'}",
        f"Clips: {len(group.clips)}",
        f"Total: {_format_total_duration(group.total_duration)}",
    ]
    return "\n".join(lines)


def _progress_value(item: QueueItem) -> float | None:
    if item.progress is None:
        return 0.0
    return max(0.0, min(100.0, item.progress))


def _format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:4.1f}%"


def _format_speed(speed_bps: float | None) -> str | None:
    if speed_bps is None:
        return None
    return f"{_format_bytes(speed_bps)}/s"


def _format_eta(eta_seconds: int | None) -> str | None:
    if eta_seconds is None:
        return None
    minutes, seconds = divmod(eta_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"ETA {hours:d}:{minutes:02d}:{seconds:02d}"
    return f"ETA {minutes:02d}:{seconds:02d}"


def _format_bytes(value: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = value
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{size:.0f}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PiB"


def _short_error(message: str) -> str:
    line = message.splitlines()[0] if message else ""
    return (line[:77] + "...") if len(line) > 80 else line


def _format_preview(
    clip: ResolvedClip,
    item: QueueItem | None,
    output_name: str,
    default_format: str,
    metadata: VideoMetadata | None,
    metadata_error: str | None,
    warnings: list[OverlapFinding] | None,
    merges: list[MergeSuggestion] | None,
) -> str:
    tag = _format_tag_label(clip)
    label_value = _format_preview_value(clip.clip.label)
    rotation = _format_preview_value(clip.clip.rotation)
    score = _format_preview_value(clip.clip.score)
    opponent = _format_preview_value(clip.clip.opponent)
    serve_target = _format_preview_value(clip.clip.serve_target)
    output_format = item.output_format if item else default_format
    output_name = _format_output_name(output_name, output_format)
    title = metadata.title if metadata and metadata.title else "--"
    uploader = metadata.uploader if metadata and metadata.uploader else "--"
    duration = _format_duration(metadata.duration) if metadata else None
    lines: list[str] = []
    if metadata_error:
        lines.append(f"Metadata error: {_short_error(metadata_error)}")
    elif metadata is None:
        lines.append("Metadata: loading...")
    lines.extend(
        [
            f"Title: {title}",
            f"Uploader: {uploader}",
            f"Duration: {_format_preview_value(duration)}",
            "",
            f"Tag: {tag}",
            f"Label: {label_value}",
            f"Rotation: {rotation}",
            f"Score: {score}",
            f"Opponent: {opponent}",
            f"Serve Target: {serve_target}",
            f"Video ID: {clip.video_id}",
            f"Start: {format_seconds(clip.start_sec)}",
            f"End: {format_seconds(clip.end_sec)}",
            f"Cut: {format_seconds(clip.cut_start)}-{format_seconds(clip.cut_end)}",
            f"Output: {output_name}",
        ]
    )

    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"- {_format_overlap_warning(clip, warning)}")

    if merges:
        lines.append("")
        lines.append("Merge candidates:")
        for suggestion in merges:
            lines.append(f"- {_format_merge_hint(clip, suggestion)}")

    if item is None:
        lines.append("")
        lines.append("Queue: not queued")
    else:
        lines.append("")
        lines.append(f"Queue: {item.status.value.upper()}")
        lines.append(f"Progress: {_format_preview_value(_format_percent(item.progress))}")
        lines.append(f"Speed: {_format_preview_value(_format_speed(item.speed_bps))}")
        lines.append(f"ETA: {_format_preview_value(_format_eta(item.eta_seconds))}")
        if item.error:
            lines.append(f"Error: {_short_error(item.error)}")

    lines.extend(
        [
            "",
            f"Start URL: {clip.clip.start_url}",
            f"End URL: {clip.clip.end_url}",
        ]
    )
    return "\n".join(lines)


def _clip_has_context(clip: ResolvedClip) -> bool:
    return any(
        [
            clip.clip.label,
            clip.clip.rotation,
            clip.clip.score,
            clip.clip.opponent,
            clip.clip.serve_target,
        ]
    )


def _build_clip_sidecar_payload(
    clip: ResolvedClip,
    output_path: Path,
    metadata: VideoMetadata | None,
) -> dict[str, object]:
    tag = clip.display_tag if clip.display_tag is not None else clip.clip.tag
    return {
        "tag": tag,
        "label": clip.clip.label,
        "rotation": clip.clip.rotation,
        "score": clip.clip.score,
        "opponent": clip.clip.opponent,
        "serve_target": clip.clip.serve_target,
        "video_id": clip.video_id,
        "start_sec": clip.start_sec,
        "end_sec": clip.end_sec,
        "cut_start": clip.cut_start,
        "cut_end": clip.cut_end,
        "start_url": clip.clip.start_url,
        "end_url": clip.clip.end_url,
        "output_file": output_path.name,
        "output_path": str(output_path),
        "title": metadata.title if metadata and metadata.title else None,
        "uploader": metadata.uploader if metadata and metadata.uploader else None,
        "webpage_url": metadata.webpage_url if metadata and metadata.webpage_url else None,
    }


def _sidecar_path(output_path: Path) -> Path:
    suffix = output_path.suffix
    if suffix:
        return output_path.with_suffix(f"{suffix}.clip.json")
    return output_path.with_suffix(".clip.json")


def _format_preview_value(value: str | None) -> str:
    return value if value else "--"


def _format_duration(duration: int | None) -> str | None:
    if duration is None:
        return None
    minutes, seconds = divmod(duration, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _format_tag_label(clip: ResolvedClip) -> str:
    if clip.clip.tag:
        return clip.clip.tag
    if clip.display_tag:
        return f"{clip.display_tag} (auto)"
    return "(none)"


def _format_overlap_warning(clip: ResolvedClip, finding: OverlapFinding) -> str:
    other = finding.second if finding.first == clip else finding.first
    other_tag = other.display_tag or "-"
    range_text = f"{format_seconds(other.start_sec)}-{format_seconds(other.end_sec)}"
    if finding.kind == OverlapKind.DUPLICATE:
        return f"Duplicate of {other_tag} ({range_text})"
    percent = int(round(finding.overlap_ratio * 100))
    return f"Overlap {percent}% with {other_tag} ({range_text})"


def _format_merge_hint(clip: ResolvedClip, suggestion: MergeSuggestion) -> str:
    other = suggestion.second if suggestion.first == clip else suggestion.first
    other_tag = other.display_tag or "-"
    gap = format_seconds(suggestion.gap_seconds)
    return f"Adjacent to {other_tag} (gap {gap}s)"


def _format_total_duration(total_seconds: float) -> str:
    total = int(round(total_seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _group_title(metadata: VideoMetadata | None) -> str | None:
    if metadata is None:
        return None
    title = (metadata.title or "").strip()
    if not title:
        return None
    return _truncate(title, 48)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _thumb_key(clip: ResolvedClip) -> tuple[str, str]:
    return (clip.video_id, format_seconds(clip.start_sec))


def _index_overlaps(findings: list[OverlapFinding]) -> dict[ResolvedClip, list[OverlapFinding]]:
    index: dict[ResolvedClip, list[OverlapFinding]] = {}
    for finding in findings:
        index.setdefault(finding.first, []).append(finding)
        index.setdefault(finding.second, []).append(finding)
    return index


def _index_merges(
    suggestions: list[MergeSuggestion],
) -> dict[ResolvedClip, list[MergeSuggestion]]:
    index: dict[ResolvedClip, list[MergeSuggestion]] = {}
    for suggestion in suggestions:
        index.setdefault(suggestion.first, []).append(suggestion)
        index.setdefault(suggestion.second, []).append(suggestion)
    return index


def _apply_pad_updates(
    specs: list[ClipSpec],
    indices: list[int],
    pad_before: int | None,
    pad_after: int | None,
) -> None:
    for index in indices:
        spec = specs[index]
        new_before = pad_before if pad_before is not None else spec.pad_before
        new_after = pad_after if pad_after is not None else spec.pad_after
        specs[index] = ClipSpec(
            start_url=spec.start_url,
            end_url=spec.end_url,
            tag=spec.tag,
            label=spec.label,
            rotation=spec.rotation,
            score=spec.score,
            opponent=spec.opponent,
            serve_target=spec.serve_target,
            pad_before=new_before,
            pad_after=new_after,
        )


def _normalize_pad_overrides(
    specs: list[ClipSpec],
    default_before: int,
    default_after: int,
) -> bool:
    changed = False
    for index, spec in enumerate(specs):
        if spec.pad_before is None or spec.pad_after is None:
            continue
        if spec.pad_before == default_before and spec.pad_after == default_after:
            specs[index] = ClipSpec(
                start_url=spec.start_url,
                end_url=spec.end_url,
                tag=spec.tag,
                label=spec.label,
                rotation=spec.rotation,
                score=spec.score,
                opponent=spec.opponent,
                serve_target=spec.serve_target,
                pad_before=None,
                pad_after=None,
            )
            changed = True
    return changed


def _read_system_clipboard() -> str | None:
    try:
        import tkinter
    except ImportError:
        return None
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.update()
        text = root.clipboard_get()
        return text
    except tkinter.TclError:
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _render_chafa_output(chafa_path: str | None, path: Path) -> Text | None:
    if not chafa_path:
        return None
    command = [chafa_path, "-f", "symbols", "-s", "48x12", str(path)]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout or ""
    if not output.strip():
        return None
    return Text.from_ansi(output)


def _update_image_widget(widget: PreviewImage, path: Path) -> None:
    setter = getattr(widget, "set_image", None)
    if callable(setter):
        setter(path)
        return
    if hasattr(widget, "image"):
        setattr(widget, "image", path)
        return
    if hasattr(widget, "path"):
        setattr(widget, "path", path)
        return
    widget.update(str(path))


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


def _relative_display(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    return str(relative) if str(relative) else "."


def _path_key(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    return str(resolved).casefold()


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


def _format_output_name(base: str, output_format: str) -> str:
    ext = output_format.lower().lstrip(".")
    return f"{base}.{ext}"


def _entry_is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return False
    hidden_flag = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)
    return bool(attrs & hidden_flag)


def _format_entry_line(root: Path, entry: PathEntry) -> str:
    rel = entry.path.relative_to(root)
    text = rel.as_posix()
    kind = FileEntryKind.DIR if entry.is_dir else FileEntryKind.FILE
    icon = file_icon_for_kind(kind, entry.path)
    suffix = "/" if entry.is_dir else ""
    return f"{icon} {text}{suffix}"


def _format_drive_line(drive: Path) -> str:
    icon = file_icon_for_kind(FileEntryKind.DIR, drive)
    return f"{icon} {drive}"


def _format_parent_line(root: Path) -> str | None:
    parent = root.parent
    if parent == root:
        icon = file_icon_for_kind(FileEntryKind.UP, root)
        return f"{icon} .."
    icon = file_icon_for_kind(FileEntryKind.UP, parent)
    return f"{icon} .."


def _normalize_output_format(value: str) -> str:
    return value.strip().lower().lstrip(".")


def _is_valid_output_format(value: str) -> bool:
    if not value:
        return False
    return value.isalnum() and len(value) <= 6


def main() -> None:
    parser = argparse.ArgumentParser(prog="clipstui")
    parser.add_argument("clip_file", nargs="?", help="Path to clip file")
    parser.add_argument("--output-dir", help="Output directory for downloads")
    parser.add_argument("--output-format", help="Output format, e.g. mp4 or mkv")
    parser.add_argument("--output-template", help="Output filename template")
    parser.add_argument("--preset", help="Preset profile name")
    args = parser.parse_args()
    path = Path(args.clip_file).expanduser() if args.clip_file else None
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    preset = find_preset(args.preset) if args.preset else None
    if args.preset and preset is None:
        names = ", ".join(preset.name for preset in list_presets()) or "none"
        parser.error(f"Unknown preset: {args.preset}. Available: {names}")
    try:
        app = ClipstuiApp(
            clip_path=path,
            output_dir=output_dir,
            output_format=args.output_format,
            output_template=args.output_template,
            preset=preset,
        )
    except ValueError as exc:
        parser.error(str(exc))
    app.run()




