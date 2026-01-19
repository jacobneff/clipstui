# clipstui

A Textual TUI for viewing and downloading timestamped YouTube clips via yt-dlp.

## Requirements

- Python 3.12
- `uv`
- Windows Terminal recommended (Sixel support helps later), but any terminal works for now.
- `yt-dlp` and `ffmpeg` on PATH for downloads.
- A Nerd Font for file icons (e.g., Cascadia Code NF).
- File list colors also use terminal theme colors.

## Setup

```sh
uv venv --python 3.12
uv sync --extra dev
```

## Usage

Run the app with a clip file path:

```sh
uv run clipstui path\to\clips.clip
```

The file tree defaults to the current working directory when no clip file is provided.

Optional output directory:

```sh
uv run clipstui path\to\clips.clip --output-dir path\to\downloads
```

Optional output format:

```sh
uv run clipstui path\to\clips.clip --output-format mp4
```

Optional output template:

```sh
uv run clipstui path\to\clips.clip --output-template "{tag}_{start}-{end}_{videoid}_{title}"
```

Optional preset profile:

```sh
uv run clipstui path\to\clips.clip --preset "Volleyball highlights"
```

Preview thumbnails are captured at the clip start time (requires `yt-dlp` + `ffmpeg`).

The MVP flow is:

1. Provide a `.txt` clip file or pick one from the left file tree.
2. The middle pane lists clips (tag + video id + start/end).
3. Select a clip to see the details in the right pane (start/end, padded range, output name).
4. Press `d` to queue the selected clip for download (output defaults to the clip file directory).
5. Use the queue list to start downloads, monitor progress, and retry failures.

Export helpers:

- Manifests: `*_clips.csv` + `*_clips.json` in the output directory.
- Concat list: `*_concat.txt` in the output directory for ffmpeg (absolute paths).
- Rally pack: `*_rally_pack` folder with copied outputs plus `concat.txt` and `missing.txt`.

Keybinds:

- `q`: quit (global)
- `r`: reload the current file
- `ctrl+p`: command palette
- `d`: queue selected clip
- `A`: queue all clips
- `f`: retry failed downloads
- `F`: retry failed downloads for current video
- `O`: open output in player (mpv/vlc)
- `Y`: open YouTube at clip start
- `E`: export manifest (CSV/JSON)
- `C`: export ffmpeg concat list
- `B`: create rally pack folder (copy + concat list)
- `t`: toggle auto-tag prefix by video
- `P`: set global pad defaults
- `V`: set pad for current video
- `S`: set pad for selected clips
- `N`: normalize pad overrides
- `g`: merge adjacent clips
- `d`: start queued downloads (queue list)
- `p`: pause/resume selected queue items (queue list)
- `p`: paste clip from clipboard (clip list)
- `D`: download only failed (clip list)
- `x`/`delete`: cancel selected queue items (queue list)
- `ctrl+up`/`ctrl+down`: move queue items
- `c`: create file/dir (file list)
- `R`: rename selected file/dir
- `M`: move selected file/dir
- `X`: delete selected file/dir
- `h`: toggle hidden files (when vim mode off)
- `v`: toggle vim mode for the file picker
- `o`: set output directory
- `m`: set output format (mp4/mkv/webm)
- `T`: set output template
- `L`: load preset profile
- `/`: fuzzy search files/folders in the current tree root (Telescope-style)
- `?`: show help

File picker:

- Select the folder ".." entry at the top of the left pane to move to the parent directory.
- File actions (create/rename/move/delete) apply to the left pane selection.

Vim mode (file picker):

- `j/k`: move up/down
- `gg` / `G`: top/bottom
- `h/l`: parent/enter
- `n/N`: next/prev search match
- `enter`: open directory / file
- `q`: close the file picker
- `J/K`: page down/up
- `0/$`: top/bottom

Clip list:

- `space`: toggle clip selection
- `d`/`enter`: queue current clip (or selected clips)
- `A`: queue all clips
- `p`: paste clip from clipboard
- `D`: download only failed (selected clips)
- `enter`/`space` on group header: expand/collapse

Queue list:

- `space`: toggle queue selection
- `d`: start queued downloads
- `p`: pause/resume selected queue items
- `x`/`delete`: cancel selected queue items
- `ctrl+up`/`ctrl+down`: move queue item

## Clip file format

Each clip block starts with `CLIP`, followed by two URL lines:

```text
CLIP optional-tag
https://www.youtube.com/watch?v=VIDEO_ID&t=10
https://www.youtube.com/watch?v=VIDEO_ID&t=20
```

Blank lines and lines starting with `#` are ignored.

Use the `.clip` extension for clip files (recommended). `.txt` is still accepted.

## Testing

```sh
uv run pytest
uv run mypy src
```
