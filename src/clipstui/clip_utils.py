from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .parser import ClipSpec
from .resolve import ResolvedClip, extract_video_id, resolve_clip


@dataclass(frozen=True)
class AutoTagOptions:
    prefix_by_video: bool = False
    width: int = 3
    base_prefix: str = "C"
    prefix_separator: str = "_"


@dataclass(frozen=True)
class ClipGroup:
    video_id: str
    clips: list[ResolvedClip]
    total_duration: float


class OverlapKind(Enum):
    DUPLICATE = "duplicate"
    OVERLAP = "overlap"


@dataclass(frozen=True)
class OverlapFinding:
    video_id: str
    first: ResolvedClip
    second: ResolvedClip
    overlap_seconds: float
    overlap_ratio: float
    kind: OverlapKind


@dataclass(frozen=True)
class MergeSuggestion:
    video_id: str
    first: ResolvedClip
    second: ResolvedClip
    gap_seconds: float
    merged_start: float
    merged_end: float


def resolve_clips(
    specs: list[ClipSpec],
    pad_before: int,
    pad_after: int,
    *,
    auto_tag: AutoTagOptions | None = None,
) -> list[ResolvedClip]:
    tags = compute_auto_tags(specs, auto_tag) if auto_tag else [spec.tag for spec in specs]
    resolved: list[ResolvedClip] = []
    for spec, tag in zip(specs, tags, strict=True):
        resolved.append(
            resolve_clip(spec, pad_before, pad_after, tag_override=tag)
        )
    return resolved


def compute_auto_tags(
    specs: list[ClipSpec],
    options: AutoTagOptions | None,
) -> list[str | None]:
    if options is None:
        return [spec.tag for spec in specs]
    seen = {spec.tag.casefold() for spec in specs if spec.tag}
    assigned = set(seen)
    counters: dict[str, int] = {}
    result: list[str | None] = []

    for spec in specs:
        if spec.tag:
            result.append(spec.tag)
            continue
        if options.prefix_by_video:
            video_id = extract_video_id(spec.start_url)
            counter_key = video_id
        else:
            video_id = ""
            counter_key = "__all__"
        counters[counter_key] = counters.get(counter_key, 0) + 1
        tag = _next_auto_tag(
            counters,
            counter_key,
            assigned,
            options,
            video_id,
        )
        assigned.add(tag.casefold())
        result.append(tag)

    return result


def group_clips_by_video(clips: list[ResolvedClip]) -> list[ClipGroup]:
    groups: dict[str, list[ResolvedClip]] = {}
    for clip in clips:
        groups.setdefault(clip.video_id, []).append(clip)
    result: list[ClipGroup] = []
    for video_id, items in groups.items():
        total = sum(_duration(item) for item in items)
        result.append(ClipGroup(video_id=video_id, clips=items, total_duration=total))
    return result


def analyze_overlaps(
    clips: list[ResolvedClip],
    *,
    heavy_overlap_ratio: float = 0.8,
    duplicate_tolerance: float = 0.001,
) -> list[OverlapFinding]:
    findings: list[OverlapFinding] = []
    for video_id, items in _group_items(clips).items():
        ordered = sorted(items, key=lambda clip: clip.start_sec)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                if second.start_sec > first.end_sec:
                    break
                overlap = _overlap_seconds(first, second)
                if overlap <= 0:
                    continue
                if _is_duplicate(first, second, duplicate_tolerance):
                    findings.append(
                        OverlapFinding(
                            video_id=video_id,
                            first=first,
                            second=second,
                            overlap_seconds=overlap,
                            overlap_ratio=1.0,
                            kind=OverlapKind.DUPLICATE,
                        )
                    )
                    continue
                ratio = overlap / min(_duration(first), _duration(second))
                if ratio >= heavy_overlap_ratio:
                    findings.append(
                        OverlapFinding(
                            video_id=video_id,
                            first=first,
                            second=second,
                            overlap_seconds=overlap,
                            overlap_ratio=ratio,
                            kind=OverlapKind.OVERLAP,
                        )
                    )
    return findings


def plan_adjacent_merges(
    clips: list[ResolvedClip],
    *,
    gap_threshold: float = 1.0,
) -> list[MergeSuggestion]:
    suggestions: list[MergeSuggestion] = []
    for first, second in zip(clips, clips[1:], strict=False):
        if first.video_id != second.video_id:
            continue
        gap = second.start_sec - first.end_sec
        if gap > gap_threshold:
            continue
        merged_start = min(first.start_sec, second.start_sec)
        merged_end = max(first.end_sec, second.end_sec)
        suggestions.append(
            MergeSuggestion(
                video_id=first.video_id,
                first=first,
                second=second,
                gap_seconds=gap,
                merged_start=merged_start,
                merged_end=merged_end,
            )
        )
    return suggestions


def _duration(clip: ResolvedClip) -> float:
    return max(0.0, clip.end_sec - clip.start_sec)


def _group_items(clips: list[ResolvedClip]) -> dict[str, list[ResolvedClip]]:
    grouped: dict[str, list[ResolvedClip]] = {}
    for clip in clips:
        grouped.setdefault(clip.video_id, []).append(clip)
    return grouped


def _overlap_seconds(first: ResolvedClip, second: ResolvedClip) -> float:
    start = max(first.start_sec, second.start_sec)
    end = min(first.end_sec, second.end_sec)
    return max(0.0, end - start)


def _is_duplicate(first: ResolvedClip, second: ResolvedClip, tol: float) -> bool:
    return abs(first.start_sec - second.start_sec) <= tol and abs(first.end_sec - second.end_sec) <= tol


def _next_auto_tag(
    counters: dict[str, int],
    key: str,
    assigned: set[str],
    options: AutoTagOptions,
    video_id: str,
) -> str:
    while True:
        number = counters.get(key, 1)
        base = f"{options.base_prefix}{number:0{options.width}d}"
        if options.prefix_by_video:
            tag = f"{video_id}{options.prefix_separator}{base}"
        else:
            tag = base
        if tag.casefold() not in assigned:
            return tag
        counters[key] = number + 1
