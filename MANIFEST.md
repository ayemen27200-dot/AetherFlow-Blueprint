# AetherFlow Suite — Manifest v1.2 (Blueprint + Scaffold)

> **Purpose:** hand-off package for staged implementation. This manifest describes the files that are present; it does not claim that the application has passed production acceptance.

## Package inventory

| Component | Files | Purpose |
|---|---:|---|
| D0 Master Blueprint | 2 | Project constitution and architecture |
| D1 Foundation | 12 | Plantree, SQLite schema, OpenAPI 3.1, policy/config examples, checkpoint/model lock, runbook, license and delivery notes |
| D2 MVP | 6 | MVP scope, implementation map, FFmpeg plan, SQLite queue, acceptance criteria and delivery note |
| D3 Core Engine | 7 | ASR, agent protocol, SSE, web UI, Tailscale, acceptance criteria and delivery note |
| D4 Cloud Scale | 7 | ProPainter/mask policy, Modal, quota management, LiteLLM, Telegram, acceptance criteria and delivery note |
| D5 Hardening | 7 | Test, security, accessibility, backup, CI/CD, release and delivery plans |
| Root metadata and packaging | 8 | README, pyproject, `.env.example`, `.gitignore`, changelog, contributing guide, MIT license and manifest |
| Python starter packages | 20 | `agent/`, `orchestrator/`, `executors/` and `web/` starter code |
| Runtime configuration | 5 | Example config, transfer spec, error policies, rules and model-lock copy |
| Docker deployment | 3 | Dockerfile, Caddy reverse proxy and Compose scaffold |
| Scripts | 6 | Phone/VPS installers, setup wizard, spec validator and fixture generator (+ package initializer) |
| Tests and fixture | 3 | Smoke tests, fixture instructions and synthetic 5-second MP4 |
| **Total ZIP payload** | **86** | Files only; directory entries are not included in this count |

The 41 Dossier files are the formal specification set. The remaining 45 files provide project metadata and starter implementation/scaffold assets.

## Canonical files

- `D0_MASTER_BLUEPRINT/ARCHITECTURE.md` — architecture and system boundaries.
- `D1_FOUNDATION/03_API_SPEC.yaml` — canonical OpenAPI 3.1 contract. Do not treat Markdown tables as the machine-readable API contract.
- `D1_FOUNDATION/02_DB_SCHEMA.sql` — proposed SQLite schema.
- `D1_FOUNDATION/09_models.lock.json` and `config/models.lock.json` — model-lock templates; any placeholder hash or unpinned model revision must be resolved and verified before release.
- `D4_CLOUD_SCALE/01_PROPAINTER_EXECUTOR.md` — mask generation, review and validation requirements.
- `scripts/init_wizard.py` — interactive setup; writes `.env` with mode `0600` and avoids printing secret values.
- `scripts/install_phone.sh` — installs Termux prerequisites and the Python scaffold. It intentionally does not download unpinned whisper.cpp sources or large model weights.
- `scripts/install_vps.sh` — Ubuntu + Docker Compose bootstrap. Review `.env`, DNS and firewall settings before exposing the service.

## Reproducible local checks

From the project root, with Python 3.11+:

```bash
python -m pip install -e ".[dev]"
python scripts/validate_specs.py
python -m compileall -q agent orchestrator executors web scripts
pytest -q
bash -n scripts/install_phone.sh scripts/install_vps.sh scripts/generate_test_fixture.sh
```

The included MP4 is a synthetic transport/splitting fixture; it does not test Arabic transcription, subtitle-mask accuracy, long-video behavior, or ProPainter visual quality. The validator checks YAML parsing and local OpenAPI `$ref` integrity; run `openapi-spec-validator D1_FOUNDATION/03_API_SPEC.yaml` for additional contract validation.

## Honest implementation status

This package is a **blueprint plus starter scaffold**, not a completed production video-processing product. Current source returns an explicit `501 Not Implemented` for job endpoints. The persistent job queue, upload handling and media validation, phone-agent protocol, real FFmpeg/ASR/ProPainter execution, cloud dispatch and quota enforcement, auth enforcement, operational metrics, UI behavior and end-to-end workflow still need implementation and acceptance testing.

The installers automate prerequisites and initial configuration only; successful installation does not mean the jobs will run. Do not claim “100% ready”, “production-ready”, or that any gate G1–G5 has passed until evidence is recorded for each acceptance criterion. Do not deploy a public endpoint until authentication, authorization, upload validation, rate limits, TLS/domain settings and firewall rules have been reviewed.

## Cost and credentials

- `free`, `paid_optional`, and `local_only` are configuration policy labels, not a guarantee that a cloud vendor provides a free quota.
- Cloud credentials are stored in `.env`; no integration should run merely because a flag is enabled. Runtime consent, quota enforcement and vendor-specific credentials must be implemented and tested.
- `FILL_ON_FIRST_RUN` / `PIN_BEFORE_RELEASE` are unresolved model-lock placeholders. Do not trust a hash obtained from the same untrusted download path as proof of integrity.
- Never commit `.env`, tokens, private keys, media files, database files, or model weights.

## Gate sequence

`D0 → D1 → G1 → D2 → G2 → D3 → G3 → D4 → G4 → D5 → G5 → versioned release`

A gate passes only when its acceptance evidence is checked in and reviewed; the presence of the corresponding documentation is not sufficient.
