# AetherFlow Suite — Manifest v1.3 (Blueprint + Partial D2 Implementation)

> **Purpose:** hand-off package for staged implementation. This manifest describes the files that are present and the state of their implementation; it does not claim that the application has passed production acceptance or that any gate G1–G5 has passed.

## Package inventory

| Component | Files | Purpose |
|---|---:|---|
| D0 Master Blueprint | 2 | Project constitution and architecture |
| D1 Foundation | 12 | Plantree, SQLite schema, OpenAPI 3.1, policy/config examples, checkpoint/model lock, runbook, license and delivery notes |
| D2 MVP | 13 | MVP scope, implementation map, FFmpeg plan, SQLite queue, acceptance criteria, delivery note, living G2 evidence ledger and the Task016–Task021 acceptance, audit, criterion-review, verification-concurrency and independent-audit-evidence reports |
| D3 Core Engine | 7 | ASR, agent protocol, SSE, web UI, Tailscale, acceptance criteria and delivery note |
| D4 Cloud Scale | 7 | ProPainter/mask policy, Modal, quota management, LiteLLM, Telegram, acceptance criteria and delivery note |
| D5 Hardening | 7 | Test, security, accessibility, backup, CI/CD, release and delivery plans |
| Root metadata and packaging | 9 | README, `README_D2.md`, pyproject, `.env.example`, `.gitignore`, changelog, contributing guide, MIT license and manifest |
| Python packages | 21 | `agent/`, `orchestrator/`, `executors/` and `web/`; D2 queue/API/worker/FFmpeg paths are implemented, later-milestone executors remain scaffolds |
| Runtime configuration | 5 | Example config, transfer spec, error policies, rules and model-lock copy |
| Docker deployment | 4 | Dockerfile, Caddy reverse proxy, Compose stack (with a real worker service) and `.dockerignore` |
| Scripts | 9 | Phone/VPS installers, setup wizard, spec validator, JUnit summary, fixture generator, clean-host Compose verifier and reproducible long-media benchmark (+ package initializer) |
| Tests and fixture | 14 | Unit/API/integration suites, structural Compose configuration tests, verifier-isolation regression tests, media-toolchain guard, fixture instructions and the synthetic 5-second MP4 |
| **Total payload (excluding caches/VCS metadata)** | **128** | Files only; directory entries are not included |

The 41 Dossier files are the formal specification set (D0–D5 documents). The remaining files provide project metadata, implemented D2 components or starter/scaffold assets for later milestones.

## Canonical files

- `D0_MASTER_BLUEPRINT/ARCHITECTURE.md` — architecture and system boundaries.
- `D1_FOUNDATION/03_API_SPEC.yaml` — canonical OpenAPI 3.1 contract. Do not treat Markdown tables as the machine-readable API contract.
- `D1_FOUNDATION/02_DB_SCHEMA.sql` — proposed SQLite schema; the queue's embedded DDL is guarded against drift from it by tests.
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
ruff check .
pytest -q
bash -n scripts/install_phone.sh scripts/install_vps.sh scripts/generate_test_fixture.sh scripts/verify_compose_stack.sh scripts/benchmark_long_media.sh
```

For the full media pipeline (mirrors CI), install FFmpeg and FFprobe and
enable the toolchain guard so a missing binary fails the run instead of
skipping the integration tests; JUnit output and the job summary carry the
exact pass/fail/skip counts:

```bash
export AETHERFLOW_REQUIRE_MEDIA_BINARIES=1
pytest -q -ra --junitxml=test-results/junit.xml
python scripts/summarize_junit.py test-results/junit.xml
```

The included MP4 is a synthetic transport/splitting fixture; it does not test Arabic transcription, subtitle-mask accuracy, long-video behavior, or ProPainter visual quality. The validator checks YAML parsing and local OpenAPI `$ref` integrity; run `openapi-spec-validator D1_FOUNDATION/03_API_SPEC.yaml` for additional contract validation.

## Honest implementation status

This package is a blueprint **plus a partially implemented D2 MVP**, not a
completed production video-processing product. The breakdown below is
evidence-based; test counts are produced by the suite itself and archived as
a JUnit artifact in CI, not estimated.

### Implemented and covered by automated tests

- **Orchestrator HTTP API** (`orchestrator/main.py`): fail-closed Bearer
  authentication, `POST /jobs` / `GET /jobs` / `GET /jobs/{id}`, contract
  ordering and status filtering, opaque upload-id validation (server
  filesystem paths are never accepted), durable 503 mapping on SQLite
  failures, and a response serializer that strips any absolute or
  parent-escaping output path so API responses only expose root-relative
  output references.
- **Durable SQLite queue** (`orchestrator/queue.py`): WAL, busy timeout,
  atomic generation-fenced claims, type-filtered `dequeue`, fenced
  `complete_job` publishing verified outputs, explicit evidence-only
  recovery (no time-based automatic rescheduling), and fail-closed local
  path policy.
- **FFmpeg split executor** (`executors/ffmpeg.py`): argv-list subprocess
  only (shell use is structurally forbidden by a test), stream copy without
  re-encoding, bounded timeout and process-group cancellation, progress
  parsing, and real media validation — a duration probe plus a full decode
  requiring exit code 0 and an empty error stream, applied to inputs and
  every output part; partial output is discarded on failure.
- **Worker pipeline** (`agent/worker.py`, `agent/upload_store.py`,
  `agent/cli.py`): the end-to-end local path API → durable queue → worker →
  FFmpeg → verified outputs runs against the synthetic fixture, with a
  pool-wide shared job budget, a tested concurrency cap, stable path-free
  error codes, relative output references, graceful signal handling, and
  separate trusted-local (`aetherflow split`) and durable-queue
  (`aetherflow worker run`) execution channels. An automated test asserts
  failure logs never contain filesystem paths even when the underlying
  exception message does.
- **Active health endpoint** (`orchestrator/main.py`): `/health` performs a
  live database probe and returns 200 `healthy` or a generic 503 with no
  driver text, SQL or paths; the worker component honestly reports
  `not_monitored` until the D3 heartbeat exists.
- **CI**: installs real FFmpeg/FFprobe, hard-fails via the media toolchain
  guard, runs Ruff and the full suite with zero unexplained skips on the
  runner, and publishes JUnit results plus a job-summary count.
- **Operational evidence beyond in-process tests**: a two-process run
  (standalone Uvicorn API plus a separate `aetherflow worker run` process
  sharing one SQLite file, driven by real HTTP calls) demonstrated
  authentication rejection, safe-id validation, queued → completed with
  independently decodable relative-reference outputs, corrupt-input
  failure with no partial output, cross-process durability and status
  filtering. The transcript is referenced from PR #6; it is a local
  sandbox run, not a clean-host deployment.

### Implemented but not yet operationally proven

- The Compose stack now runs a real `aetherflow worker run` service sharing
  one SQLite file and one data volume with the orchestrator, with the API
  port unpublished and containers hardened. The CI `compose-smoke` job
  (`scripts/verify_compose_stack.sh`, no continue-on-error) passed
  end-to-end on a clean ubuntu-latest runner (run 40150132081) with the
  earlier ten-section script: image build, live DB health, queued→completed
  over real HTTP, independent output decode inside the worker container,
  corrupt-input failure without partial output, worker restart durability,
  leak scans and confirmation that port 8000 is never host-published. An
  independent audit then found the verifier could delete repository data and
  tear down a fixed-name project, and that the VPS installer had a 0777
  fallback; all three were remediated (fully isolated mktemp working
  directory, unique per-run Compose project/image/named volume (compatible
  with user-namespace Docker), 0600 env file, non-root uid-1000 seeding at
  0750/0640 with no world-writable path, an explicit non-interference
  section, and nine regression tests in
  `tests/unit/test_verifier_isolation.py`). The isolated eleven-section
  script passes end-to-end on the runner's user-namespace Docker daemon
  (run 40154243602 on head `818cd7a`). A public
  Let's Encrypt certificate still needs a real DNS host; only the internal
  localhost certificate has been observed on the runner.
- Long-media performance has been measured honestly in the two-core build
  environment via `scripts/benchmark_long_media.sh`, which now runs three
  full-path repeats per duration and records median/min: after the Task020
  bounded-concurrent-verification optimization the medians are 18.50s for
  the 30-minute case (runs 17.68/18.75/18.50, target <15s) and 68.95s for
  the two-hour case (runs 71.34/68.95/68.08, target <60s); pure
  stream-copy split alone is 0.91s/5.84s, so the mandatory decode
  verification dominates. The script records media characteristics and
  accepts representative real files via
  `BENCH_30M_INPUT`/`BENCH_2H_INPUT`. Neither target is met on this host,
  and the VPS run (or a documented criterion review with the optimization
  plan)
  that can close the criteria remains pending. Full matrix:
  `D2_MVP/07_G2_EVIDENCE_LEDGER.md`.
- A full secret-leak audit of every component has not been performed;
  automated coverage currently targets the worker failure path and the
  container-log token scan inside compose-smoke.

### Not implemented (later milestones)

- `POST /uploads`, SSE progress, API-driven cancellation, agent heartbeats
  and the phone-agent control protocol (`agent/agent.py` remains a
  scaffold), Whisper/ASR and ProPainter executors (still scaffolds), cloud
  dispatch, quota enforcement, automatic stall recovery, and the web UI.
- No G2 acceptance is declared here: the implemented pipeline items have
  automated evidence, but the gate remains open pending independent review
  of that evidence and the unproven operational items above.

The installers automate prerequisites and initial configuration only; successful installation does not mean the jobs will run. Do not claim “100% ready”, “production-ready”, or that any gate G1–G5 has passed until evidence is recorded for each acceptance criterion. Do not deploy a public endpoint until authentication, authorization, upload validation, rate limits, TLS/domain settings and firewall rules have been reviewed.

## Cost and credentials

- `free`, `paid_optional`, and `local_only` are configuration policy labels, not a guarantee that a cloud vendor provides a free quota.
- Cloud credentials are stored in `.env`; no integration should run merely because a flag is enabled. Runtime consent, quota enforcement and vendor-specific credentials must be implemented and tested.
- `FILL_ON_FIRST_RUN` / `PIN_BEFORE_RELEASE` are unresolved model-lock placeholders. Do not trust a hash obtained from the same untrusted download path as proof of integrity.
- Never commit `.env`, tokens, private keys, media files, database files, or model weights.

## Gate sequence

`D0 → D1 → G1 → D2 → G2 → D3 → G3 → D4 → G4 → D5 → G5 → versioned release`

A gate passes only when its acceptance evidence is checked in and reviewed; the presence of the corresponding documentation is not sufficient.
