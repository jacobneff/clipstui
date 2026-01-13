from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_path

APP_NAME = "clipstui"


def cache_root() -> Path:
    root = user_cache_path(APP_NAME)
    root.mkdir(parents=True, exist_ok=True)
    return root


def metadata_cache_dir() -> Path:
    path = cache_root() / "metadata"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbs_cache_dir() -> Path:
    path = cache_root() / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path
