# D2 — Implementation Map (G2)

The initial source files now live in the repository tree so imports, package installation and CLI initialization can be checked directly. They are intentionally **scaffolds**, not a completed implementation.

| Source file | Current state | Required completion |
|---|---|---|
| `orchestrator/main.py` | FastAPI models/routes import; job operations return HTTP 501 | Authentication, upload validation, durable job creation/list/detail/cancel and health checks |
| `orchestrator/queue.py` | Interface only | SQLite schema, WAL, busy timeout, transactional enqueue/claim and recovery |
| `executors/base.py` | Interface | Stable executor contract, typed results, cancellation and errors |
| `executors/ffmpeg.py` | Interface with segment validation | FFprobe validation, argv-only FFmpeg, timeout, `-progress`, output validation |
| `agent/cli.py` | Initialization command works; split command reports not implemented | Local/offline execution and authenticated remote job submission |
| `docker/docker-compose.yml` | Container wiring scaffold | Test clean install, file permissions, deployment secrets, TLS, backup and rollback |

Remote API `JobSpec.file` must be an opaque upload ID issued by `POST /uploads`; do not accept arbitrary client-supplied server filesystem paths. Local CLI paths can be supported only in a separate trusted local execution path.

## Gate rule

A module importing successfully, the Docker image starting, or `/health` returning an HTTP response does **not** mean G2 has passed. Implement the acceptance checklist in `05_ACCEPTANCE_CRITERIA.md` and attach actual test outputs before marking the gate complete.
