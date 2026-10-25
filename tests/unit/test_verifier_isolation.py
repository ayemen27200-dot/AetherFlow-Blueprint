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
        line
        for line in verifier_text.splitlines()
        if " down " in line and not line.lstrip().startswith("#")
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


def test_verifier_uses_a_unique_named_volume_not_a_repo_bind_mount(
    verifier_text: str,
) -> None:
    assert "override.yml" in verifier_text
    # Data lives in a per-run unique named volume (rootless-safe), never in a
    # bind mount against the repository checkout.
    assert 'VOLUME_NAME="${PROJECT}_data"' in verifier_text
    assert "$VOLUME_NAME:/app/data" in verifier_text
    assert "$DATA_DIR:/app/data" not in verifier_text
    assert 'cat > "$REPO_ROOT/.env"' not in verifier_text
    assert '> "$REPO_ROOT/.env"' not in verifier_text
    # The volume is seeded inside the container namespace and handed to uid
    # 1000 with safe modes, never world-writable.
    assert "chown -R 1000:1000 /app/data" in verifier_text
    assert "chmod 750 /app/data" in verifier_text


def test_partial_output_check_runs_inside_the_container(verifier_text: str) -> None:
    # With a named volume there is no host-side data path to probe.
    assert 'exec -T worker test ! -e "/app/data/outputs/$JOB2"' in verifier_text


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
