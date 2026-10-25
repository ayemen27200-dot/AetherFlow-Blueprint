"""Regression tests for the non-destructive isolation guarantees of the
clean-host Compose verifier and the VPS installer.

An independent audit found that an earlier verifier version deleted the
repository data directory and tore down a fixed-name Compose project, while
the VPS installer fell back to world-writable (0777) data directories. These
tests pin the safe behaviour so neither defect can silently return.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_compose_stack.sh"
INSTALLER = REPO_ROOT / "scripts" / "install_vps.sh"


@pytest.fixture(scope="module")
def verifier_text() -> str:
    return VERIFIER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_verifier_uses_a_unique_mktemp_working_directory(verifier_text: str) -> None:
    assert "mktemp -d" in verifier_text
    # The working directory variable, not the repository checkout, is the
    # only directory ever removed.
    assert re.search(r'rm -rf "\$WORK_DIR"', verifier_text)


def test_verifier_never_deletes_repository_data(verifier_text: str) -> None:
    forbidden = [
        r'rm -rf "\$REPO_ROOT/data"',
        r"rm -rf \$REPO_ROOT/data",
        r"rm -rf data",
        r"rm -rf ./data",
    ]
    for pattern in forbidden:
        assert re.search(pattern, verifier_text) is None, (
            f"verifier must never delete the repository data tree: {pattern}"
        )


def test_verifier_uses_a_unique_compose_project_and_image(verifier_text: str) -> None:
    # Unique project and image tag derived from the per-run id.
    assert re.search(r'PROJECT="aetherflowsmoke\$\{?RUN_ID', verifier_text)
    assert re.search(r'IMAGE_REF="aetherflow-smoke:', verifier_text)
    # Every Compose invocation is scoped with the -p project flag.
    assert 'COMPOSE=(docker compose -p "$PROJECT"' in verifier_text
    # The fixed production project name may appear only in safety checks and
    # non-interference snapshots, never as the operated project.
    assert 'docker compose -p aetherflow ' not in verifier_text
    assert re.search(r'\[\[ "\$PROJECT" != "aetherflow" \]\]', verifier_text)


def test_teardown_is_scoped_to_the_unique_project(verifier_text: str) -> None:
    down_lines = [
        line for line in verifier_text.splitlines() if " down " in line
    ]
    assert down_lines, "expected a scoped compose down in cleanup"
    for line in down_lines:
        assert '"${COMPOSE[@]}"' in line, (
            "teardown must go through the unique-project COMPOSE array, never "
            f"a fixed-name project: {line}"
        )
        assert "-v" in line  # volumes removed belong to the unique project only


def test_temp_env_file_is_private_and_data_is_never_world_writable(
    verifier_text: str,
) -> None:
    assert "umask 077" in verifier_text
    assert re.search(r'chmod 600 "\$ENV_FILE"', verifier_text)
    assert "chmod 750" in verifier_text
    assert "chmod 0777" not in verifier_text
    assert "chmod 777" not in verifier_text


def test_verifier_remaps_bind_mounts_into_the_temp_directory(verifier_text: str) -> None:
    assert "override.yml" in verifier_text
    assert "$DATA_DIR:/app/data" in verifier_text
    # The repository .env is never created or used by the verifier.
    assert 'cat > "$REPO_ROOT/.env"' not in verifier_text
    assert '> "$REPO_ROOT/.env"' not in verifier_text


def test_verifier_proves_non_interference(verifier_text: str) -> None:
    # Before/after snapshots of the fixed project and the repository data tree.
    assert "com.docker.compose.project=aetherflow" in verifier_text
    assert "REPO_DATA_SNAPSHOT" in verifier_text
    assert "diff -u" in verifier_text


def test_installer_has_no_world_writable_fallback(installer_text: str) -> None:
    assert "0777" not in installer_text
    assert "chmod 777" not in installer_text
    # Ownership transfer to the container uid is mandatory and aborts on failure.
    assert "sudo -n chown -R 1000:1000 data" in installer_text
    assert "exit 1" in installer_text
