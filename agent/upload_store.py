"""Local upload store bridging opaque upload ids to worker-local files.

The HTTP API only ever accepts opaque upload ids (never server filesystem
paths) and the queue stores them verbatim behind an ``upload://`` marker.
A worker process, which owns the local storage layout, is the only
component allowed to map such an id to a real file. That mapping lives
here, behind strict containment rules, so the two channels stay separate:

* the id is re-validated with the same token rule the queue enforces
  (charset, length, no separators, no dot-only names);
* the candidate is joined inside one configured store directory, symlinks
  are resolved, and the real target must stay inside that directory;
* the target must be an existing regular file.

Nothing here is ever called by the web layer, and no path derived here is
sent back to API clients.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.queue import InvalidUploadIdError, validate_upload_id


class UploadResolutionError(Exception):
    """Stable error for an upload id that cannot be resolved locally."""

    error_code = "INVALID_INPUT"


class LocalUploadStore:
    """Resolve opaque upload ids against a single trusted directory."""

    def __init__(self, store_dir: str | Path) -> None:
        base = Path(store_dir).resolve()
        if not base.is_dir():
            raise UploadResolutionError("configured upload store directory is missing")
        self.store_dir = base

    def resolve(self, upload_id: str) -> Path:
        """Return the real local file for ``upload_id`` or raise.

        Fails closed on every anomaly: malformed token, missing object, or a
        resolved target that escapes the store root (e.g. a symlink placed
        in the store by another process).
        """
        try:
            token = validate_upload_id(upload_id)
        except InvalidUploadIdError as exc:
            raise UploadResolutionError("upload id is not valid") from exc

        candidate = (self.store_dir / token).resolve()
        if not candidate.is_relative_to(self.store_dir):
            raise UploadResolutionError("upload object escapes the store directory")
        if not candidate.is_file():
            raise UploadResolutionError("upload object is not available")
        return candidate
