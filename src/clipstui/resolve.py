from __future__ import annotations

import re
from dataclasses import dataclass
from string import Formatter
from urllib.parse import parse_qs, urlparse

from .parser import ClipSpec
from .timeparse import format_seconds, get_seconds_from_url

_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
_WHITESPACE_RE = re.compile(r"\s+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
DEFAULT_OUTPUT_TEMPLATE = "{tag}_{videoid}_{start}-{end}"
_TEMPLATE_FIELDS = {
    "tag",
    "videoid",
    "start",
    "end",
    "title",
    "label",
    "rotation",
    "score",
    "opponent",
    "serve_target",
    "serve",
}


class _SafeDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


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
    output_name = _build_output_filename(
        effective_tag,
        clip.label,
        start_id,
        start_sec,
        end_sec,
    )

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


def validate_output_template(template: str) -> None:
    if not template.strip():
        raise ValueError("Output template cannot be empty")
    formatter = Formatter()
    for _, field_name, format_spec, conversion in formatter.parse(template):
        if field_name is None:
            continue
        if not field_name:
            raise ValueError("Output template contains empty fields")
        if field_name not in _TEMPLATE_FIELDS:
            raise ValueError(f"Unknown output field: {field_name}")
        if format_spec or conversion:
            raise ValueError("Output fields do not support format specifiers")


def format_output_basename(
    template: str,
    clip: ResolvedClip,
    *,
    title: str | None = None,
) -> str:
    validate_output_template(template)
    tag = clip.display_tag or clip.clip.tag or ""
    label = clip.clip.label or ""
    rotation = clip.clip.rotation or ""
    score = clip.clip.score or ""
    opponent = clip.clip.opponent or ""
    serve_target = clip.clip.serve_target or ""
    values = _SafeDict(
        tag=tag,
        videoid=clip.video_id,
        start=format_seconds(clip.start_sec),
        end=format_seconds(clip.end_sec),
        title=title or "",
        label=label,
        rotation=rotation,
        score=score,
        opponent=opponent,
        serve_target=serve_target,
        serve=serve_target,
    )
    raw = Formatter().vformat(template, (), values)
    if clip.clip.label and not _template_uses_field(template, "label"):
        raw = f"{raw}_{clip.clip.label}" if raw else clip.clip.label
    return _normalize_basename(raw)


def _build_output_filename(
    tag: str | None,
    label: str | None,
    video_id: str,
    start_sec: float,
    end_sec: float,
) -> str:
    parts: list[str] = []
    if tag:
        parts.append(tag)
    if label:
        parts.append(label)
    parts.append(video_id)
    parts.append(f"{format_seconds(start_sec)}-{format_seconds(end_sec)}")
    base = "_".join(parts)
    return _normalize_basename(base)


def _sanitize_basename(name: str) -> str:
    cleaned = []
    for char in name:
        if char in _INVALID_FILENAME_CHARS or ord(char) < 32:
            cleaned.append("_")
        else:
            cleaned.append(char)

    sanitized = "".join(cleaned)
    sanitized = _WHITESPACE_RE.sub("_", sanitized)
    sanitized = sanitized.strip(" ._-")
    return sanitized or "clip"


def _normalize_basename(name: str) -> str:
    sanitized = _sanitize_basename(name)
    collapsed = _MULTI_UNDERSCORE_RE.sub("_", sanitized)
    collapsed = collapsed.strip("._-")
    return collapsed or "clip"


def _template_uses_field(template: str, field: str) -> bool:
    formatter = Formatter()
    for _, field_name, _, _ in formatter.parse(template):
        if field_name == field:
            return True
    return False


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
