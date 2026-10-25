"""Unit tests for the FFmpeg split executor.

Subprocess is faked everywhere here; real-binary behavior lives in
tests/integration/test_split_e2e.py. Temporary files only, no network.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

from executors import ffmpeg as ffmpeg_mod
from executors.ffmpeg import (
    ExecutorCancelledError,
    ExecutorInputError,
    ExecutorTimeoutError,
    ExecutorToolError,
    FFmpegSplitExecutor,
    MediaInfo,
    ProgressParser,
    parse_hms,
)

# --------------------------------------------------------------------------- #
# Helpers / fakes
# --------------------------------------------------------------------------- #


class FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._index = 0
        self._wake = threading.Event()

    def __iter__(self) -> FakeStream:
        return self

    def __next__(self) -> str:
        if self._index < len(self._lines):
            value = self._lines[self._index]
            self._index += 1
            return value
        # Simulate a blocked pipe until the process is killed (timeout/cancel).
        if self._wake.wait(timeout=0.02):
            raise StopIteration
        raise StopIteration

    def exhaust(self) -> None:
        self._wake.set()


class FakeProcess:
    def __init__(self, lines: list[str], returncode: int = 0, pid: int = 4242) -> None:
        self.stdout = FakeStream(lines)
        self.stderr = FakeStream([])  # real Popen always exposes this PIPE too
        self._returncode = returncode
        self.returncode: int | None = None
        self.pid = pid
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._returncode if self.stdout._index >= len(self.stdout._lines) else None

    def wait(self, timeout: float | None = None) -> int:
        self.stdout.exhaust()
        self.returncode = self._returncode
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = self._returncode
        self.stdout.exhaust()

    def kill(self) -> None:
        self.killed = True
        self.returncode = self._returncode
        self.stdout.exhaust()


@pytest.fixture
def executor(tmp_path: Path) -> FFmpegSplitExecutor:
    base = tmp_path / "inbox"
    out = tmp_path / "outbox"
    base.mkdir()
    out.mkdir()
    return FFmpegSplitExecutor(
        segment_sec=3,
        allowed_base_dir=base,
        output_base_dir=out,
        max_file_size_bytes=1024 * 1024,
        allowed_extensions=(".mp4", ".mkv"),
        ffmpeg_cmd="/usr/bin/ffmpeg",
        ffprobe_cmd="/usr/bin/ffprobe",
        timeout_sec=30,
    )


def make_media(tmp_path: Path, name: str = "clip.mp4", size: int = 100) -> Path:
    path = tmp_path / "inbox" / name
    path.write_bytes(b"x" * size)
    return path


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_parse_hms_valid_and_invalid() -> None:
    assert parse_hms("00:00:05.00") == 5.0
    assert parse_hms("01:02:03.50") == 3723.5
    assert parse_hms("garbage") is None
    assert parse_hms("") is None


def test_progress_parser_clamps_steps_and_deduplicates() -> None:
    seen: list[float] = []
    parser = ProgressParser(10.0, seen.append)
    assert seen == [0.0]  # start event
    parser.feed_line("out_time_us=1000000")  # 10%
    parser.feed_line("out_time_us=1100000")  # still 10% -> no new emit
    parser.feed_line("out_time_us=5000000")  # 50%
    parser.feed_line("out_time_ms=8000000")  # 80%
    parser.feed_line("out_time=00:00:09.50")  # 95%
    parser.feed_line("progress=end")  # ignored
    parser.feed_line("nonsense")
    assert seen == [0.0, 10.0, 50.0, 80.0, 95.0]
    parser.finish()
    assert seen[-1] == 100.0
    parser.finish()  # idempotent, no duplicate 100
    assert seen.count(100.0) == 1


def test_progress_parser_clamps_out_of_range() -> None:
    seen: list[float] = []
    parser = ProgressParser(10.0, seen.append)
    parser.feed_line("out_time_us=999999999")  # huge -> clamped to 100
    assert seen == [0.0, 100.0]


def test_progress_parser_requires_positive_duration() -> None:
    with pytest.raises(ValueError):
        ProgressParser(0, lambda _: None)


# --------------------------------------------------------------------------- #
# Spec / input / output validation
# --------------------------------------------------------------------------- #


def test_constructor_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError):
        FFmpegSplitExecutor(0)
    with pytest.raises(ValueError):
        FFmpegSplitExecutor(timeout_sec=0)


def test_validate_spec_rejects_bad_shapes(executor: FFmpegSplitExecutor) -> None:
    for bad in (
        "not-a-dict",
        {},
        {"job_id": "", "input_path": "/x/y.mp4"},
        {"job_id": "../escape", "input_path": "/x/y.mp4"},
        {"job_id": "ok", "input_path": ""},
        {"job_id": "ok", "input_path": None},
    ):
        with pytest.raises(ExecutorInputError):
            executor._validate_spec(bad)  # type: ignore[arg-type]


def test_input_fails_closed_without_base_dir(tmp_path: Path) -> None:
    bare = FFmpegSplitExecutor(ffmpeg_cmd="ffmpeg", ffprobe_cmd=None)
    with pytest.raises(ExecutorInputError, match="trusted base"):
        bare._validate_input(str(tmp_path / "a.mp4"))


def test_input_rejects_relative_missing_outside_bad_extension_oversize(
    executor: FFmpegSplitExecutor, tmp_path: Path
) -> None:
    media = make_media(tmp_path)
    with pytest.raises(ExecutorInputError, match="absolute"):
        executor._validate_input("inbox/clip.mp4")
    with pytest.raises(ExecutorInputError):
        executor._validate_input(str(tmp_path / "elsewhere.mp4"))
    missing = tmp_path / "inbox" / "ghost.mp4"
    with pytest.raises(ExecutorInputError, match="does not exist"):
        executor._validate_input(str(missing))
    weird = make_media(tmp_path, "clip.txt")
    with pytest.raises(ExecutorInputError, match="extension"):
        executor._validate_input(str(weird))
    big = make_media(tmp_path, "big.mp4", size=2 * 1024 * 1024)
    with pytest.raises(ExecutorInputError, match="size limit"):
        executor._validate_input(str(big))
    # The valid media passes and is symlink-resolved.
    assert executor._validate_input(str(media)) == media.resolve()


def test_output_requires_base_and_rejects_existing_or_unwritable(
    executor: FFmpegSplitExecutor, tmp_path: Path
) -> None:
    bare = FFmpegSplitExecutor(ffmpeg_cmd="ffmpeg")
    with pytest.raises(ExecutorInputError, match="output base"):
        bare._prepare_output("j1", Path("/x.mp4"))

    out_dir, pattern = executor._prepare_output("j1", Path("/x/clip.mp4"))
    assert out_dir == (tmp_path / "outbox" / "j1").resolve()
    assert pattern.name == "part_%03d.mp4"
    with pytest.raises(ExecutorInputError, match="already exists"):
        executor._prepare_output("j1", Path("/x/clip.mp4"))

    if os.geteuid() != 0:
        readonly = tmp_path / "locked"
        readonly.mkdir()
        locked_exec = FFmpegSplitExecutor(
            allowed_base_dir=tmp_path / "inbox",
            output_base_dir=readonly,
            ffmpeg_cmd="ffmpeg",
        )
        readonly.chmod(0o500)
        try:
            with pytest.raises(ExecutorInputError, match="not writable"):
                locked_exec._prepare_output("j2", Path("/x/clip.mp4"))
        finally:
            readonly.chmod(0o755)


# --------------------------------------------------------------------------- #
# Probe paths (subprocess.run faked)
# --------------------------------------------------------------------------- #


def test_probe_prefers_ffprobe_json(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = make_media(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "/usr/bin/ffprobe":
            return subprocess.CompletedProcess(
                argv, 0, stdout='{"format": {"duration": "5.0"}}', stderr=""
            )
        # Dedicated decode call must carry -xerror and report a clean stream.
        assert "-xerror" in argv and "null" in argv
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
    info = executor.probe(media)
    assert info == MediaInfo(duration_sec=5.0, decodable=True)
    # Metadata probe first, then an independent decode pass — never merged.
    assert [call[0] for call in calls] == ["/usr/bin/ffprobe", "/usr/bin/ffmpeg"]


def test_probe_rejects_file_when_metadata_ok_but_decode_emits_errors(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Audit regression: ffprobe-readable headers with corrupt coded content.
    # FFmpeg can exit 0 while emitting decode errors, so an empty error stream
    # is required rather than trusting the exit code alone.
    media = make_media(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[0] == "/usr/bin/ffprobe":
            return subprocess.CompletedProcess(
                argv, 0, stdout='{"format": {"duration": "5.0"}}', stderr=""
            )
        return subprocess.CompletedProcess(
            argv, 0, stdout="", stderr="[h264] Invalid NAL unit size\n"
        )

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
    with pytest.raises(ExecutorInputError, match="could not be validated"):
        executor.probe(media)


def test_probe_rejects_file_when_metadata_ok_but_decode_exits_nonzero(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = make_media(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[0] == "/usr/bin/ffprobe":
            return subprocess.CompletedProcess(
                argv, 0, stdout='{"format": {"duration": "5.0"}}', stderr=""
            )
        return subprocess.CompletedProcess(argv, 183, stdout="", stderr="decode failed")

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
    with pytest.raises(ExecutorInputError, match="could not be validated"):
        executor.probe(media)


def test_decode_media_is_bounded_by_probe_timeout(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = make_media(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
    assert executor._decode_media(media) is True
    assert seen["timeout"] == executor.probe_timeout_sec


def test_probe_falls_back_to_ffmpeg_when_ffprobe_fails(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = make_media(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "/usr/bin/ffprobe":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
        if "-f" in argv and "null" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        # Banner invocation carrying the human duration line.
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="Duration: 00:00:05.00, start: 0.000000, bitrate: 100 kb/s\n",
        )

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
    info = executor.probe(media)
    assert info.duration_sec == 5.0 and info.decodable is True
    # ffprobe fail -> ffmpeg fallback (decode + banner) -> dedicated decode.
    assert [call[0] for call in calls] == [
        "/usr/bin/ffprobe",
        "/usr/bin/ffmpeg",
        "/usr/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]


def test_probe_corrupt_media_raises(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = make_media(tmp_path)
    monkeypatch.setattr(
        ffmpeg_mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="invalid data"),
    )
    with pytest.raises(ExecutorInputError, match="could not be validated"):
        executor.probe(media)


# --------------------------------------------------------------------------- #
# argv + subprocess lifecycle
# --------------------------------------------------------------------------- #


def test_build_argv_is_list_without_shell_and_uses_stream_copy(
    executor: FFmpegSplitExecutor,
) -> None:
    argv = executor._build_argv(Path("/in/clip.mp4"), Path("/out/p/part_%03d.mp4"))
    assert isinstance(argv, list) and all(isinstance(item, str) for item in argv)
    assert argv[0] == "/usr/bin/ffmpeg"
    assert "-c" in argv and argv[argv.index("-c") + 1] == "copy"
    assert "libx264" not in " ".join(argv)  # MVP forbids re-encoding
    assert "-progress" in argv and "pipe:1" in argv
    assert "-nostdin" in argv and "-nostats" in argv
    assert "-segment_time" in argv and argv[argv.index("-segment_time") + 1] == "3"


def test_run_subprocess_drives_progress_and_returns_code(
    executor: FFmpegSplitExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = ["out_time_us=1000000\n", "out_time_us=5000000\n", "progress=end\n"]
    fake = FakeProcess(lines)
    monkeypatch.setattr(executor, "_popen", lambda argv: fake)
    monkeypatch.setattr(ffmpeg_mod.os, "killpg", lambda *a: None)
    seen: list[float] = []
    parser = ProgressParser(10.0, seen.append)
    code = executor._run_subprocess(["ffmpeg"], parser, None)
    assert code == 0
    assert seen == [0.0, 10.0, 50.0]


def test_run_subprocess_timeout_terminates_group(
    executor: FFmpegSplitExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor.timeout_sec = 0.001
    fake = FakeProcess(["out_time_us=1000000\n"])  # pipe blocks after one line
    killed: list[int] = []
    monkeypatch.setattr(executor, "_popen", lambda argv: fake)
    monkeypatch.setattr(ffmpeg_mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        ffmpeg_mod.os, "killpg", lambda pgid, sig: killed.append(sig) or fake.kill()
    )
    parser = ProgressParser(10.0, lambda _: None)
    with pytest.raises(ExecutorTimeoutError):
        executor._run_subprocess(["ffmpeg"], parser, None)
    import signal

    assert signal.SIGTERM in killed
    assert fake.killed or fake.terminated


def test_run_subprocess_cancellation(
    executor: FFmpegSplitExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = threading.Event()
    fake = FakeProcess(["out_time_us=1000000\n", "out_time_us=2000000\n"])
    monkeypatch.setattr(executor, "_popen", lambda argv: fake)
    monkeypatch.setattr(ffmpeg_mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(ffmpeg_mod.os, "killpg", lambda *a: fake.terminate())

    def cancel_on_progress(_pct: float) -> None:
        event.set()

    parser = ProgressParser(10.0, cancel_on_progress)
    with pytest.raises(ExecutorCancelledError):
        executor._run_subprocess(["ffmpeg"], parser, event)


def test_nonzero_exit_classification(executor: FFmpegSplitExecutor) -> None:
    with pytest.raises(ExecutorToolError, match="code 137"):
        executor._classify_nonzero_exit(137, None)
    event = threading.Event()
    event.set()
    with pytest.raises(ExecutorCancelledError):
        executor._classify_nonzero_exit(15, event)
    with pytest.raises(ExecutorToolError, match="code 2"):
        executor._classify_nonzero_exit(2, None)


# --------------------------------------------------------------------------- #
# Output verification and full run with fakes
# --------------------------------------------------------------------------- #


def test_verify_outputs_rejects_missing_empty_undecodable_and_duration_drift(
    executor: FFmpegSplitExecutor, tmp_path: Path
) -> None:
    out_dir = tmp_path / "outbox" / "job"
    out_dir.mkdir(parents=True)
    pattern = out_dir / "part_%03d.mp4"
    media = MediaInfo(duration_sec=10.0, decodable=True)

    with pytest.raises(ExecutorToolError, match="no output"):
        executor._verify_outputs(out_dir, pattern, media, "job")

    part = out_dir / "part_000.mp4"
    part.write_bytes(b"")
    with pytest.raises(ExecutorToolError, match="missing or empty"):
        executor._verify_outputs(out_dir, pattern, media, "job")

    part.write_bytes(b"data")
    executor.probe = lambda path: MediaInfo(duration_sec=2.0, decodable=False)  # type: ignore[method-assign]
    with pytest.raises(ExecutorToolError, match="failed media validation"):
        executor._verify_outputs(out_dir, pattern, media, "job")

    executor.probe = lambda path: MediaInfo(duration_sec=2.0, decodable=True)  # type: ignore[method-assign]
    with pytest.raises(ExecutorToolError, match="beyond tolerance"):
        executor._verify_outputs(out_dir, pattern, media, "job")


def test_full_run_happy_path_with_fakes(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = make_media(tmp_path)
    monkeypatch.setattr(
        executor, "probe", lambda path: MediaInfo(duration_sec=5.0, decodable=True)
    )
    monkeypatch.setattr(
        executor,
        "_popen",
        lambda argv: FakeProcess(["out_time_us=2500000\n", "progress=end\n"]),
    )

    def fake_verify(output_dir, output_pattern, media_info, job_id):
        part = output_dir / "part_000.mp4"
        part.write_bytes(b"segment-bytes")
        return {
            "job_id": job_id,
            "outputs": [str(part)],
            "segment_count": 1,
            "source_duration_sec": 5.0,
            "outputs_duration_sec": 5.0,
            "total_bytes": part.stat().st_size,
            "segment_sec": 3,
        }

    monkeypatch.setattr(executor, "_verify_outputs", fake_verify)
    seen: list[float] = []
    result = executor.run(
        {"job_id": "job-1", "input_path": str(media)}, seen.append
    )
    assert result["segment_count"] == 1 and result["job_id"] == "job-1"
    assert seen[0] == 0.0 and seen[-1] == 100.0


def test_full_run_nonzero_exit_raises_tool_error(
    executor: FFmpegSplitExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = make_media(tmp_path)
    monkeypatch.setattr(
        executor, "probe", lambda path: MediaInfo(duration_sec=5.0, decodable=True)
    )
    monkeypatch.setattr(executor, "_popen", lambda argv: FakeProcess([], returncode=1))
    with pytest.raises(ExecutorToolError, match="code 1"):
        executor.run({"job_id": "job-2", "input_path": str(media)}, lambda _: None)
    # No success claims without verified outputs, and messages stay path-free.
    assert not list((tmp_path / "outbox" / "job-2").glob("*.mp4"))


def test_from_config_reads_project_transfer_contract(tmp_path: Path) -> None:
    built = FFmpegSplitExecutor.from_config(
        allowed_base_dir=tmp_path / "in",
        output_base_dir=tmp_path / "out",
        segment_sec=12,
    )
    assert built.segment_sec == 12
    assert built.max_file_size_bytes == 2000 * 1024 * 1024
    assert built.allowed_extensions == frozenset(
        {".mp4", ".mkv", ".mov", ".mp3", ".wav", ".m4a"}
    )


def test_resolve_transfer_spec_explicit_argument_wins(tmp_path: Path) -> None:
    spec = tmp_path / "file_transfer_spec.yaml"
    spec.write_text(
        "constraints:\n  max_file_size_mb: 42\n  allowed_extensions: [.mp4]\n",
        encoding="utf-8",
    )
    resolved = ffmpeg_mod.resolve_transfer_spec(spec)
    assert resolved == spec.resolve()


def test_resolve_transfer_spec_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = tmp_path / "nested" / "file_transfer_spec.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text("constraints: {max_file_size_mb: 1, allowed_extensions: []}\n")
    monkeypatch.setenv("AETHERFLOW_TRANSFER_SPEC", str(spec))
    assert ffmpeg_mod.resolve_transfer_spec() == spec.resolve()


def test_resolve_transfer_spec_cwd_fallback_for_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate the installed wheel layout: no package-relative config exists.
    monkeypatch.setattr(ffmpeg_mod, "_PACKAGE_SPEC", tmp_path / "missing.yaml")
    monkeypatch.delenv("AETHERFLOW_TRANSFER_SPEC", raising=False)
    app_dir = tmp_path / "app"
    (app_dir / "config").mkdir(parents=True)
    spec = app_dir / "config" / "file_transfer_spec.yaml"
    spec.write_text("constraints: {max_file_size_mb: 1, allowed_extensions: []}\n")
    monkeypatch.chdir(app_dir)
    assert ffmpeg_mod.resolve_transfer_spec() == spec.resolve()


def test_resolve_transfer_spec_missing_everywhere_is_path_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_mod, "_PACKAGE_SPEC", tmp_path / "missing.yaml")
    monkeypatch.delenv("AETHERFLOW_TRANSFER_SPEC", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError) as excinfo:
        ffmpeg_mod.resolve_transfer_spec()
    assert str(tmp_path) not in str(excinfo.value)
