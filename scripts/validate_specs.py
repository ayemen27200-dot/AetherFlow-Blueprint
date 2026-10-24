#!/usr/bin/env python3
"""Validate the YAML syntax and basic local-reference integrity of the specs."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("PyYAML is required. Install with: python -m pip install -e '.[dev]'", file=sys.stderr)
    raise SystemExit(2) from None

ROOT = Path(__file__).resolve().parents[1]
YAML_FILES = [
    ROOT / "D1_FOUNDATION" / "03_API_SPEC.yaml",
    ROOT / "D1_FOUNDATION" / "04_rules.yaml",
    ROOT / "D1_FOUNDATION" / "05_error_policies.yaml",
    ROOT / "D1_FOUNDATION" / "06_config.yaml.example",
    ROOT / "config" / "rules.yaml",
    ROOT / "config" / "error_policies.yaml",
    ROOT / "config" / "config.yaml.example",
    ROOT / "config" / "file_transfer_spec.yaml",
    ROOT / "docker" / "docker-compose.yml",
]

def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise ValueError(f"Only local JSON pointers are supported here: {pointer}")
    current = document
    for raw_token in pointer[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ValueError(f"Unresolved reference: {pointer}")
    return current

def main() -> int:
    parsed: dict[Path, Any] = {}
    errors: list[str] = []
    for path in YAML_FILES:
        if not path.exists():
            errors.append(f"missing: {path.relative_to(ROOT)}")
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                parsed[path] = yaml.safe_load(stream)
        except Exception as exc:  # parse errors should be reported in this validation tool
            errors.append(f"YAML parse error in {path.relative_to(ROOT)}: {exc}")

    api_path = ROOT / "D1_FOUNDATION" / "03_API_SPEC.yaml"
    api = parsed.get(api_path)
    if isinstance(api, dict):
        if api.get("openapi") != "3.1.0":
            errors.append("OpenAPI document must declare openapi: 3.1.0")
        if not isinstance(api.get("paths"), dict) or not api["paths"]:
            errors.append("OpenAPI document is missing paths")
        refs: list[str] = []
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    refs.append(ref)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(api)
        for ref in refs:
            try:
                resolve_pointer(api, ref)
            except ValueError as exc:
                errors.append(str(exc))

    lock_path = ROOT / "D1_FOUNDATION" / "09_models.lock.json"
    try:
        with lock_path.open(encoding="utf-8") as stream:
            json.load(stream)
    except Exception as exc:
        errors.append(f"Invalid JSON in model lock: {exc}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"OK: parsed {len(parsed)} YAML files; OpenAPI 3.1 local references resolve; "
        "model lock JSON parses."
    )
    print(
        "Note: this is not a replacement for openapi-spec-validator "
        "or a runtime integration test."
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
