# D5 — Test Suite Specification

## الهدف
تغطية line coverage ≥70% كحد أولي، مع تغطية قوية لمسارات الأمان والحالات الطرفية. النسبة وحدها لا تثبت جودة الاختبارات.

## الأدوات
- pytest, pytest-asyncio, pytest-cov
- httpx / ASGI transport
- respx أو أداة mocking مناسبة
- freezegun عند الحاجة للوقت

## هيكل مقترح
```text
tests/
├── conftest.py
├── unit/
│   ├── test_decision_engine.py
│   ├── test_environment_selector.py
│   ├── test_quota_manager.py
│   ├── test_notifier.py
│   ├── test_queue.py
│   └── test_rules_loader.py
├── integration/
│   ├── test_split_e2e.py
│   ├── test_transcription_e2e.py
│   ├── test_text_removal_e2e.py
│   └── test_sse.py
├── e2e/test_full_flow.py
└── fixtures/
    ├── sample_5s.mp4
    ├── sample_30s_ar.mp4
    ├── expected_srt.txt
    └── rules_test.yaml
```

## أمثلة توجيه
```python
import pytest
from orchestrator.decision_engine import evaluate

@pytest.mark.parametrize("env,expected", [
    ({"environment_type": "phone", "ram_available_gb": 6,
      "battery_pct": 80, "thermal_status": "normal", "duration_min": 20,
      "language": "ar"}, "whisper_cpp"),
    ({"environment_type": "vps", "language": "ar"}, "faster_whisper"),
])
def test_transcription_routing(env, expected):
    result = evaluate({"task": "transcription", **env})
    assert result.executor == expected


def test_default_fallback():
    result = evaluate({"task": "unknown_task"})
    assert result.executor == "error"
```

يجب أن تتطابق أسماء الحقول مع العقد الفعلي لـ`rules.yaml`؛ المثال يحتاج تكييفاً عند التنفيذ.

## E2E split
استخدم fixture قصيرة في CI. فيديو ساعتين اختبار nightly/اختياري بسبب حجم الاختبار، ولا تجعل CI يعتمد على تنزيل media غير مرخصة.

## CI
```yaml
name: Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: pytest --cov=orchestrator --cov=agent --cov=executors --cov-fail-under=70
```

ثبّت إصدارات Actions، وأضف lint/type-check/validation للـYAML وSQL وOpenAPI. لا تستثنِ من التغطية إلا boilerplate مبرراً.
