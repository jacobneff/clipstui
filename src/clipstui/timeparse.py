from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

_HMS_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$")
_SECONDS_RE = re.compile(r"^\d+(?:\.\d+)?s?$")


def convert_time_token_to_seconds(token: str) -> float:
    token = token.strip().lower()
    if not token:
        raise ValueError("Empty time token")

    if ":" in token:
        parts = token.split(":")
        if len(parts) == 2:
            minutes_text, seconds_text = parts
            hours_text = "0"
        elif len(parts) == 3:
            hours_text, minutes_text, seconds_text = parts
        else:
            raise ValueError(f"Invalid time token: {token}")

        if not (hours_text.isdigit() and minutes_text.isdigit()):
            raise ValueError(f"Invalid time token: {token}")
        seconds_val = _parse_decimal_seconds(seconds_text)
        hours_val = int(hours_text)
        minutes_val = int(minutes_text)
        return _round_seconds(hours_val * 3600 + minutes_val * 60 + seconds_val)

    if _SECONDS_RE.match(token):
        return _round_seconds(_parse_decimal_seconds(token[:-1] if token.endswith("s") else token))

    match = _HMS_RE.match(token)
    if match and any(match.groups()):
        hours_val = int(match.group(1) or 0)
        minutes_val = int(match.group(2) or 0)
        seconds_val = float(match.group(3) or 0)
        return _round_seconds(hours_val * 3600 + minutes_val * 60 + seconds_val)

    raise ValueError(f"Invalid time token: {token}")


def parse_time_delta(token: str) -> float:
    token = token.strip()
    if not token:
        raise ValueError("Empty time token")
    if token[0] not in {"+", "-"}:
        raise ValueError("Time delta must start with + or -")
    sign = -1.0 if token[0] == "-" else 1.0
    seconds = convert_time_token_to_seconds(token[1:])
    return _round_seconds(sign * seconds)


def format_seconds(value: float) -> str:
    if value != value:
        return "0"
    rounded = _round_seconds(value)
    text = f"{rounded:.3f}"
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def get_seconds_from_url(url: str) -> float:
    parsed = urlparse(url)
    query_token = _extract_time_token(parse_qs(parsed.query))
    if query_token is None and parsed.fragment:
        query_token = _extract_time_token(parse_qs(parsed.fragment))

    if query_token is None:
        raise ValueError("Missing t or start parameter")

    return convert_time_token_to_seconds(query_token)


def _extract_time_token(query: dict[str, list[str]]) -> str | None:
    for key in ("t", "start"):
        values = query.get(key)
        if values:
            return values[0]
    return None


def _parse_decimal_seconds(value: str) -> float:
    try:
        return float(Decimal(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid time token: {value}") from exc


def _round_seconds(value: float) -> float:
    return round(value, 3)
