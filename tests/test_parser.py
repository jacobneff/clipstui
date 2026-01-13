import pytest

from clipstui.parser import ClipSpec, format_clip_file, parse_clip_file


def test_parse_single_clip() -> None:
    text = "CLIP\nhttps://example.com/?t=10\nhttps://example.com/?t=20\n"
    clips = parse_clip_file(text)
    assert clips == [
        ClipSpec(
            start_url="https://example.com/?t=10",
            end_url="https://example.com/?t=20",
            tag=None,
        )
    ]


def test_parse_tag_with_comments_and_whitespace() -> None:
    text = """
    # leading comment
    CLIP   highlight reel
    # between start/end
      https://example.com/?t=5
    # another comment
    https://example.com/?t=15
    """
    clips = parse_clip_file(text)
    assert clips == [
        ClipSpec(
            start_url="https://example.com/?t=5",
            end_url="https://example.com/?t=15",
            tag="highlight reel",
        )
    ]


def test_parse_multiple_clips_with_gaps() -> None:
    text = """
    CLIP
    https://example.com/?t=1
    https://example.com/?t=2

    # gap
    CLIP tag-2
    https://example.com/?t=3
    https://example.com/?t=4
    """
    clips = parse_clip_file(text)
    assert clips == [
        ClipSpec(
            start_url="https://example.com/?t=1",
            end_url="https://example.com/?t=2",
            tag=None,
        ),
        ClipSpec(
            start_url="https://example.com/?t=3",
            end_url="https://example.com/?t=4",
            tag="tag-2",
        ),
    ]


def test_parse_bom() -> None:
    text = "\ufeffCLIP\nhttps://example.com/?t=8\nhttps://example.com/?t=9\n"
    clips = parse_clip_file(text)
    assert clips == [
        ClipSpec(
            start_url="https://example.com/?t=8",
            end_url="https://example.com/?t=9",
            tag=None,
        )
    ]


def test_parse_pad_line() -> None:
    text = """
    CLIP demo
    PAD 2 3
    https://example.com/?t=10
    https://example.com/?t=20
    """
    clips = parse_clip_file(text)
    assert clips == [
        ClipSpec(
            start_url="https://example.com/?t=10",
            end_url="https://example.com/?t=20",
            tag="demo",
            pad_before=2,
            pad_after=3,
        )
    ]


def test_parse_pad_single_value() -> None:
    text = """
    CLIP
    PAD 4
    https://example.com/?t=1
    https://example.com/?t=2
    """
    clips = parse_clip_file(text)
    assert clips == [
        ClipSpec(
            start_url="https://example.com/?t=1",
            end_url="https://example.com/?t=2",
            tag=None,
            pad_before=4,
            pad_after=0,
        )
    ]


def test_format_clip_file_includes_pad() -> None:
    clip = ClipSpec(
        start_url="https://example.com/?t=3",
        end_url="https://example.com/?t=4",
        tag="pad",
        pad_before=1,
        pad_after=2,
    )
    text = format_clip_file([clip])
    assert "PAD 1 2" in text


def test_missing_start_url_raises() -> None:
    with pytest.raises(ValueError, match="CLIP missing start URL"):
        parse_clip_file("CLIP\n# comment only\n")


def test_missing_end_url_raises() -> None:
    with pytest.raises(ValueError, match="CLIP missing end URL"):
        parse_clip_file("CLIP\nhttps://example.com/?t=1\n# comment only\n")


def test_unexpected_content_raises() -> None:
    with pytest.raises(ValueError, match="Unexpected content"):
        parse_clip_file("https://example.com/?t=1\n")
