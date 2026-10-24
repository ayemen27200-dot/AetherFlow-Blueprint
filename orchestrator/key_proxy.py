"""Provider-key handling scaffold. Never log raw API keys."""

from __future__ import annotations


class KeyProxy:
    def store(self, provider: str, api_key: str) -> str:
        del provider, api_key
        raise NotImplementedError("Implement encrypted secret storage and rotation")
