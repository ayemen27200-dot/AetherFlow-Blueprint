"""Provider quota guard scaffold; fail closed when quota state is unknown."""

from __future__ import annotations


class QuotaManager:
    def allow(self, provider: str, estimated_cost: float = 0.0) -> bool:
        del provider, estimated_cost
        raise NotImplementedError("Implement provider quota accounting and circuit breakers")
