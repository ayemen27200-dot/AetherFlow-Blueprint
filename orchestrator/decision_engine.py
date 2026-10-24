"""Rule evaluation scaffold. Cloud consent and policy enforcement must be fail-closed."""

from __future__ import annotations


class DecisionEngine:
    def choose(self, job_spec: dict[str, object], environment_state: dict[str, object]) -> str:
        del job_spec, environment_state
        raise NotImplementedError("Implement rules.yaml evaluation and cloud-consent guards")
