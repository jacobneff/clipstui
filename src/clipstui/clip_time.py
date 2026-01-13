from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .timeparse import (
    convert_time_token_to_seconds,
    format_seconds,
    get_seconds_from_url,
    parse_time_delta,
)

_URL_RE = re.compile(r"https?://[^\s]+")


def looks_like_url(value: str) -> bool:
    value = value.strip()
    if "://" in value:
        return True
    return "youtu" in value.lower()


def extract_youtube_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in _URL_RE.findall(text):
        cleaned = match.strip("()[]{}<>\"'.,")
        if "youtu" in cleaned.lower():
            urls.append(cleaned)
    return urls


def coerce_time_input(
    value: str,
    *,
    base_url: str | None,
    base_seconds: float | None = None,
) -> tuple[str, float]:
    text = value.strip()
    if not text:
        raise ValueError("Missing time or URL")
    if looks_like_url(text):
        seconds = get_seconds_from_url(text)
        return (text, seconds)

    if text[0] in {"+", "-"}:
        if base_seconds is None and base_url is not None:
            base_seconds = get_seconds_from_url(base_url)
        if base_seconds is None:
            raise ValueError("Delta requires a base time")
        delta = parse_time_delta(text)
        seconds = max(0.0, base_seconds + delta)
    else:
        seconds = convert_time_token_to_seconds(text)

    if base_url is None:
        raise ValueError("Time tokens require a base URL")
    return (replace_url_time(base_url, seconds), seconds)


def replace_url_time(url: str, seconds: float) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    key = "t" if "t" in query or "start" not in query else "start"
    query.pop("t", None)
    query.pop("start", None)
    query[key] = [format_seconds(seconds)]
    cleaned = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=cleaned, fragment=""))
