# D2 — Implementation Map (G2)

The D2 MVP split pipeline is **implemented and covered by automated tests**;
this table describes the actual state of each file rather than the original
scaffold plan. The gate rule at the bottom still applies: green tests do not
by themselves close G2, and unproven acceptance items stay explicitly marked.

| Source file | Current state | Evidence / remaining boundary |
|---|---|---|
| `orchestrator/main.py` | Implemented. FastAPI app with fail-closed Bearer auth, `POST /jobs` (validated `JobSpec`, opaque-id only), `GET /jobs` (status filter), `GET /jobs/{id}` and an active `GET /health` backed by a real `SELECT 1`; public responses strip absolute/`..` output paths | API tests in `tests/api/` and the G2 E2E suite. `POST /uploads`, SSE, API-level cancellation and downloads are D3 and are not routed here |
| `orchestrator/queue.py` | Implemented. SQLite DDL with WAL, `foreign_keys=ON`, 5 s `busy_timeout`; atomic claim via `BEGIN IMMEDIATE` + `UPDATE ... RETURNING`; claim-generation fencing; statuses `queued → assigned → processing → completed/failed`; explicit, caller-fenced `recover_claimed_jobs` (no time-based auto-reschedule) | Unit tests in `tests/unit/test_queue_worker*.py`; DDL drift from `D1_FOUNDATION/02_DB_SCHEMA.sql` is guarded by tests. No schema change was required for the split path |
| `executors/base.py` | Implemented as the minimal abstract `Executor.run(job_spec, on_progress)` contract shared by all executors | Contract used by the worker and unit tests |
| `executors/ffmpeg.py` | Implemented. Argv-list subprocess only (no shell), `-c copy` stream copy (no re-encode), bounded timeout with process-group termination, `-progress pipe:1` parsing, trusted-base containment, transfer-spec size/extension limits; mandatory full null decode of the input and **every** output part (`-xerror`, exit 0 plus empty error stream); bounded concurrent verification with input/split overlap; any failure discards the job directory | `tests/unit/test_ffmpeg_executor.py` and `tests/integration/test_split_e2e.py`. Whisper/ProPainter/Modal executors in the same package remain intentional D3/D4 scaffolds |
| `agent/worker.py` | Implemented. `QueueWorker.process_one/run`, shared `_JobBudget`, `LocalUploadStore` resolution, stable path-free failure codes, root-relative output references, `serve()` loop and the `build_executor` factory | G2 E2E suite including worker-restart durability and corrupt-input failure |
| `agent/upload_store.py` | Implemented. Opaque upload-id → object resolution inside one trusted store directory; path traversal is rejected before the executor sees the input | Rejection tests in the queue/worker unit and E2E suites |
| `agent/cli.py` | Implemented, two explicitly separate paths: `aetherflow worker run` (durable queue consumer) and `aetherflow split FILE` (trusted local path, absolute path inside an allowed base; never touches the queue) | `test_g2_cli_worker_processes_one_queued_job`, `test_g2_cli_local_split_*` |
| `docker/docker-compose.yml` | Implemented. Real `aetherflow worker run` service sharing one SQLite file and one named volume with the orchestrator; Uvicorn `expose`-only, Caddy is the sole published entry point, non-root containers, `no-new-privileges`, `cap_drop: ALL` | Isolated eleven-section `scripts/verify_compose_stack.sh`, green in CI on a clean runner. A public Let's Encrypt certificate still requires a real DNS host (only the internal localhost certificate has been observed) |

## Upload boundary (why no `POST /uploads` is needed for this path)

Remote API `JobSpec.file` must be an opaque upload ID issued by
`POST /uploads`; server filesystem paths from untrusted clients are rejected
at the API (`400`) and again at the queue boundary. `POST /uploads` (HTTP
object ingestion) is a D3 endpoint and is deliberately not implemented in D2.
The D2 MVP ingests objects out-of-band into the same trusted store the worker
reads from (`LocalUploadStore`), which keeps the split job path fully
testable without building the larger upload subsystem. The local CLI path is
the only place filesystem paths are accepted, and only when absolute and
inside the caller-supplied trusted base.

## Explicitly not implemented in D2 (later milestones)

`POST /uploads`, SSE progress, API-driven cancellation, heartbeats and stall
recovery, the phone-agent control protocol (`agent/agent.py`), the decision
engine, environment selector, key proxy and quota manager, Whisper/ASR and
ProPainter executors, cloud dispatch and the web UI. These remain scaffolds
or specifications and must not be inferred as working from the D2 pipeline.

## Gate rule

A module importing successfully, the Docker image starting, or `/health`
returning an HTTP response does **not** mean G2 has passed. Implement the
acceptance checklist in `05_ACCEPTANCE_CRITERIA.md` and attach actual test
outputs before marking the gate complete. The living evidence ledger
(`07_G2_EVIDENCE_LEDGER.md`) records what is proven, what is only
sandbox-measured (the 15 s / 60 s long-media ceilings remain open pending a
target-host measurement with representative media), and what still needs
independent review.
