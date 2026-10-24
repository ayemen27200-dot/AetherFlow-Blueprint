"""FFmpeg split executor scaffold. Commands must use argv arrays, not shell strings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import Executor


class FFmpegSplitExecutor(Executor):
    def __init__(self, segment_sec: int = 300) -> None:
        if segment_sec <= 0:
            raise ValueError("segment_sec must be positive")
        self.segment_sec = segment_sec

    def run(self, job_spec: dict[str, Any], on_progress: Callable[[float], None]) -> dict[str, Any]:
        del job_spec, on_progress
        raise NotImplementedError(
            "Implement FFprobe validation, bounded FFmpeg subprocess and output checks"
        )
