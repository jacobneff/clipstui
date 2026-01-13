from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable
from urllib.parse import urlparse

import httpx

from .paths import thumbs_cache_dir
from .timeparse import format_seconds

Fetcher = Callable[[str], bytes]
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]

_VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def download_thumbnail(
    url: str,
    video_id: str,
    cache_dir: Path | None = None,
    fetcher: Fetcher | None = None,
) -> Path:
    if not url:
        raise ValueError("Missing thumbnail URL")

    cache_dir = cache_dir or thumbs_cache_dir()
    ext = _guess_extension(url)
    path = cache_dir / f"{video_id}{ext}"
    if path.exists():
        return path

    fetcher = fetcher or _http_fetch
    data = fetcher(url)
    path.write_bytes(data)
    return path


def generate_clip_thumbnail(
    url: str,
    video_id: str,
    start_sec: float,
    cache_dir: Path | None = None,
    runner: Runner | None = None,
) -> Path:
    if start_sec < 0:
        raise ValueError("Start time must be non-negative")
    cache_dir = cache_dir or thumbs_cache_dir()
    token = format_seconds(start_sec)
    path = cache_dir / f"{video_id}_{token}.jpg"
    if path.exists():
        return path
    runner = runner or _run_subprocess
    direct_url = _get_direct_video_url(url, runner)
    _extract_frame(direct_url, start_sec, path, runner)
    return path


def _guess_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in _VALID_EXTENSIONS:
        return suffix
    return ".jpg"


def _http_fetch(url: str) -> bytes:
    with httpx.Client(follow_redirects=True, timeout=10.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def _get_direct_video_url(url: str, runner: Runner) -> str:
    command = [
        "yt-dlp",
        "-g",
        "-f",
        "bestvideo+bestaudio/best",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        url,
    ]
    completed = runner(command)
    if completed.returncode != 0:
        raise RuntimeError(_summarize_error(completed, "yt-dlp"))
    stdout = completed.stdout or ""
    line = _first_non_empty_line(stdout)
    if line is None:
        raise RuntimeError("yt-dlp returned no direct URL")
    return line


def _extract_frame(url: str, start_sec: float, output_path: Path, runner: Runner) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        format_seconds(start_sec),
        "-i",
        url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-y",
        str(output_path),
    ]
    completed = runner(command)
    if completed.returncode != 0:
        raise RuntimeError(_summarize_error(completed, "ffmpeg"))
    if not output_path.exists():
        raise RuntimeError("ffmpeg did not write thumbnail")


def _run_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing command: {command[0]}") from exc


def _summarize_error(completed: subprocess.CompletedProcess[str], tool: str) -> str:
    stderr = completed.stderr or ""
    stdout = completed.stdout or ""
    message = stderr.strip() or stdout.strip()
    if not message:
        return f"{tool} failed with exit code {completed.returncode}"
    return message.splitlines()[-1]


def _first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None

