import pytest

from clipstui.parser import ClipSpec
from clipstui.resolve import resolve_clip


def test_resolve_clip_padding_and_output() -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=abc123&t=20",
        tag="demo",
    )
    resolved = resolve_clip(clip, pad_before=2, pad_after=3)
    assert resolved.start_sec == 10
    assert resolved.end_sec == 20
    assert resolved.cut_start == 8
    assert resolved.cut_end == 23
    assert resolved.video_id == "abc123"
    assert resolved.output_name == "demo_abc123_10-20"


def test_resolve_clip_uses_pad_overrides() -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=abc123&t=20",
        tag=None,
        pad_before=5,
        pad_after=1,
    )
    resolved = resolve_clip(clip, pad_before=0, pad_after=0)
    assert resolved.cut_start == 5
    assert resolved.cut_end == 21


def test_resolve_clip_sanitizes_filename() -> None:
    clip = ClipSpec(
        start_url="https://youtu.be/abc123?t=1",
        end_url="https://youtu.be/abc123?t=2",
        tag="bad:tag*name",
    )
    resolved = resolve_clip(clip, pad_before=0, pad_after=0)
    assert resolved.output_name == "bad_tag_name_abc123_1-2"


def test_resolve_clip_mismatched_video_ids() -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=xyz789&t=20",
        tag=None,
    )
    with pytest.raises(ValueError, match="different videos"):
        resolve_clip(clip, pad_before=0, pad_after=0)


def test_resolve_clip_decimal_times() -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=1.5",
        end_url="https://www.youtube.com/watch?v=abc123&t=2.5",
        tag="demo",
    )
    resolved = resolve_clip(clip, pad_before=0, pad_after=0)
    assert resolved.start_sec == 1.5
    assert resolved.end_sec == 2.5
    assert resolved.output_name == "demo_abc123_1.5-2.5"
