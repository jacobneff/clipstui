from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .paths import metadata_cache_dir
from .resolve import extract_video_id

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    title: str | None
    uploader: str | None
    duration: int | None
    thumbnail_url: str | None
    webpage_url: str | None


def get_metadata(
    url: str,
    cache_dir: Path | None = None,
    runner: Runner | None = None,
) -> VideoMetadata:
    video_id = extract_video_id(url)
    cache_dir = cache_dir or metadata_cache_dir()
    cache_path = cache_dir / f"{video_id}.json"

    data = _read_cached_json(cache_path)
    if data is None:
        data = _run_dump_json(url, runner)
        _write_json(cache_path, data)

    return _parse_metadata(data, video_id)


def _run_dump_json(url: str, runner: Runner | None) -> dict[str, Any]:
    command = ["yt-dlp", "--dump-json", "--skip-download", "--no-playlist", url]
    runner = runner or _run_subprocess
    completed = runner(command)
    if completed.returncode != 0:
        message = _summarize_error(completed)
        raise RuntimeError(message)

    stdout = completed.stdout or ""
    line = _last_non_empty_line(stdout)
    if line is None:
        raise RuntimeError("yt-dlp returned no metadata")

    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse metadata JSON") from exc


def _run_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _summarize_error(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = completed.stderr or ""
    stdout = completed.stdout or ""
    message = stderr.strip() or stdout.strip()
    if not message:
        return f"yt-dlp failed with exit code {completed.returncode}"
    return message.splitlines()[-1]


def _last_non_empty_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return None


def _read_cached_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def _parse_metadata(data: dict[str, Any], video_id: str) -> VideoMetadata:
    duration_value = data.get("duration")
    duration = int(duration_value) if isinstance(duration_value, (int, float)) else None
    return VideoMetadata(
        video_id=str(data.get("id") or video_id),
        title=_as_str(data.get("title")),
        uploader=_as_str(data.get("uploader")),
        duration=duration,
        thumbnail_url=_as_str(data.get("thumbnail")),
        webpage_url=_as_str(data.get("webpage_url")),
    )


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
