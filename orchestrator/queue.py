"""SQLite queue interface; transactional implementation is a D2 task."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class QueuedJob:
    id: str
    type: str
    priority: int
    created_at: str


class SQLiteQueue:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        raise NotImplementedError("Implement schema, WAL, busy_timeout and transactional queue access")

    def enqueue(self, job: QueuedJob) -> None:
        raise NotImplementedError

    def dequeue(self) -> QueuedJob | None:
        raise NotImplementedError

    def size(self) -> int:
        raise NotImplementedError
