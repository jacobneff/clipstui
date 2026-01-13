import subprocess
from pathlib import Path

from clipstui.download_queue import DownloadStatus
from clipstui.parser import ClipSpec
from clipstui.resolve import resolve_clip
from clipstui.timeparse import format_seconds
from clipstui.ytdlp_runner import build_ytdlp_command, parse_progress_line, run_ytdlp


def test_build_ytdlp_command_strips_time() -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=abc123&t=20",
        tag=None,
    )
    resolved = resolve_clip(clip, pad_before=0, pad_after=0)
    cmd = build_ytdlp_command(resolved, Path("out"), "mp4")
    assert cmd[0] == "yt-dlp"
    assert "--progress-template" in cmd
    assert "--merge-output-format" in cmd
    assert "--download-sections" in cmd
    assert f"*{format_seconds(resolved.cut_start)}-{format_seconds(resolved.cut_end)}" in cmd
    assert "t=10" not in " ".join(cmd)
    assert cmd[-1].endswith("watch?v=abc123")
    assert "%(ext)s" in " ".join(cmd)


def test_run_ytdlp_success(tmp_path: Path) -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=abc123&t=20",
        tag=None,
    )
    resolved = resolve_clip(clip, pad_before=0, pad_after=0)

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = run_ytdlp(resolved, tmp_path, "mp4", runner=runner)
    assert result.status == DownloadStatus.DONE
    assert result.output_path == tmp_path / f"{resolved.output_name}.mp4"


def test_run_ytdlp_failure(tmp_path: Path) -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=abc123&t=20",
        tag=None,
    )
    resolved = resolve_clip(clip, pad_before=0, pad_after=0)

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    result = run_ytdlp(resolved, tmp_path, "mp4", runner=runner)
    assert result.status == DownloadStatus.FAILED
    assert result.error == "boom"


def test_parse_progress_line_template() -> None:
    line = (
        "clipstui:status=downloading percent=12.5 downloaded=100 total=800 "
        "total_est=None speed=2048 eta=9"
    )
    update = parse_progress_line(line)
    assert update is not None
    assert update.percent == 12.5
    assert update.speed_bps == 2048.0
    assert update.eta_seconds == 9
