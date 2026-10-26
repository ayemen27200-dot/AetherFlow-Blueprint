# AetherFlow Suite

> Local-first, distributed video-processing orchestrator — blueprint, D2 MVP implementation and later-milestone scaffolds.

**Status:** the D2 MVP split pipeline is implemented and covered by automated tests: authenticated job API, durable SQLite queue with claim fencing, a real worker, an FFmpeg split executor with mandatory full-decode verification, two CLI execution paths and a hardened Compose stack. It is not a finished video-processing product: `POST /uploads`, the agent protocol, ASR/ProPainter inference executors, cloud quota enforcement and the web UI remain later-milestone (D3–D5) work, and the G2 performance and public-TLS acceptance items are still open. See `D2_MVP/07_G2_EVIDENCE_LEDGER.md` for the evidence state and `D2_MVP/02_IMPLEMENTATION_MAP.md` for per-file status.

## What the design covers

- Phone/Termux for lightweight local work.
- A VPS orchestrator for job coordination, persistent state and API access.
- Optional cloud/GPU workers only when cloud consent, tier policy and quota checks permit them.
- FFmpeg splitting, speech transcription, and subtitle/text inpainting through separate executors.

## Repository layout

- `D0_MASTER_BLUEPRINT/`: architecture and constraints.
- `D1_FOUNDATION/`: SQL, configuration policies, machine-readable OpenAPI 3.1 contract and runbook.
- `D2_MVP/` to `D5_HARDENING/`: implementation tasks and acceptance gates.
- `orchestrator/`, `agent/`, `executors/`: the D2 job API, SQLite queue, worker, upload store and FFmpeg executor are implemented; D3/D4 modules (decision engine, quota, key proxy, Whisper/ProPainter/Modal executors, agent protocol) intentionally still raise `NotImplementedError`.
- `docker/`, `config/`, `scripts/`, `tests/`: Compose deployment with a real worker service, settings, bootstrap/verification scripts and the unit/API/integration test suites plus the 5-second fixture.

## Local setup

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

The CLI supports the two D2 execution paths:

- `aetherflow worker run --upload-store DIR --output-dir DIR` — consumes
  jobs created through the authenticated API and stored durably in SQLite
  (the queue/worker path).
- `aetherflow split /abs/path/media.mp4 --allowed-base DIR --output-dir DIR`
  — runs the FFmpeg executor directly on a trusted local file; this path
  never touches the queue and rejects relative paths.

`POST /uploads` HTTP ingestion is not part of D2 — **no such HTTP endpoint
exists in the current API**. In the current stage, test and operator
workflows place objects into the upload store out-of-band (the store, not the
API, names the object); the API itself neither mints nor exposes upload ids.
The API accepts only the resulting opaque token in `JobSpec.file`, and the
store resolves it inside the trusted store directory. The D1 contract's
`POST /uploads` remains specified-but-unimplemented and belongs to D3.
Later-milestone executors still return an explicit not-implemented response
rather than pretending work happened.

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
- Remote job requests must reference the opaque upload token of an object already placed in the trusted store through the out-of-band D2 ingestion path; never accept arbitrary filesystem paths from an untrusted client. The API neither issues nor registers these tokens in D2 — that is the deferred D3 `POST /uploads` subsystem.
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
