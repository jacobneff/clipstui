from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .download_queue import DownloadStatus
from .resolve import ResolvedClip
from .timeparse import format_seconds

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
ProgressCallback = Callable[["ProgressUpdate"], None]

_PROGRESS_PREFIX = "clipstui:"
_PROGRESS_TEMPLATE = (
    "clipstui:status=%(progress.status)s "
    "percent=%(progress.percent)s "
    "downloaded=%(progress.downloaded_bytes)s "
    "total=%(progress.total_bytes)s "
    "total_est=%(progress.total_bytes_estimate)s "
    "speed=%(progress.speed)s "
    "eta=%(progress.eta)s"
)
_PROGRESS_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
_ETA_RE = re.compile(r"ETA\s+(\d{1,2}:\d{2}(?::\d{2})?)")
_SPEED_RE = re.compile(r"(\d+(?:\.\d+)?)([KMGTP]?i?B)/s")


@dataclass(frozen=True)
class ProgressUpdate:
    percent: float | None
    eta_seconds: int | None
    speed_bps: float | None
    status: str | None = None


@dataclass(frozen=True)
class DownloadResult:
    status: DownloadStatus
    output_path: Path | None
    error: str | None = None


def build_ytdlp_command(
    clip: ResolvedClip,
    output_dir: Path,
    output_format: str,
    output_name: str | None = None,
) -> list[str]:
    output_format = output_format.lower().lstrip(".")
    name = output_name or clip.output_name
    output_path = output_dir / f"{name}.%(ext)s"
    section = f"*{format_seconds(clip.cut_start)}-{format_seconds(clip.cut_end)}"
    base_url = _strip_time_params(clip.clip.start_url)
    return [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--no-color",
        "--progress-template",
        f"download:{_PROGRESS_TEMPLATE}",
        "--merge-output-format",
        output_format,
        "--download-sections",
        section,
        "-o",
        str(output_path),
        base_url,
    ]


def run_ytdlp(
    clip: ResolvedClip,
    output_dir: Path,
    output_format: str,
    *,
    output_name: str | None = None,
    runner: Runner | None = None,
) -> DownloadResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_format = output_format.lower().lstrip(".")
    command = build_ytdlp_command(clip, output_dir, output_format, output_name)
    runner = runner or _run_subprocess

    try:
        completed = runner(command)
    except FileNotFoundError:
        return DownloadResult(
            status=DownloadStatus.FAILED,
            output_path=None,
            error="yt-dlp not found on PATH",
        )

    output_path = _expected_output_path(output_dir, clip, output_format, output_name)
    if completed.returncode != 0:
        error = _summarize_error(completed)
        return DownloadResult(
            status=DownloadStatus.FAILED,
            output_path=output_path,
            error=error,
        )

    return DownloadResult(
        status=DownloadStatus.DONE,
        output_path=output_path,
        error=None,
    )


def run_ytdlp_with_progress(
    clip: ResolvedClip,
    output_dir: Path,
    output_format: str,
    on_progress: ProgressCallback,
    cancel_event: threading.Event | None = None,
    *,
    output_name: str | None = None,
) -> DownloadResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_format = output_format.lower().lstrip(".")
    command = build_ytdlp_command(clip, output_dir, output_format, output_name)
    output_path = _expected_output_path(output_dir, clip, output_format, output_name)

    if cancel_event is not None and cancel_event.is_set():
        return DownloadResult(
            status=DownloadStatus.CANCELED,
            output_path=output_path,
            error="Canceled",
        )

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return DownloadResult(
            status=DownloadStatus.FAILED,
            output_path=None,
            error="yt-dlp not found on PATH",
        )

    last_message = ""

    def read_output() -> None:
        nonlocal last_message
        if process.stdout is None:
            return
        for line in process.stdout:
            stripped = line.strip()
            update = parse_progress_line(stripped)
            if update is not None:
                on_progress(update)
            elif stripped:
                last_message = stripped

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    canceled = False
    if cancel_event is not None:
        while process.poll() is None:
            if cancel_event.wait(timeout=0.1):
                canceled = True
                _terminate_process(process)
                break

    returncode = process.wait()
    reader.join(timeout=0.2)
    if canceled:
        return DownloadResult(
            status=DownloadStatus.CANCELED,
            output_path=output_path,
            error="Canceled",
        )
    if returncode != 0:
        error = last_message or f"yt-dlp failed with exit code {returncode}"
        return DownloadResult(
            status=DownloadStatus.FAILED,
            output_path=output_path,
            error=error,
        )

    return DownloadResult(
        status=DownloadStatus.DONE,
        output_path=output_path,
        error=None,
    )


def parse_progress_line(line: str) -> ProgressUpdate | None:
    if not line:
        return None

    if line.startswith(_PROGRESS_PREFIX):
        data = _parse_key_values(line[len(_PROGRESS_PREFIX) :].strip())
        percent = _parse_float(data.get("percent"))
        downloaded = _parse_float(data.get("downloaded"))
        total = _parse_float(data.get("total"))
        total_est = _parse_float(data.get("total_est"))
        if percent is None and downloaded is not None:
            total_value = total if total and total > 0 else total_est
            if total_value:
                percent = (downloaded / total_value) * 100
        percent = _clamp_percent(percent)
        eta_seconds = _parse_int(data.get("eta"))
        speed_bps = _parse_speed(data.get("speed"))
        status = data.get("status")
        return ProgressUpdate(
            percent=percent,
            eta_seconds=eta_seconds,
            speed_bps=speed_bps,
            status=status,
        )

    percent = _parse_percent_from_line(line)
    eta_seconds = _parse_eta_from_line(line)
    if percent is None and eta_seconds is None:
        return None
    return ProgressUpdate(
        percent=_clamp_percent(percent),
        eta_seconds=eta_seconds,
        speed_bps=None,
        status=None,
    )


def _run_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _strip_time_params(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query.pop("t", None)
    query.pop("start", None)
    cleaned_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=cleaned_query, fragment=""))


def _summarize_error(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = completed.stderr or ""
    stdout = completed.stdout or ""
    message = stderr.strip() or stdout.strip()
    if not message:
        return f"yt-dlp failed with exit code {completed.returncode}"
    return message.splitlines()[-1]


def _parse_key_values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in text.split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key] = value
    return result


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "nan", "na"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "nan", "na"}:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _parse_speed(value: str | None) -> float | None:
    numeric = _parse_float(value)
    if numeric is not None:
        return numeric
    if value is None:
        return None
    match = _SPEED_RE.search(value)
    if not match:
        return None
    magnitude = float(match.group(1))
    unit = match.group(2)
    return magnitude * _unit_multiplier(unit)


def _unit_multiplier(unit: str) -> float:
    unit = unit.upper()
    order = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit = unit.replace("IB", "B")
    if unit not in order:
        return 1.0
    return 1024 ** order.index(unit)


def _parse_percent_from_line(line: str) -> float | None:
    match = _PROGRESS_RE.search(line)
    if not match:
        return None
    return float(match.group(1))


def _parse_eta_from_line(line: str) -> int | None:
    match = _ETA_RE.search(line)
    if not match:
        return None
    token = match.group(1)
    parts = token.split(":")
    if len(parts) == 2:
        hours_text = "0"
        minutes_text, seconds_text = parts
    else:
        hours_text, minutes_text, seconds_text = parts
    return int(hours_text) * 3600 + int(minutes_text) * 60 + int(seconds_text)


def _clamp_percent(percent: float | None) -> float | None:
    if percent is None:
        return None
    return max(0.0, min(100.0, percent))


def _expected_output_path(
    output_dir: Path,
    clip: ResolvedClip,
    output_format: str,
    output_name: str | None = None,
) -> Path:
    ext = output_format.lower().lstrip(".")
    name = output_name or clip.output_name
    return output_dir / f"{name}.{ext}"


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
