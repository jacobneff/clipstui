from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import config_path

CONFIG_VERSION = 1


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    output_dir: str | None = None
    output_format: str | None = None
    output_template: str | None = None
    pad_before_default: int | None = None
    pad_after_default: int | None = None
    tree_root: str | None = None
    show_hidden: bool | None = None
    auto_tag_prefix: bool | None = None


def load_config(path: Path | None = None) -> tuple[AppConfig, str | None]:
    path = path or config_path()
    if not path.exists():
        return AppConfig(), None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AppConfig(), f"Failed to read config: {path} ({exc})"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return AppConfig(), f"Config file is not valid JSON: {path}"
    if not isinstance(data, dict):
        return AppConfig(), f"Config file must be a JSON object: {path}"
    return _parse_config_data(data), None


def save_config(config: AppConfig, path: Path | None = None) -> str | None:
    path = path or config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Failed to create config directory: {path.parent} ({exc})"
    payload = _config_to_dict(config)
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        return f"Failed to write config: {path} ({exc})"
    return None


def _parse_config_data(data: dict[str, Any]) -> AppConfig:
    return AppConfig(
        version=_as_int(data.get("version")) or CONFIG_VERSION,
        output_dir=_as_str(data.get("output_dir")),
        output_format=_as_str(data.get("output_format")),
        output_template=_as_str(data.get("output_template")),
        pad_before_default=_as_nonneg_int(data.get("pad_before_default")),
        pad_after_default=_as_nonneg_int(data.get("pad_after_default")),
        tree_root=_as_str(data.get("tree_root")),
        show_hidden=_as_bool(data.get("show_hidden")),
        auto_tag_prefix=_as_bool(data.get("auto_tag_prefix")),
    )


def _config_to_dict(config: AppConfig) -> dict[str, Any]:
    data: dict[str, Any] = {"version": config.version}
    _set_if(data, "output_dir", config.output_dir)
    _set_if(data, "output_format", config.output_format)
    _set_if(data, "output_template", config.output_template)
    _set_if(data, "pad_before_default", config.pad_before_default)
    _set_if(data, "pad_after_default", config.pad_after_default)
    _set_if(data, "tree_root", config.tree_root)
    _set_if(data, "show_hidden", config.show_hidden)
    _set_if(data, "auto_tag_prefix", config.auto_tag_prefix)
    return data


def _set_if(data: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        data[key] = value


def _as_str(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_nonneg_int(value: Any) -> int | None:
    number = _as_int(value)
    if number is None or number < 0:
        return None
    return number
