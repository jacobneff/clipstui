from pathlib import Path

from clipstui.thumbs import download_thumbnail


def test_download_thumbnail_cached(tmp_path: Path) -> None:
    calls = 0

    def fetch(_: str) -> bytes:
        nonlocal calls
        calls += 1
        return b"image"

    url = "https://example.com/thumb.jpg"
    path1 = download_thumbnail(url, "abc123", cache_dir=tmp_path, fetcher=fetch)
    assert path1.exists()
    path2 = download_thumbnail(url, "abc123", cache_dir=tmp_path, fetcher=fetch)
    assert path1 == path2
    assert calls == 1


def test_download_thumbnail_default_extension(tmp_path: Path) -> None:
    def fetch(_: str) -> bytes:
        return b"image"

    url = "https://example.com/thumb"
    path = download_thumbnail(url, "xyz789", cache_dir=tmp_path, fetcher=fetch)
    assert path.name == "xyz789.jpg"
