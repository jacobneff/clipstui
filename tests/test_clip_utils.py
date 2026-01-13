from clipstui.clip_utils import (
    AutoTagOptions,
    OverlapKind,
    analyze_overlaps,
    compute_auto_tags,
    group_clips_by_video,
    plan_adjacent_merges,
    resolve_clips,
)
from clipstui.parser import ClipSpec
from clipstui.resolve import resolve_clip


def test_compute_auto_tags_global() -> None:
    specs = [
        ClipSpec(
            start_url="https://www.youtube.com/watch?v=abc123&t=10",
            end_url="https://www.youtube.com/watch?v=abc123&t=12",
            tag=None,
        ),
        ClipSpec(
            start_url="https://www.youtube.com/watch?v=abc123&t=13",
            end_url="https://www.youtube.com/watch?v=abc123&t=15",
            tag="C001",
        ),
        ClipSpec(
            start_url="https://www.youtube.com/watch?v=abc123&t=16",
            end_url="https://www.youtube.com/watch?v=abc123&t=18",
            tag=None,
        ),
    ]
    tags = compute_auto_tags(specs, AutoTagOptions())
    assert tags == ["C002", "C001", "C003"]


def test_compute_auto_tags_prefix_by_video() -> None:
    specs = [
        ClipSpec(
            start_url="https://www.youtube.com/watch?v=aaa111&t=10",
            end_url="https://www.youtube.com/watch?v=aaa111&t=12",
            tag=None,
        ),
        ClipSpec(
            start_url="https://www.youtube.com/watch?v=aaa111&t=13",
            end_url="https://www.youtube.com/watch?v=aaa111&t=15",
            tag=None,
        ),
        ClipSpec(
            start_url="https://www.youtube.com/watch?v=bbb222&t=10",
            end_url="https://www.youtube.com/watch?v=bbb222&t=12",
            tag=None,
        ),
    ]
    tags = compute_auto_tags(specs, AutoTagOptions(prefix_by_video=True))
    assert tags == ["aaa111_C001", "aaa111_C002", "bbb222_C001"]


def test_resolve_clips_auto_tag_output_name() -> None:
    specs = [
        ClipSpec(
            start_url="https://www.youtube.com/watch?v=abc123&t=10",
            end_url="https://www.youtube.com/watch?v=abc123&t=12",
            tag=None,
        )
    ]
    resolved = resolve_clips(specs, pad_before=0, pad_after=0, auto_tag=AutoTagOptions())
    assert resolved[0].display_tag == "C001"
    assert resolved[0].output_name.startswith("C001_abc123_")


def test_group_clips_by_video() -> None:
    clips = [
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=abc123&t=10",
                end_url="https://www.youtube.com/watch?v=abc123&t=12",
            ),
            pad_before=0,
            pad_after=0,
        ),
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=xyz789&t=10",
                end_url="https://www.youtube.com/watch?v=xyz789&t=15",
            ),
            pad_before=0,
            pad_after=0,
        ),
    ]
    groups = group_clips_by_video(clips)
    assert len(groups) == 2
    assert groups[0].video_id == "abc123"
    assert groups[0].total_duration == 2
    assert groups[1].video_id == "xyz789"


def test_analyze_overlaps_detects_duplicate() -> None:
    clips = [
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=abc123&t=10",
                end_url="https://www.youtube.com/watch?v=abc123&t=20",
            ),
            pad_before=0,
            pad_after=0,
        ),
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=abc123&t=10",
                end_url="https://www.youtube.com/watch?v=abc123&t=20",
            ),
            pad_before=0,
            pad_after=0,
        ),
    ]
    findings = analyze_overlaps(clips)
    assert len(findings) == 1
    assert findings[0].kind == OverlapKind.DUPLICATE


def test_analyze_overlaps_detects_heavy_overlap() -> None:
    clips = [
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=abc123&t=10",
                end_url="https://www.youtube.com/watch?v=abc123&t=20",
            ),
            pad_before=0,
            pad_after=0,
        ),
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=abc123&t=15",
                end_url="https://www.youtube.com/watch?v=abc123&t=19",
            ),
            pad_before=0,
            pad_after=0,
        ),
    ]
    findings = analyze_overlaps(clips, heavy_overlap_ratio=0.8)
    assert len(findings) == 1
    assert findings[0].kind == OverlapKind.OVERLAP


def test_plan_adjacent_merges() -> None:
    clips = [
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=abc123&t=0",
                end_url="https://www.youtube.com/watch?v=abc123&t=10",
            ),
            pad_before=0,
            pad_after=0,
        ),
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=abc123&t=10.5",
                end_url="https://www.youtube.com/watch?v=abc123&t=12",
            ),
            pad_before=0,
            pad_after=0,
        ),
        resolve_clip(
            ClipSpec(
                start_url="https://www.youtube.com/watch?v=xyz789&t=0",
                end_url="https://www.youtube.com/watch?v=xyz789&t=1",
            ),
            pad_before=0,
            pad_after=0,
        ),
    ]
    merges = plan_adjacent_merges(clips, gap_threshold=1.0)
    assert len(merges) == 1
    assert merges[0].video_id == "abc123"
