from clipstui.clip_time import coerce_time_input, extract_youtube_urls


def test_coerce_time_input_token_uses_base_url() -> None:
    base_url = "https://www.youtube.com/watch?v=abc123&t=10"
    url, seconds = coerce_time_input("1:02", base_url=base_url)
    assert seconds == 62
    assert "t=62" in url


def test_coerce_time_input_delta() -> None:
    base_url = "https://www.youtube.com/watch?v=abc123&t=10"
    url, seconds = coerce_time_input("+2.5", base_url=base_url)
    assert seconds == 12.5
    assert "t=12.5" in url


def test_extract_youtube_urls() -> None:
    text = "watch https://youtu.be/abc123?t=1 and https://example.com"
    urls = extract_youtube_urls(text)
    assert urls == ["https://youtu.be/abc123?t=1"]
