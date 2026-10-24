"""FFmpeg split executor.

Implements the D2 contract in ``D2_MVP/03_FFMPEG_EXECUTOR.md``:

* every filesystem path is absolute, symlink-resolved and checked against an
  explicit trusted base directory — the executor fails closed when no base is
  configured, mirroring the queue's local-path policy;
* media validation has two independent parts: a duration probe (ffprobe
  JSON when available, otherwise an FFmpeg banner/null-decode fallback) and
  a separate full null-decode pass. Readable metadata alone never implies
  decodability — the decode must exit 0 with an empty error stream, and the
  same rule is applied to every output part;
* FFmpeg is invoked through ``subprocess`` with an argv list only
  (``shell=True`` is never used), a bounded timeout and process-group
  termination on timeout/cancellation;
* progress is parsed from the machine-readable ``-progress pipe:1`` stream,
  clamped to 0..100 and reported in fixed 5% steps without duplicate updates;
* exit code zero alone is never success: every output part must exist, be
  non-empty, decodable and the combined duration must stay within a documented
  tolerance of the probed source duration.

The module performs no API/database changes and runs no cloud calls.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Executor

# Reuse the project's transfer contract for size/extension limits.
_TRANSFER_SPEC = Path(__file__).resolve().parents[1] / "config" / "file_transfer_spec.yaml"

# Job ids double as output directory names; keep them to bounded safe tokens.
_JOB_ID_RE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
# Stream-copy segmentation lands on keyframe boundaries, so part durations are
# approximate. Accept the documented imprecision with an explicit tolerance
# rather than pretending the split is frame-accurate.
_DURATION_TOLERANCE = 0.15
_PROGRESS_STEP = 5.0


class ExecutorError(Exception):
    """Base class for executor failures with a stable public error code."""

    error_code = "TOOL_ERROR"


class ExecutorInputError(ExecutorError):
    """Rejected/unsafe/corrupt input — a permanent failure, not a retry cue."""

    error_code = "INVALID_INPUT"


class ExecutorToolError(ExecutorError):
    """FFmpeg failed or its outputs could not be verified."""

    error_code = "TOOL_ERROR"


class ExecutorTimeoutError(ExecutorError):
    """The bounded subprocess timeout elapsed before completion."""

    error_code = "TIMEOUT"


class ExecutorCancelledError(ExecutorError):
    """The caller signalled cancellation while the process was running."""

    error_code = "CANCELLED"


@dataclass(frozen=True)
class MediaInfo:
    """Probe result needed for progress and output verification."""

    duration_sec: float
    decodable: bool


class ProgressParser:
    """Stateful parser for FFmpeg ``-progress pipe:1`` key=value lines.

    Percentages are clamped to ``[0, 100]`` and forwarded only when they reach
    the next fixed step, so callers never receive duplicate or jittery updates.
    """

    def __init__(
        self,
        duration_sec: float,
        on_progress: Callable[[float], None],
        *,
        step: float = _PROGRESS_STEP,
    ) -> None:
        if duration_sec <= 0:
            raise ValueError("duration_sec must be positive")
        self._duration_sec = duration_sec
        self._on_progress = on_progress
        self._step = step
        self._last_emitted = -1.0
        # Announce 0% immediately so callers always observe a start event.
        self._emit(0.0)

    def feed_line(self, line: str) -> None:
        if not line:
            return
        key, sep, value = line.partition("=")
        if not sep:
            return
        key = key.strip()
        value = value.strip()
        seconds: float | None = None
        if key == "out_time_us" and value.isdigit():
            seconds = int(value) / 1_000_000
        elif key == "out_time_ms" and value.isdigit():
            # FFmpeg's out_time_ms is also documented in microseconds.
            seconds = int(value) / 1_000_000
        elif key == "out_time":
            seconds = parse_hms(value)
        if seconds is None:
            return
        pct = max(0.0, min(100.0, seconds / self._duration_sec * 100.0))
        self._emit(pct)

    def finish(self) -> None:
        self._emit(100.0)

    def _emit(self, pct: float) -> None:
        stepped = min(100.0, (pct // self._step) * self._step)
        if stepped > self._last_emitted:
            self._last_emitted = stepped
            self._on_progress(stepped)


def parse_hms(value: str) -> float | None:
    """Parse ``HH:MM:SS.xx`` (FFmpeg human timestamp) into seconds."""
    try:
        hours, minutes, seconds = value.split(":")
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, AttributeError):
        return None
    return result if result >= 0 else None


class FFmpegSplitExecutor(Executor):
    """Split one media file into stream-copied segments."""

    def __init__(
        self,
        segment_sec: int = 300,
        *,
        allowed_base_dir: str | Path | None = None,
        output_base_dir: str | Path | None = None,
        max_file_size_bytes: int | None = None,
        allowed_extensions: frozenset[str] | set[str] | tuple[str, ...] | None = None,
        timeout_sec: float = 3600.0,
        ffmpeg_cmd: str | None = None,
        ffprobe_cmd: str | None = None,
        progress_step: float = _PROGRESS_STEP,
        probe_timeout_sec: float = 60.0,
    ) -> None:
        if segment_sec <= 0:
            raise ValueError("segment_sec must be positive")
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be positive")
        if probe_timeout_sec <= 0:
            raise ValueError("probe_timeout_sec must be positive")
        self.segment_sec = segment_sec
        self.timeout_sec = float(timeout_sec)
        self.probe_timeout_sec = float(probe_timeout_sec)
        self.allowed_base_dir = (
            Path(allowed_base_dir).resolve() if allowed_base_dir is not None else None
        )
        self.output_base_dir = (
            Path(output_base_dir).resolve() if output_base_dir is not None else None
        )
        self.max_file_size_bytes = max_file_size_bytes
        self.allowed_extensions = (
            frozenset(ext.lower() for ext in allowed_extensions)
            if allowed_extensions is not None
            else None
        )
        self.ffmpeg_cmd = ffmpeg_cmd or shutil.which("ffmpeg")
        self.ffprobe_cmd = ffprobe_cmd or shutil.which("ffprobe")
        self.progress_step = progress_step

    @classmethod
    def from_config(
        cls,
        spec_path: str | Path = _TRANSFER_SPEC,
        *,
        allowed_base_dir: str | Path,
        output_base_dir: str | Path,
        segment_sec: int = 300,
        timeout_sec: float = 3600.0,
        ffmpeg_cmd: str | None = None,
        ffprobe_cmd: str | None = None,
    ) -> FFmpegSplitExecutor:
        """Build an executor whose limits come from the transfer YAML only."""
        import yaml

        data = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
        constraints = data["constraints"]
        return cls(
            segment_sec,
            allowed_base_dir=allowed_base_dir,
            output_base_dir=output_base_dir,
            max_file_size_bytes=int(constraints["max_file_size_mb"]) * 1024 * 1024,
            allowed_extensions=tuple(constraints["allowed_extensions"]),
            timeout_sec=timeout_sec,
            ffmpeg_cmd=ffmpeg_cmd,
            ffprobe_cmd=ffprobe_cmd,
        )

    # ------------------------------------------------------------------ run
    def run(
        self,
        job_spec: dict[str, Any],
        on_progress: Callable[[float], None],
        *,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Validate, probe, split and verify. Raises an :class:`ExecutorError`."""
        job_id, input_path = self._validate_spec(job_spec)
        source = self._validate_input(input_path)
        output_dir, output_pattern = self._prepare_output(job_id, source)
        try:
            media = self.probe(source)

            argv = self._build_argv(source, output_pattern)
            parser = ProgressParser(
                media.duration_sec, on_progress, step=self.progress_step
            )
            returncode = self._run_subprocess(argv, parser, cancel_event)
            if returncode != 0:
                self._classify_nonzero_exit(returncode, cancel_event)

            result = self._verify_outputs(output_dir, output_pattern, media, job_id)
        except ExecutorError:
            # Never leave partial segments that could be mistaken for outputs.
            self._discard_partial_output(output_dir)
            raise
        parser.finish()
        return result

    def _discard_partial_output(self, output_dir: Path) -> None:
        """Remove a job directory we created, but only inside the trusted base."""
        base = self.output_base_dir
        if base is None:
            return
        resolved = output_dir.resolve()
        if resolved == base.resolve() or not resolved.is_relative_to(base):
            return
        shutil.rmtree(resolved, ignore_errors=True)

    # ------------------------------------------------------------- validation
    def _validate_spec(self, job_spec: dict[str, Any]) -> tuple[str, str]:
        if not isinstance(job_spec, dict):
            raise ExecutorInputError("job specification must be an object")
        job_id = job_spec.get("job_id")
        input_path = job_spec.get("input_path")
        if not isinstance(job_id, str) or not job_id or set(job_id) - _JOB_ID_RE_CHARS:
            raise ExecutorInputError("invalid or missing job id")
        if not isinstance(input_path, str) or not input_path:
            raise ExecutorInputError("invalid or missing input path")
        return job_id, input_path

    def _validate_input(self, input_path: str) -> Path:
        # Fail closed: without a trusted root no local path can be accepted.
        if self.allowed_base_dir is None:
            raise ExecutorInputError(
                "input validation requires an explicit trusted base directory"
            )
        candidate = Path(input_path)
        if not candidate.is_absolute():
            raise ExecutorInputError("input path must be absolute")
        resolved = candidate.resolve()  # follows symlinks before containment
        if not resolved.is_relative_to(self.allowed_base_dir):
            raise ExecutorInputError("input path is outside the trusted directory")
        if not resolved.is_file():
            raise ExecutorInputError("input file does not exist or is not a file")
        if (
            self.allowed_extensions is not None
            and resolved.suffix.lower() not in self.allowed_extensions
        ):
            raise ExecutorInputError("input file extension is not allowed")
        if self.max_file_size_bytes is not None:
            if resolved.stat().st_size > self.max_file_size_bytes:
                raise ExecutorInputError("input file exceeds the configured size limit")
        return resolved

    def _prepare_output(self, job_id: str, source: Path) -> tuple[Path, Path]:
        if self.output_base_dir is None:
            raise ExecutorInputError(
                "output handling requires an explicit output base directory"
            )
        output_dir = (self.output_base_dir / job_id).resolve()
        if not output_dir.is_relative_to(self.output_base_dir):
            raise ExecutorInputError("resolved output directory escapes its base")
        try:
            output_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ExecutorInputError(
                "output directory for this job already exists"
            ) from exc
        except OSError as exc:
            # Includes permission/read-only failures; never leak the raw path.
            raise ExecutorInputError("output directory is not writable") from exc
        output_pattern = output_dir / f"part_%03d{source.suffix.lower()}"
        return output_dir, output_pattern

    # ------------------------------------------------------------------ probe
    def probe(self, path: Path) -> MediaInfo:
        """Read metadata and verify the complete media stream can be decoded."""
        info = self._probe_with_ffprobe(path) if self.ffprobe_cmd else None
        if info is None:
            info = self._probe_with_ffmpeg(path)
        if info is None or not self._decode_media(path):
            raise ExecutorInputError("input media could not be validated")
        return MediaInfo(duration_sec=info.duration_sec, decodable=True)

    def _decode_media(self, path: Path) -> bool:
        """Decode the complete media stream with a bounded FFmpeg subprocess.

        A zero exit code alone is not evidence of decodability: FFmpeg can
        exit 0 while flooding stderr with decode errors (verified against a
        header-intact, content-corrupt fixture whose duration metadata stays
        readable). Success therefore requires both exit code 0 and an empty
        error-level stderr stream; ``-xerror`` additionally turns the first
        decode error into a nonzero exit as defence in depth.
        """
        if not self.ffmpeg_cmd:
            return False
        try:
            completed = subprocess.run(
                [
                    self.ffmpeg_cmd,
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(path),
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=self.probe_timeout_sec,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0 and not completed.stderr.strip()

    def _probe_with_ffprobe(self, path: Path) -> MediaInfo | None:
        try:
            completed = subprocess.run(
                [
                    self.ffprobe_cmd,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=self.probe_timeout_sec,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        try:
            payload = json.loads(completed.stdout)
            duration = float(payload["format"]["duration"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if duration <= 0:
            return None
        return MediaInfo(duration_sec=duration, decodable=True)

    def _probe_with_ffmpeg(self, path: Path) -> MediaInfo | None:
        """Fallback used when no ffprobe binary is available.

        Parses the banner duration and performs a full null decode, which is
        stronger evidence of structural validity than a header read.
        """
        if not self.ffmpeg_cmd:
            return None
        try:
            completed = subprocess.run(
                [
                    self.ffmpeg_cmd,
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=self.probe_timeout_sec,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        # The error-level decode above suppresses the banner; duration comes
        # from a separate default-level invocation in that case.
        duration = self._read_banner_duration(path)
        if duration is None:
            return None
        return MediaInfo(duration_sec=duration, decodable=True)

    def _read_banner_duration(self, path: Path) -> float | None:
        if not self.ffmpeg_cmd:
            return None
        try:
            completed = subprocess.run(
                [self.ffmpeg_cmd, "-hide_banner", "-nostdin", "-i", str(path)],
                capture_output=True,
                text=True,
                timeout=self.probe_timeout_sec,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        for line in completed.stderr.splitlines():
            if "Duration:" in line:
                token = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
                return parse_hms(token)
        return None

    # ------------------------------------------------------------- subprocess
    def _build_argv(self, source: Path, output_pattern: Path) -> list[str]:
        if not self.ffmpeg_cmd:
            raise ExecutorToolError("ffmpeg executable is not available")
        return [
            self.ffmpeg_cmd,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0",
            "-c",
            "copy",  # MVP: stream copy only, no re-encoding
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_sec),
            "-reset_timestamps",
            "1",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output_pattern),
        ]

    def _popen(self, argv: list[str]) -> subprocess.Popen[str]:
        # Seam for tests; start_new_session puts FFmpeg into its own process
        # group so timeout/cancel can terminate the whole tree reliably.
        return subprocess.Popen(  # noqa: S603 - argv list, shell never used
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )

    def _run_subprocess(
        self,
        argv: list[str],
        parser: ProgressParser,
        cancel_event: threading.Event | None,
    ) -> int:
        try:
            process = self._popen(argv)
        except OSError as exc:
            raise ExecutorToolError("media tool could not be started") from exc

        lines: queue.Queue[str | None] = queue.Queue()
        # Drain stderr concurrently: an unread PIPE can fill its OS buffer and
        # block FFmpeg mid-run. Keep only a bounded tail for local diagnostics;
        # user-facing errors stay generic and never include paths or this text.
        stderr_tail: deque[str] = deque(maxlen=50)

        def reader() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        def stderr_reader() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_tail.append(line)

        thread = threading.Thread(target=reader, daemon=True)
        err_thread = threading.Thread(target=stderr_reader, daemon=True)
        thread.start()
        err_thread.start()
        end_at = time.monotonic() + self.timeout_sec
        while True:
            try:
                item = lines.get(timeout=0.1)
            except queue.Empty:
                item = None
            if item is not None:
                parser.feed_line(item)
            if cancel_event is not None and cancel_event.is_set():
                self._terminate_group(process)
                raise ExecutorCancelledError("processing was cancelled")
            if time.monotonic() > end_at:
                self._terminate_group(process)
                raise ExecutorTimeoutError("media processing exceeded its time limit")
            if item is None and process.poll() is not None:
                break
        thread.join(timeout=1.0)
        err_thread.join(timeout=1.0)
        process.wait()
        return int(process.returncode)

    def _terminate_group(self, process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        process.wait()

    def _classify_nonzero_exit(
        self, returncode: int, cancel_event: threading.Event | None
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ExecutorCancelledError("processing was cancelled")
        # Code 137 often means SIGKILL/OOM but other killers produce it too; we
        # surface the numeric code without asserting a single cause.
        raise ExecutorToolError(f"media tool exited with code {returncode}")

    # --------------------------------------------------------------- outputs
    def _verify_outputs(
        self,
        output_dir: Path,
        output_pattern: Path,
        media: MediaInfo,
        job_id: str,
    ) -> dict[str, Any]:
        suffix = output_pattern.suffix
        parts = sorted(output_dir.glob(f"*{suffix}"))
        if not parts:
            raise ExecutorToolError("processing produced no output files")
        outputs: list[str] = []
        total_duration = 0.0
        total_bytes = 0
        for part in parts:
            if not part.is_file() or part.stat().st_size == 0:
                raise ExecutorToolError("an output part is missing or empty")
            try:
                info = self.probe(part)
            except ExecutorInputError as exc:
                raise ExecutorToolError("an output part failed media validation") from exc
            if not info.decodable:
                raise ExecutorToolError("an output part failed media validation")
            outputs.append(str(part))
            total_duration += info.duration_sec
            total_bytes += part.stat().st_size

        expected = media.duration_sec
        if expected > 0 and abs(total_duration - expected) > expected * _DURATION_TOLERANCE:
            raise ExecutorToolError("output duration differs from the source beyond tolerance")

        return {
            "job_id": job_id,
            "outputs": outputs,
            "segment_count": len(outputs),
            "source_duration_sec": round(expected, 3),
            "outputs_duration_sec": round(total_duration, 3),
            "total_bytes": total_bytes,
            "segment_sec": self.segment_sec,
        }
