# clipstui — Agent Instructions

## Project goal

Build a polished Python TUI for managing and downloading YouTube volleyball clips defined in a simple text format:

CLIP
<start_url_with_t_or_start>
<end_url_with_t_or_start>

The app provides:

- File browser (select clip text files)
- Clip list (grouped by video)
- Preview pane (metadata + thumbnail)
- Download queue with progress, retries, and a failed log

Target platform: Windows (Windows Terminal Preview with Sixel support when available).

---

## Tech stack

- Python 3.12
- Textual for the TUI
- textual-image for inline image rendering (Sixel where supported)
- yt-dlp + ffmpeg for downloads (must be on PATH)
- Optional: chafa as a fallback thumbnail renderer (if installed on PATH)

Tooling:

- uv for packaging + virtual env
- pytest for tests
- ruff for formatting/lint
- mypy (or pyright) for basic type checking

---

## Repo layout (src layout)

- pyproject.toml
- README.md
- agents.md (this file)
- src/clipstui/
  - __init__.py
  - app.py                # Textual App entry point
  - models.py             # dataclasses + enums
  - parser.py             # CLIP file parsing
  - timeparse.py          # time token parsing and URL time extraction
  - resolve.py            # padding + output naming + grouping logic
  - ytdlp_runner.py       # subprocess runner + progress parsing + events
  - metadata.py           # yt-dlp --dump-json cache for titles/thumbs
  - thumbs.py             # thumbnail download + render helpers
  - paths.py              # cache dirs via platformdirs
- tests/
  - test_parser.py
  - test_timeparse.py
  - test_resolve.py

---

## Core design rules

1) Separate engine from UI.
   - Core modules must not import Textual.
   - UI calls engine functions and subscribes to events.

2) No shell=True.
   - Always use subprocess with argument lists.
   - Quote/escape is handled by passing args as a list.

3) Streaming progress.
   - yt-dlp output must be read line-by-line.
   - Emit structured progress events; UI renders progress bars.

4) Caching.
   - Cache yt-dlp metadata by video ID (JSON).
   - Cache thumbnails by video ID (image files).
   - Use platformdirs for cache location.

5) Deterministic file naming.
   - Output name should include tag, start-end seconds, and video ID.
   - Must be safe across Windows filesystem rules.

---

## Clip format behavior

- Ignore blank lines and lines beginning with '#'
- Each CLIP block has:
  - "CLIP" line (optionally "CLIP <tag>")
  - start URL line
  - end URL line
- Compute start/end seconds from:
  - query param t= or start=
  - tokens accepted:
    - integer seconds (e.g., 1149 or 1149s)
    - mm:ss or hh:mm:ss
    - 1h2m3s / 2m10s / 40s

Validation:

- If end <= start, mark as failed with reason.

Padding:

- cutStart = max(0, start - PadBefore)
- cutEnd = end + PadAfter

---

## External dependencies (runtime)

clipstui assumes these commands exist:

- yt-dlp
- ffmpeg
Optional:
- chafa

If missing, show a clear error in the UI and provide the exact missing command name.

---

## Progress parsing approach

Use yt-dlp with a parse-friendly progress format.

- Prefer a stable progress template that prints percent, speed, eta.
- Fall back to "spinner + last line" if parsing fails.

Always tolerate incomplete/malformed progress lines.

---

## Textual UI requirements

- 3-pane layout:
  - Left: DirectoryTree (browse/select a text file)
  - Middle: clip list (multi-select)
  - Right: preview (metadata + thumbnail + computed ranges)

- Queue view:
  - shows each clip job with status, percent, speed, eta
  - actions: Start, Pause/Cancel (best-effort), Retry failed, Open failed log

Keybindings (suggested defaults):

- q: quit
- r: reload file
- /: search
- space: toggle select
- d: download selected
- a: add clip (paste helper)
- e: edit clip (simple modal for start/end/pad/tag)

---

## Coding conventions

- Use dataclasses and enums for state.
- Type hints everywhere (at least public functions).
- Keep functions small and testable.
- Prefer pathlib.Path over raw strings for paths.
- Write tests for parsing/time conversion/output naming first.

---

## Definition of Done (per ticket)

A ticket is done when:

- It has unit tests for normal + edge cases
- It runs on Windows (PowerShell) via:
  - uv run python -m clipstui
- Errors are user-readable (no raw stack traces in normal flow)

---

## Initial build steps (agent should implement)

1) Scaffold pyproject with uv.
2) Add dependencies:
   - textual
   - textual-image
   - platformdirs
   - httpx (or requests) for thumbnail download
   - pytest, ruff, mypy (dev)
3) Create minimal app.py that opens a Textual UI with placeholder panes.
4) Implement Ticket #1: parser + tests.

---

## Progress checklist

- [x] Scaffold repo (pyproject, src layout, entrypoints, tests)
- [x] Parse CLIP blocks + parser tests
- [x] Time parsing + resolve logic + tests
- [x] Textual UI with file tree, clip list, preview pane, and keybinds
- [x] File browser root switching (multi-drive browsing)
- [x] Download queue with progress parsing and retry support
- [x] Output format selection (CLI + UI)
- [x] Preview shows per-clip progress, speed, and ETA
- [x] yt-dlp runner with structured progress parsing + tests
- [x] Help modal (`?`) + tip bar and output directory selection
- [x] Metadata cache (`yt-dlp --dump-json`) and thumbnail download
- [x] Preview thumbnails (textual-image + chafa fallback)
- [ ] Clip editor (add/edit/pad/tag) and validation hints
- [ ] Multi-select downloads and queue controls (pause/cancel)
- [ ] Failed log file + open/copy helpers
