from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .resolve import ResolvedClip


class DownloadStatus(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class QueueItem:
    resolved: ResolvedClip
    status: DownloadStatus = DownloadStatus.QUEUED
    output_path: Path | None = None
    error: str | None = None
    progress: float | None = None
    speed_bps: float | None = None
    eta_seconds: int | None = None
    output_format: str = "mp4"
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    pause_requested: bool = False
    cancel_requested: bool = False
