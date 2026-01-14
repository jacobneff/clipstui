from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .resolve import DEFAULT_OUTPUT_TEMPLATE


@dataclass(frozen=True)
class PresetProfile:
    name: str
    description: str
    pad_before: int | None = None
    pad_after: int | None = None
    output_format: str | None = None
    output_dir: Path | None = None
    output_template: str | None = None


_PRESETS = [
    PresetProfile(
        name="Volleyball highlights",
        description="Tighter pads, title-rich filenames",
        pad_before=2,
        pad_after=2,
        output_format="mp4",
        output_dir=Path("highlights"),
        output_template="{tag}_{start}-{end}_{videoid}_{title}",
    ),
    PresetProfile(
        name="Full rally",
        description="No extra pad, plain filenames",
        pad_before=0,
        pad_after=0,
        output_format="mp4",
        output_dir=Path("rallies"),
        output_template=DEFAULT_OUTPUT_TEMPLATE,
    ),
]


def list_presets() -> list[PresetProfile]:
    return list(_PRESETS)


def find_preset(name: str) -> PresetProfile | None:
    key = name.strip().casefold()
    if not key:
        return None
    for preset in _PRESETS:
        if preset.name.casefold() == key:
            return preset
    return None
