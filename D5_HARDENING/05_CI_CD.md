# D5 — CI/CD Pipeline

## 1. Test workflow
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
      - run: ruff check .
      - run: mypy orchestrator/ agent/ executors/
      - run: pytest --cov=orchestrator --cov=agent --cov=executors --cov-fail-under=70
```

## 2. Security workflow
```yaml
name: Security
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 0 * * 0"
jobs:
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install bandit pip-audit
      - run: bandit -r orchestrator/ agent/ executors/ -ll
      - run: pip-audit
      # TODO: add Trivy using a reviewed pinned action version
```

## 3. Accessibility workflow
شغّل التطبيق وbrowser test في CI، ثم axe-core/Playwright على الواجهة. لا توجه axe إلى API-only endpoint ولا تستخدم `sleep 10` كضمان جاهزية؛ استخدم readiness loop واحتفظ بالتقرير.

## 4. Release
- لا تبنِ صورة من Dockerfile غير موجود.
- تحقق من tests/security/a11y قبل tag.
- ثبّت إصدارات actions بدلاً من refs متحركة عند اعتماد الإنتاج.
- انشر ملاحظات إصدار مع hashes والـSBOM وسياسة التراجع.

## Branch protection وSemVer
- حماية main وPR ومراجعة ناجحة وفحوص إلزامية.
- `MAJOR.MINOR.PATCH` مع CHANGELOG.
- لا secrets في logs؛ استخدم أذونات GitHub Actions الأقل اللازمة.

## مخرجات
`.github/workflows/test.yml`, `security.yml`, `a11y.yml`, و`release.yml` بعد تنفيذها والتحقق من نجاحها.
