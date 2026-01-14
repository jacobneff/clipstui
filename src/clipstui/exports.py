from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .metadata import VideoMetadata
from .resolve import ResolvedClip, format_output_basename
from .timeparse import format_seconds

MANIFEST_FIELDS = [
    "index",
    "tag",
    "label",
    "rotation",
    "score",
    "opponent",
    "serve_target",
    "video_id",
    "start_sec",
    "end_sec",
    "cut_start",
    "cut_end",
    "duration",
    "cut_duration",
    "pad_before",
    "pad_after",
    "output_name",
    "output_format",
    "output_file",
    "output_path",
    "title",
    "uploader",
    "video_duration",
    "webpage_url",
    "start_url",
    "end_url",
]


@dataclass(frozen=True)
class ManifestEntry:
    index: int
    tag: str | None
    label: str | None
    rotation: str | None
    score: str | None
    opponent: str | None
    serve_target: str | None
    video_id: str
    start_sec: float
    end_sec: float
    cut_start: float
    cut_end: float
    duration: float
    cut_duration: float
    pad_before: float
    pad_after: float
    output_name: str
    output_format: str
    output_file: str
    output_path: Path
    start_url: str
    end_url: str
    title: str | None
    uploader: str | None
    video_duration: int | None
    webpage_url: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "tag": self.tag,
            "label": self.label,
            "rotation": self.rotation,
            "score": self.score,
            "opponent": self.opponent,
            "serve_target": self.serve_target,
            "video_id": self.video_id,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
            "cut_start": self.cut_start,
            "cut_end": self.cut_end,
            "duration": self.duration,
            "cut_duration": self.cut_duration,
            "pad_before": self.pad_before,
            "pad_after": self.pad_after,
            "output_name": self.output_name,
            "output_format": self.output_format,
            "output_file": self.output_file,
            "output_path": str(self.output_path),
            "title": self.title,
            "uploader": self.uploader,
            "video_duration": self.video_duration,
            "webpage_url": self.webpage_url,
            "start_url": self.start_url,
            "end_url": self.end_url,
        }


def build_manifest_entries(
    clips: Iterable[ResolvedClip],
    *,
    output_dir: Path,
    output_format: str,
    output_template: str,
    metadata: Mapping[str, VideoMetadata] | None = None,
) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    output_format = output_format.lower().lstrip(".")
    for index, clip in enumerate(clips, start=1):
        meta = metadata.get(clip.video_id) if metadata else None
        title = meta.title if meta and meta.title else None
        output_name = _output_basename(output_template, clip, title)
        output_file = f"{output_name}.{output_format}"
        output_path = output_dir / output_file
        start_sec = _round_seconds(clip.start_sec)
        end_sec = _round_seconds(clip.end_sec)
        cut_start = _round_seconds(clip.cut_start)
        cut_end = _round_seconds(clip.cut_end)
        duration = _round_seconds(max(0.0, end_sec - start_sec))
        cut_duration = _round_seconds(max(0.0, cut_end - cut_start))
        pad_before = _round_seconds(max(0.0, start_sec - cut_start))
        pad_after = _round_seconds(max(0.0, cut_end - end_sec))
        tag = clip.display_tag if clip.display_tag is not None else clip.clip.tag
        entries.append(
            ManifestEntry(
                index=index,
                tag=tag,
                label=clip.clip.label,
                rotation=clip.clip.rotation,
                score=clip.clip.score,
                opponent=clip.clip.opponent,
                serve_target=clip.clip.serve_target,
                video_id=clip.video_id,
                start_sec=start_sec,
                end_sec=end_sec,
                cut_start=cut_start,
                cut_end=cut_end,
                duration=duration,
                cut_duration=cut_duration,
                pad_before=pad_before,
                pad_after=pad_after,
                output_name=output_name,
                output_format=output_format,
                output_file=output_file,
                output_path=output_path,
                start_url=clip.clip.start_url,
                end_url=clip.clip.end_url,
                title=title,
                uploader=meta.uploader if meta and meta.uploader else None,
                video_duration=meta.duration if meta else None,
                webpage_url=meta.webpage_url if meta and meta.webpage_url else None,
            )
        )
    return entries


def manifest_to_csv(entries: Iterable[ManifestEntry]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_FIELDS)
    writer.writeheader()
    for entry in entries:
        writer.writerow(_entry_csv_row(entry))
    return buffer.getvalue()


def manifest_to_json(
    entries: Iterable[ManifestEntry],
    *,
    output_dir: Path,
    output_format: str,
    output_template: str,
) -> str:
    entries_list = list(entries)
    payload = {
        "count": len(entries_list),
        "output_dir": str(output_dir),
        "output_format": output_format,
        "output_template": output_template,
        "clips": [entry.to_dict() for entry in entries_list],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def build_concat_list(paths: Iterable[Path]) -> str:
    lines = [
        "# clipstui concat list",
        "# ffmpeg -f concat -safe 0 -i concat.txt -c copy output.mp4",
    ]
    for path in paths:
        lines.append(f"file {_concat_quote(path)}")
    return "\n".join(lines) + "\n"


def _entry_csv_row(entry: ManifestEntry) -> dict[str, str]:
    return {
        "index": str(entry.index),
        "tag": entry.tag or "",
        "label": entry.label or "",
        "rotation": entry.rotation or "",
        "score": entry.score or "",
        "opponent": entry.opponent or "",
        "serve_target": entry.serve_target or "",
        "video_id": entry.video_id,
        "start_sec": format_seconds(entry.start_sec),
        "end_sec": format_seconds(entry.end_sec),
        "cut_start": format_seconds(entry.cut_start),
        "cut_end": format_seconds(entry.cut_end),
        "duration": format_seconds(entry.duration),
        "cut_duration": format_seconds(entry.cut_duration),
        "pad_before": format_seconds(entry.pad_before),
        "pad_after": format_seconds(entry.pad_after),
        "output_name": entry.output_name,
        "output_format": entry.output_format,
        "output_file": entry.output_file,
        "output_path": str(entry.output_path),
        "title": entry.title or "",
        "uploader": entry.uploader or "",
        "video_duration": str(entry.video_duration) if entry.video_duration is not None else "",
        "webpage_url": entry.webpage_url or "",
        "start_url": entry.start_url,
        "end_url": entry.end_url,
    }


def _output_basename(template: str, clip: ResolvedClip, title: str | None) -> str:
    try:
        return format_output_basename(template, clip, title=title)
    except ValueError:
        return clip.output_name


def _round_seconds(value: float) -> float:
    return round(value, 3)


def _concat_quote(path: Path) -> str:
    text = path.as_posix()
    text = text.replace("'", "\\'")
    return f"'{text}'"
