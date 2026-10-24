# AetherFlow — Project Tree

The files below are starter scaffold files, not all implemented runtime functionality. The `D0–D5` dossiers remain the authoritative requirements and acceptance gates.

```text
aetherflow/
├── agent/                       # CLI and phone-agent scaffold
│   ├── __init__.py
│   ├── agent.py
│   └── cli.py
├── orchestrator/                # FastAPI and service logic scaffold
│   ├── __init__.py
│   ├── main.py
│   ├── queue.py
│   ├── decision_engine.py
│   ├── environment_selector.py
│   ├── quota_manager.py
│   └── key_proxy.py
├── executors/
│   ├── __init__.py
│   ├── base.py
│   ├── ffmpeg.py
│   ├── whisper_cpp.py
│   ├── faster_whisper.py
│   ├── propainter.py
│   └── modal_propainter.py
├── web/
│   ├── __init__.py
│   ├── templates/index.html
│   └── static/
├── config/
│   ├── rules.yaml
│   ├── error_policies.yaml
│   ├── config.yaml.example
│   ├── models.lock.json
│   └── file_transfer_spec.yaml
├── docker/
│   ├── docker-compose.yml
│   ├── Caddyfile
│   └── Dockerfile.orchestrator
├── scripts/
│   ├── init_wizard.py
│   ├── install_phone.sh
│   ├── install_vps.sh
│   ├── generate_test_fixture.sh
│   └── validate_specs.py
├── tests/
│   ├── test_scaffold_smoke.py
│   └── fixtures/sample_5s.mp4
├── D0_MASTER_BLUEPRINT/ ...
├── D1_FOUNDATION/ ...
├── D2_MVP/ ...
├── D3_CORE_ENGINE/ ...
├── D4_CLOUD_SCALE/ ...
├── D5_HARDENING/ ...
├── pyproject.toml
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── CHANGELOG.md
└── CONTRIBUTING.md
```

## Runtime caveat

The files make packaging and container topology inspectable, but many processing and persistence methods intentionally remain `NotImplementedError`/HTTP 501. Complete the stage-specific acceptance tests before treating the repository as operational.
