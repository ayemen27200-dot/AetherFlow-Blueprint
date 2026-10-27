"""CI guard for the real media toolchain.

The split integration tests auto-skip when FFmpeg/FFprobe are absent so the
suite stays runnable on minimal development machines. In CI, where real
media binaries are mandatory, ``AETHERFLOW_REQUIRE_MEDIA_BINARIES=1`` turns a
missing toolchain into a hard failure instead of a silent skip.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REQUIRE_ENV_VAR = "AETHERFLOW_REQUIRE_MEDIA_BINARIES"


def test_media_binaries_available_when_required() -> None:
    if os.environ.get(REQUIRE_ENV_VAR) != "1":
        pytest.skip("media toolchain enforcement is enabled only in CI")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg is not None, "ffmpeg must be installed for real split tests"
    assert ffprobe is not None, "ffprobe must be installed for real probe tests"

    subprocess.run([ffmpeg, "-version"], check=True, capture_output=True, text=True)
    subprocess.run(
        [ffprobe, "-version"], check=True, capture_output=True, text=True
    )
