"""End-to-end split tests driving a real FFmpeg binary.

The binary is resolved from PATH (CI ubuntu-latest ships ffmpeg/ffprobe) or,
locally, from the imageio-ffmpeg wheel installed only in the development
venv. Everything runs inside temporary directories; no network or paid
resources are used.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from executors.ffmpeg import (
    ExecutorInputError,
    ExecutorTimeoutError,
    FFmpegSplitExecutor,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_5s.mp4"
ALLOWED_EXT = (".mp4", ".mkv", ".mov", ".mp3", ".wav", ".m4a")


def _ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


FFMPEG = _ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg binary not available")


def _make_executor(tmp_path: Path, **kwargs) -> FFmpegSplitExecutor:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir(exist_ok=True)
    outbox.mkdir(exist_ok=True)
    defaults = {
        "segment_sec": 3,
        "allowed_base_dir": inbox,
        "output_base_dir": outbox,
        "max_file_size_bytes": 50 * 1024 * 1024,
        "allowed_extensions": ALLOWED_EXT,
        "ffmpeg_cmd": FFMPEG,
        "ffprobe_cmd": _ffprobe(),
        "timeout_sec": 60,
    }
    defaults.update(kwargs)
    return FFmpegSplitExecutor(**defaults)


def _copy_fixture(tmp_path: Path, name: str = "clip.mp4") -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    target = inbox / name
    shutil.copy2(FIXTURE, target)
    return target


def _decode_check(ffmpeg: str, path: Path) -> None:
    completed = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_real_split_produces_decodable_outputs(tmp_path: Path) -> None:
    source = _copy_fixture(tmp_path)
    executor = _make_executor(tmp_path)
    progress: list[float] = []

    result = executor.run(
        {"job_id": "job-real-1", "input_path": str(source)}, progress.append
    )

    assert result["job_id"] == "job-real-1"
    assert result["segment_count"] >= 1
    assert progress[0] == 0.0 and progress[-1] == 100.0
    assert progress == sorted(progress)  # monotonic, no jitter
    output_dir = tmp_path / "outbox" / "job-real-1"
    parts = sorted(output_dir.glob("*.mp4"))
    assert len(parts) == result["segment_count"]
    total = 0
    for part in parts:
        assert part.stat().st_size > 0
        _decode_check(FFMPEG, part)
        total += part.stat().st_size
    assert result["total_bytes"] == total
    # Stream copy keeps total duration within the documented keyframe tolerance.
    assert abs(result["outputs_duration_sec"] - result["source_duration_sec"]) <= (
        result["source_duration_sec"] * 0.15
    )


def test_real_split_creates_multiple_parts_with_frequent_keyframes(
    tmp_path: Path,
) -> None:
    # Test-only fixture: force a keyframe every ~0.5s so 2s segments split.
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    dense = inbox / "dense.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=5:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-c:v",
            "libx264",
            "-g",
            "15",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(dense),
        ],
        check=True,
        timeout=60,
    )
    executor = _make_executor(tmp_path, segment_sec=2)
    result = executor.run(
        {"job_id": "job-multi", "input_path": str(dense)}, lambda _: None
    )
    assert result["segment_count"] >= 2
    for output in result["outputs"]:
        _decode_check(FFMPEG, Path(output))


def test_missing_input_is_rejected_before_subprocess(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)
    with pytest.raises(ExecutorInputError, match="does not exist"):
        executor.run(
            {
                "job_id": "job-missing",
                "input_path": str(tmp_path / "inbox" / "ghost.mp4"),
            },
            lambda _: None,
        )
    assert not (tmp_path / "outbox" / "job-missing").exists()


def test_path_outside_trusted_base_is_rejected(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    with pytest.raises(ExecutorInputError, match="outside the trusted"):
        executor.run(
            {"job_id": "job-out", "input_path": str(outside)}, lambda _: None
        )


def test_corrupt_media_is_rejected_by_probe(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    corrupt = inbox / "broken.mp4"
    corrupt.write_bytes(b"this is not media content at all")
    executor = _make_executor(tmp_path)
    with pytest.raises(ExecutorInputError, match="could not be validated"):
        executor.run(
            {"job_id": "job-corrupt", "input_path": str(corrupt)}, lambda _: None
        )


def _corrupt_content_keep_container(path: Path) -> Path:
    """Zero a middle byte range: container headers/duration stay readable,
    while the coded frames become undecodable."""
    data = bytearray(path.read_bytes())
    middle = len(data) // 2
    data[middle : middle + 4096] = b"\x00" * 4096
    target = path.with_name("header_intact_content_corrupt.mp4")
    target.write_bytes(data)
    return target


def test_decode_media_distinguishes_good_from_header_intact_corrupt(
    tmp_path: Path,
) -> None:
    source = _copy_fixture(tmp_path)
    corrupt = _corrupt_content_keep_container(source)
    executor = _make_executor(tmp_path)

    # Premise of the regression: duration metadata remains readable on both
    # files, so metadata alone cannot tell them apart.
    if executor.ffprobe_cmd:
        corrupt_metadata = executor._probe_with_ffprobe(corrupt)
    else:
        corrupt_metadata = executor._probe_with_ffmpeg(corrupt)
    assert corrupt_metadata is not None and corrupt_metadata.duration_sec > 0

    # The dedicated full decode pass is what separates them.
    assert executor._decode_media(source) is True
    assert executor._decode_media(corrupt) is False


def test_header_intact_but_content_corrupt_media_is_rejected(tmp_path: Path) -> None:
    source = _copy_fixture(tmp_path)
    corrupt = _corrupt_content_keep_container(source)
    executor = _make_executor(tmp_path)

    with pytest.raises(ExecutorInputError, match="could not be validated"):
        executor.run(
            {"job_id": "job-corrupt-stream", "input_path": str(corrupt)},
            lambda _: None,
        )
    # Partial output from a rejected job must not remain on disk.
    assert not (tmp_path / "outbox" / "job-corrupt-stream").exists()


def test_timeout_terminates_real_subprocess(tmp_path: Path) -> None:
    source = _copy_fixture(tmp_path)
    executor = _make_executor(tmp_path, timeout_sec=0.001)
    with pytest.raises(ExecutorTimeoutError):
        executor.run(
            {"job_id": "job-timeout", "input_path": str(source)}, lambda _: None
        )
    # Partial segments are discarded so they can never be read as valid outputs.
    assert not (tmp_path / "outbox" / "job-timeout").exists()


@pytest.mark.skipif(_ffprobe() is None, reason="ffprobe not installed in this environment")
def test_ffprobe_path_reports_real_duration(tmp_path: Path) -> None:
    source = _copy_fixture(tmp_path)
    executor = _make_executor(tmp_path)
    info = executor.probe(source)
    assert info.decodable is True
    assert 4.5 <= info.duration_sec <= 5.5


def test_unwritable_output_directory_is_rejected(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory write permissions")
    source = _copy_fixture(tmp_path)
    locked = tmp_path / "locked-out"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        executor = _make_executor(tmp_path, output_base_dir=locked)
        with pytest.raises(ExecutorInputError, match="not writable"):
            executor.run(
                {"job_id": "job-locked", "input_path": str(source)},
                lambda _: None,
            )
    finally:
        locked.chmod(0o755)


def test_executor_source_never_uses_shell() -> None:
    import ast

    tree = ast.parse((REPO_ROOT / "executors" / "ffmpeg.py").read_text(encoding="utf-8"))
    shell_kwargs: list[ast.keyword] = []
    popen_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "Popen")
            or (isinstance(node.func, ast.Name) and node.func.id == "Popen")
        ):
            popen_calls += 1
            shell_kwargs = [kw for kw in node.keywords if kw.arg == "shell"]
    assert popen_calls >= 1
    assert not shell_kwargs or not ast.literal_eval(shell_kwargs[0].value)
