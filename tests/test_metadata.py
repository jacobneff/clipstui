import json
import subprocess
from pathlib import Path

from clipstui.metadata import get_metadata


def test_get_metadata_uses_cache(tmp_path: Path) -> None:
    calls = 0
    payload = {
        "id": "abc123",
        "title": "Match clip",
        "uploader": "Example",
        "duration": 120,
        "thumbnail": "https://example.com/thumb.jpg",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
    }

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    url = "https://www.youtube.com/watch?v=abc123&t=1"
    meta = get_metadata(url, cache_dir=tmp_path, runner=runner)
    assert meta.video_id == "abc123"
    assert meta.title == "Match clip"
    assert calls == 1

    meta_again = get_metadata(url, cache_dir=tmp_path, runner=runner)
    assert meta_again.video_id == "abc123"
    assert calls == 1
