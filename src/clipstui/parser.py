from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class ClipSpec:
    start_url: str
    end_url: str
    tag: str | None = None
    label: str | None = None
    rotation: str | None = None
    score: str | None = None
    opponent: str | None = None
    serve_target: str | None = None
    pad_before: int | None = None
    pad_after: int | None = None


def parse_clip_file(text: str) -> list[ClipSpec]:
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    clips: list[ClipSpec] = []
    index = 0

    while index < len(lines):
        raw = lines[index]
        index += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        parts = stripped.split(maxsplit=1)
        if parts[0] != "CLIP":
            raise ValueError(f"Unexpected content on line {index}: {stripped}")

        tag, fields = _parse_clip_header(stripped, index)
        pad_before = None
        pad_after = None

        start_line, index = _next_data_line(lines, index, "CLIP missing start URL")
        if _is_pad_line(start_line):
            pad_before, pad_after = _parse_pad_line(start_line, index)
            start_url, index = _next_data_line(lines, index, "CLIP missing start URL")
        else:
            start_url = start_line
        end_url, index = _next_data_line(lines, index, "CLIP missing end URL")
        clips.append(
            ClipSpec(
                start_url=start_url,
                end_url=end_url,
                tag=tag,
                label=fields.get("label"),
                rotation=fields.get("rotation"),
                score=fields.get("score"),
                opponent=fields.get("opponent"),
                serve_target=fields.get("serve_target"),
                pad_before=pad_before,
                pad_after=pad_after,
            )
        )

    return clips


def _next_data_line(lines: list[str], index: int, message: str) -> tuple[str, int]:
    while index < len(lines):
        raw = lines[index]
        index += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped, index

    raise ValueError(message)


def _is_pad_line(value: str) -> bool:
    parts = value.strip().split(maxsplit=1)
    return bool(parts) and parts[0].upper() == "PAD"


def _parse_pad_line(line: str, line_no: int) -> tuple[int, int]:
    parts = line.strip().split()
    if len(parts) not in {2, 3}:
        raise ValueError(f"Invalid PAD line on line {line_no}: {line}")
    try:
        pad_before = int(parts[1])
        pad_after = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"Invalid PAD values on line {line_no}: {line}") from exc
    if pad_before < 0 or pad_after < 0:
        raise ValueError(f"PAD values must be non-negative on line {line_no}: {line}")
    return (pad_before, pad_after)


def format_clip_file(clips: list[ClipSpec]) -> str:
    lines: list[str] = []
    for clip in clips:
        lines.append(_format_clip_header(clip))
        if clip.pad_before is not None or clip.pad_after is not None:
            before = clip.pad_before or 0
            after = clip.pad_after or 0
            lines.append(f"PAD {before} {after}")
        lines.append(clip.start_url)
        lines.append(clip.end_url)
        lines.append("")
    if not lines:
        return ""
    return "\n".join(lines).rstrip() + "\n"


_CLIP_FIELD_ALIASES = {
    "label": "label",
    "rotation": "rotation",
    "score": "score",
    "opponent": "opponent",
    "serve_target": "serve_target",
    "serve": "serve_target",
}


def _parse_clip_header(line: str, line_no: int) -> tuple[str | None, dict[str, str | None]]:
    rest = line.strip()[len("CLIP") :].strip()
    if not rest:
        return None, {}
    if "=" not in rest:
        return rest, {}
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise ValueError(f"Invalid CLIP header on line {line_no}: {exc}") from exc
    tag_parts: list[str] = []
    fields: dict[str, str | None] = {}
    saw_key = False
    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
            if not key:
                raise ValueError(f"Invalid CLIP field on line {line_no}: {token}")
            field = _CLIP_FIELD_ALIASES.get(key.lower())
            if field is None:
                raise ValueError(f"Unknown CLIP field '{key}' on line {line_no}")
            normalized = value.strip()
            fields[field] = normalized if normalized else None
            saw_key = True
        else:
            if saw_key:
                raise ValueError(
                    f"Unexpected token in CLIP header on line {line_no}: {token}"
                )
            tag_parts.append(token)
    tag = " ".join(tag_parts) if tag_parts else None
    return tag, fields


_CLIP_FIELD_ORDER = [
    ("label", "label"),
    ("rotation", "rotation"),
    ("score", "score"),
    ("opponent", "opponent"),
    ("serve_target", "serve_target"),
]


def _format_clip_header(clip: ClipSpec) -> str:
    parts = ["CLIP"]
    if clip.tag:
        parts.append(clip.tag)
    for key, attr in _CLIP_FIELD_ORDER:
        value = getattr(clip, attr)
        if value is None or value == "":
            continue
        parts.append(f"{key}={_quote_if_needed(str(value))}")
    return " ".join(parts)


def _quote_if_needed(value: str) -> str:
    if any(ch.isspace() for ch in value) or any(ch in "\"'" for ch in value):
        return shlex.quote(value)
    return value
