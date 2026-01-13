import pytest

from clipstui.timeparse import convert_time_token_to_seconds, get_seconds_from_url, parse_time_delta


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("1149", 1149),
        ("1149s", 1149),
        ("19:09", 19 * 60 + 9),
        ("1:02:03", 1 * 3600 + 2 * 60 + 3),
        ("1h2m3s", 1 * 3600 + 2 * 60 + 3),
    ],
)
def test_convert_time_token_to_seconds(token: str, expected: int) -> None:
    assert convert_time_token_to_seconds(token) == expected


def test_get_seconds_from_url_t_param() -> None:
    url = "https://www.youtube.com/watch?v=abc123&t=1h2m3s"
    assert get_seconds_from_url(url) == 3723


def test_get_seconds_from_url_start_param() -> None:
    url = "https://www.youtube.com/watch?v=abc123&start=90"
    assert get_seconds_from_url(url) == 90


def test_get_seconds_from_url_fragment() -> None:
    url = "https://youtu.be/abc123#t=19:09"
    assert get_seconds_from_url(url) == 1149


def test_get_seconds_from_url_missing_t() -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    with pytest.raises(ValueError, match="Missing t or start parameter"):
        get_seconds_from_url(url)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("2.5", 2.5),
        ("2.5s", 2.5),
        ("1:02.5", 62.5),
        ("+2.5", 2.5),
        ("-1", -1.0),
    ],
)
def test_convert_time_token_decimal_and_delta(token: str, expected: float) -> None:
    if token.startswith(("+", "-")):
        assert parse_time_delta(token) == expected
    else:
        assert convert_time_token_to_seconds(token) == expected
