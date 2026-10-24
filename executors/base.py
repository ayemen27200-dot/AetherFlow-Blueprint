"""Shared executor interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class Executor(ABC):
    @abstractmethod
    def run(self, job_spec: dict[str, Any], on_progress: Callable[[float], None]) -> dict[str, Any]:
        """Execute a job and return output metadata."""
        raise NotImplementedError
