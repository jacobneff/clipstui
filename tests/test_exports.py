import csv
import json
from pathlib import Path

from clipstui.exports import (
    build_concat_list,
    build_manifest_entries,
    manifest_to_csv,
    manifest_to_json,
)
from clipstui.metadata import VideoMetadata
from clipstui.parser import ClipSpec
from clipstui.resolve import resolve_clip


def _sample_clip() -> ClipSpec:
    return ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=abc123&t=20",
        tag="C001",
    )


def test_build_manifest_entries_with_template(tmp_path: Path) -> None:
    resolved = resolve_clip(_sample_clip(), pad_before=1, pad_after=2)
    output_dir = tmp_path / "out"
    metadata = {
        "abc123": VideoMetadata(
            video_id="abc123",
            title="My Title",
            uploader="Coach",
            duration=120,
            thumbnail_url=None,
            webpage_url="https://example.com/watch?v=abc123",
        )
    }
    entries = build_manifest_entries(
        [resolved],
        output_dir=output_dir,
        output_format="mp4",
        output_template="{tag}_{start}-{end}_{videoid}_{title}",
        metadata=metadata,
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.output_name == "C001_10-20_abc123_My_Title"
    assert entry.output_file == "C001_10-20_abc123_My_Title.mp4"
    assert entry.output_path == output_dir / "C001_10-20_abc123_My_Title.mp4"
    assert entry.duration == 10.0
    assert entry.cut_start == 9.0
    assert entry.cut_end == 22.0
    assert entry.pad_before == 1.0
    assert entry.pad_after == 2.0


def test_build_manifest_entries_invalid_template_fallback(tmp_path: Path) -> None:
    resolved = resolve_clip(_sample_clip(), pad_before=0, pad_after=0)
    entries = build_manifest_entries(
        [resolved],
        output_dir=tmp_path,
        output_format="mp4",
        output_template="{bad}",
        metadata={},
    )
    assert entries[0].output_name == resolved.output_name


def test_manifest_entries_include_context_fields(tmp_path: Path) -> None:
    clip = ClipSpec(
        start_url="https://www.youtube.com/watch?v=abc123&t=10",
        end_url="https://www.youtube.com/watch?v=abc123&t=20",
        tag="C001",
        label="K",
        score="22-20",
        opponent="Old Dominion",
    )
    resolved = resolve_clip(clip, pad_before=0, pad_after=0)
    entries = build_manifest_entries(
        [resolved],
        output_dir=tmp_path,
        output_format="mp4",
        output_template="{tag}_{videoid}_{start}-{end}",
        metadata={},
    )
    entry = entries[0]
    assert entry.label == "K"
    assert entry.score == "22-20"
    assert entry.opponent == "Old Dominion"


def test_manifest_to_csv() -> None:
    resolved = resolve_clip(_sample_clip(), pad_before=0, pad_after=0)
    entries = build_manifest_entries(
        [resolved],
        output_dir=Path("out"),
        output_format="mp4",
        output_template="{tag}_{videoid}_{start}-{end}",
        metadata={},
    )
    text = manifest_to_csv(entries)
    rows = list(csv.DictReader(text.splitlines()))
    assert rows[0]["video_id"] == "abc123"
    assert rows[0]["start_sec"] == "10"
    assert rows[0]["end_sec"] == "20"
    assert rows[0]["output_file"].endswith(".mp4")


def test_manifest_to_json() -> None:
    resolved = resolve_clip(_sample_clip(), pad_before=0, pad_after=0)
    output_dir = Path("out")
    entries = build_manifest_entries(
        [resolved],
        output_dir=output_dir,
        output_format="mp4",
        output_template="{tag}_{videoid}_{start}-{end}",
        metadata={},
    )
    payload = json.loads(
        manifest_to_json(
            entries,
            output_dir=output_dir,
            output_format="mp4",
            output_template="{tag}_{videoid}_{start}-{end}",
        )
    )
    assert payload["count"] == 1
    assert payload["output_dir"] == str(output_dir)
    assert payload["clips"][0]["video_id"] == "abc123"


def test_build_concat_list_escapes_quotes() -> None:
    paths = [
        Path("C:/Videos/it's ok.mp4"),
        Path("C:/Videos/clip.mp4"),
    ]
    text = build_concat_list(paths)
    assert "file 'C:/Videos/it\\'s ok.mp4'" in text
    assert "file 'C:/Videos/clip.mp4'" in text
