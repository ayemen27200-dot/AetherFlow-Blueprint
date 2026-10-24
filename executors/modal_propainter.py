"""Modal GPU worker adapter for ProPainter; implementation is tracked by the related Dossier."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import Executor


class ModalProPainterExecutor(Executor):
    def run(self, job_spec: dict[str, Any], on_progress: Callable[[float], None]) -> dict[str, Any]:
        del job_spec, on_progress
        raise NotImplementedError("Executor not implemented; see D3/D4 specifications")
