"""Environment selection scaffold."""

from __future__ import annotations


class EnvironmentSelector:
    def select(
        self, capabilities: dict[str, object], requirements: dict[str, object]
    ) -> str | None:
        del capabilities, requirements
        raise NotImplementedError("Implement resource-aware phone/VPS/cloud selection")
