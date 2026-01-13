from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .parser import ClipSpec
from .timeparse import format_seconds, get_seconds_from_url

_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ResolvedClip:
    clip: ClipSpec
    start_sec: float
    end_sec: float
    cut_start: float
    cut_end: float
    video_id: str
    output_name: str
    display_tag: str | None


def resolve_clip(
    clip: ClipSpec,
    pad_before: int,
    pad_after: int,
    *,
    tag_override: str | None = None,
) -> ResolvedClip:
    pad_before = clip.pad_before if clip.pad_before is not None else pad_before
    pad_after = clip.pad_after if clip.pad_after is not None else pad_after
    if pad_before < 0 or pad_after < 0:
        raise ValueError("Padding values must be non-negative")

    start_sec = get_seconds_from_url(clip.start_url)
    end_sec = get_seconds_from_url(clip.end_url)
    if end_sec <= start_sec:
        raise ValueError("Clip end must be greater than start")

    start_id = extract_video_id(clip.start_url)
    end_id = extract_video_id(clip.end_url)
    if start_id != end_id:
        raise ValueError("Start and end URLs refer to different videos")

    cut_start = max(0.0, start_sec - pad_before)
    cut_end = end_sec + pad_after
    effective_tag = tag_override if tag_override is not None else clip.tag
    output_name = _build_output_filename(effective_tag, start_id, start_sec, end_sec)

    return ResolvedClip(
        clip=clip,
        start_sec=start_sec,
        end_sec=end_sec,
        cut_start=cut_start,
        cut_end=cut_end,
        video_id=start_id,
        output_name=output_name,
        display_tag=effective_tag,
    )


def _build_output_filename(
    tag: str | None, video_id: str, start_sec: float, end_sec: float
) -> str:
    parts: list[str] = []
    if tag:
        parts.append(tag)
    parts.append(video_id)
    parts.append(f"{format_seconds(start_sec)}-{format_seconds(end_sec)}")
    base = "_".join(parts)
    base = _sanitize_basename(base)
    return base


def _sanitize_basename(name: str) -> str:
    cleaned = []
    for char in name:
        if char in _INVALID_FILENAME_CHARS or ord(char) < 32:
            cleaned.append("_")
        else:
            cleaned.append(char)

    sanitized = "".join(cleaned)
    sanitized = _WHITESPACE_RE.sub("_", sanitized)
    sanitized = sanitized.strip(" .")
    return sanitized or "clip"


def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "v" in query and query["v"]:
        return query["v"][0]

    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    if host in {"youtu.be", "www.youtu.be"} and path:
        return path.split("/")[0]

    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
        return parts[1]

    raise ValueError("Unable to determine video id from URL")
