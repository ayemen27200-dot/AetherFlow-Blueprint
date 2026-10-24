# AetherFlow Suite

> Local-first, distributed video-processing orchestrator — specification and implementation scaffold.

**Status:** this repository is a blueprint plus a starter scaffold. It is not a finished video-processing product. The API, persistent queue, agent protocol, upload validation, real inference executors, cloud quota enforcement, and end-to-end tests must be implemented and verified before production use.

## What the design covers

- Phone/Termux for lightweight local work.
- A VPS orchestrator for job coordination, persistent state and API access.
- Optional cloud/GPU workers only when cloud consent, tier policy and quota checks permit them.
- FFmpeg splitting, speech transcription, and subtitle/text inpainting through separate executors.

## Repository layout

- `D0_MASTER_BLUEPRINT/`: architecture and constraints.
- `D1_FOUNDATION/`: SQL, configuration policies, machine-readable OpenAPI 3.1 contract and runbook.
- `D2_MVP/` to `D5_HARDENING/`: implementation tasks and acceptance gates.
- `orchestrator/`, `agent/`, `executors/`: Python starter modules (some methods intentionally raise `NotImplementedError`).
- `docker/`, `config/`, `scripts/`, `tests/`: deployment scaffold, settings, bootstrap scripts and fixture.

## Local setup (scaffold only)

Requirements: Python 3.11+ and FFmpeg for media tests.

```bash
python -m venv .venv
# Linux/macOS/Termux
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts/init_wizard.py
pytest
python scripts/validate_specs.py
```

The CLI entry point currently supports initialization; job execution remains an implementation task. The source scaffold returns an explicit not-implemented response rather than pretending that jobs are processed.

## VPS with Docker Compose

1. Copy this repository to a supported Ubuntu VPS.
2. Run `bash scripts/install_vps.sh` as a normal user with `sudo` available. Review `.env` before exposing a real domain.
3. Set `DOMAIN` to a DNS name pointed at the VPS and set `ACME_EMAIL` to a real email in `.env`.
4. Expose only ports 80/443 publicly. The orchestrator is only exposed to the private Compose network.

For local development, `DOMAIN=localhost` uses Caddy's local certificate flow; your browser may require trusting the local CA. Do not use placeholder domains or email addresses in production.

## Phone with Termux

Run `bash scripts/install_phone.sh` from the repository checkout inside Termux. This installs system prerequisites and the Python scaffold. It does **not** silently download large model weights or build an unpinned `whisper.cpp` revision; follow the model-lock procedure before enabling transcription.

## OpenAPI and configuration validation

The canonical API contract is `D1_FOUNDATION/03_API_SPEC.yaml` (OpenAPI 3.1). Validate it with:

```bash
python scripts/validate_specs.py
openapi-spec-validator D1_FOUNDATION/03_API_SPEC.yaml  # available with the dev extra
```

## Security and model integrity

- Never commit `.env`, production tokens, private keys, uploaded media, SQLite state, or downloaded model weights.
- Remote job requests must reference a server-issued upload ID; never accept arbitrary filesystem paths from an untrusted client.
- A `FILL_ON_FIRST_RUN` hash is **not** a trusted integrity lock. Pin model revisions and verify SHA-256 values through a trusted process before production; do not silently trust the first download.
- Cloud processing requires explicit consent and policy/quota checks in the implementation. Documentation does not prove these controls exist in code.

## Development checks

```bash
pytest --cov
ruff check .
mypy orchestrator/ agent/ executors/
bandit -r orchestrator agent executors
pip-audit
python scripts/validate_specs.py
```

Record real command outputs and review findings before checking any acceptance gate. See `CONTRIBUTING.md`, `CHANGELOG.md`, and `MANIFEST.md`.

## License

MIT; see `LICENSE`. Model code, weights, datasets, and media may have separate terms.
