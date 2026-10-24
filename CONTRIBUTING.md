# Contributing to AetherFlow

## Before opening a change

1. Read `D0_MASTER_BLUEPRINT/ARCHITECTURE.md` and the relevant Dossier acceptance criteria.
2. Keep changes scoped to one stage/gate where practical; update the spec and code together when a contract changes.
3. Never commit credentials, `.env`, private media, model weights, local database files, or real production logs.
4. Never bypass `cloud_consent`, access control, quota guards, media size limits, or path validation to make a test pass.
5. Verify third-party model/code licenses before redistributing assets. Pin model revisions and checksums before production.

## Development workflow

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/init_wizard.py
python scripts/validate_specs.py
pytest --cov
ruff check .
mypy orchestrator/ agent/ executors/
```

Some scaffold tests or job features will remain incomplete until their implementation tasks are finished. Do not mark a gate passed merely because a stub imports or a container starts.

## Pull requests

- Use a topic branch (`feat/...`, `fix/...`, `docs/...`, or `test/...`).
- Include the problem statement, design impact, tests run, and any known limitations.
- Include migrations/API contract updates when required.
- Provide before/after evidence for media processing or UI changes without publishing private content.
- Address security and accessibility findings explicitly; do not claim formal certification without an actual assessment.

## Commit and release notes

Use clear, imperative commit messages. Update `CHANGELOG.md` for user-visible behavior or contract changes. A release requires reviewed acceptance evidence for G1–G5 and an explicit rollback plan.
