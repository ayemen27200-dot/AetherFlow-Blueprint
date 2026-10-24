#!/usr/bin/env python3
"""Interactive AetherFlow setup wizard.

Creates a private .env and an intent/configuration file without printing secrets.
The config flags describe intended integrations; they do not imply that the
corresponding runtime integrations have already been implemented.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required. Install with: python -m pip install -e '.[dev]'", file=sys.stderr)
    raise SystemExit(2)

BANNER = r"""
╔═══════════════════════════════════════════════════════════╗
║          AetherFlow Suite — Setup Wizard                 ║
║          Local-first video-processing scaffold           ║
╚═══════════════════════════════════════════════════════════╝
"""


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def ask(prompt: str, default: str = "", *, secret: bool = False, interactive: bool = True) -> str:
    if not interactive:
        return default
    suffix = f" [{default}]" if default and not secret else ""
    raw = getpass.getpass(f"{prompt}{suffix}: ") if secret else input(f"{prompt}{suffix}: ")
    value = raw.strip()
    return value or default


def ask_bool(prompt: str, default: bool, *, interactive: bool) -> bool:
    if not interactive:
        return default
    hint = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    if raw in {"y", "yes", "نعم", "ن"}:
        return True
    if raw in {"n", "no", "لا", "ل"}:
        return False
    print("Unrecognized response; keeping the default.")
    return default


def ask_choice(prompt: str, choices: list[str], default: str, *, interactive: bool) -> str:
    if not interactive:
        return default
    print(f"\n{prompt}")
    for index, choice in enumerate(choices, 1):
        marker = " (default)" if choice == default else ""
        print(f"  {index}. {choice}{marker}")
    raw = input(f"Choose [1-{len(choices)}] or Enter: ").strip()
    if not raw:
        return default
    try:
        selected = choices[int(raw) - 1]
    except (ValueError, IndexError):
        print(f"Invalid choice; using {default}.")
        return default
    return selected


def generated_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def locate_project_root() -> Path:
    cwd = Path.cwd().resolve()
    candidates = [cwd, *cwd.parents, Path(__file__).resolve().parents[1]]
    root = next(
        (candidate for candidate in candidates
         if (candidate / ".env.example").is_file() and (candidate / "config").is_dir()),
        None,
    )
    if root is None:
        raise SystemExit(
            "Run this wizard from the AetherFlow project root (the directory containing .env.example)."
        )
    return root


def write_private_file(path: Path, contents: str) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(contents, encoding="utf-8")
        os.chmod(temp_path, 0o600)
        temp_path.replace(path)
        os.chmod(path, 0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--non-interactive", action="store_true", help="Use safe defaults; never prompt")
    parser.add_argument("--domain", default=None, help="Public domain or localhost")
    parser.add_argument("--acme-email", default=None, help="Email used for TLS certificate issuance")
    parser.add_argument("--tier", choices=["free", "paid_optional", "local_only"], default=None)
    parser.add_argument("--force-secrets", action="store_true", help="Rotate generated server/agent secrets")
    parser.add_argument("--force-config", action="store_true", help="Replace an existing config/config.yaml")
    args = parser.parse_args(argv)
    interactive = not args.non_interactive

    root = locate_project_root()
    env_path = root / ".env"
    config_dir = root / "config"
    config_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    existing_env = parse_env(env_path)
    existing_config_path = config_dir / "config.yaml"

    print(BANNER)
    tier_default = args.tier or "free"
    tier = args.tier or ask_choice(
        "1/6 — Cost policy tier:", ["free", "paid_optional", "local_only"], tier_default,
        interactive=interactive,
    )
    use_phone = ask_bool("2/6 — Configure a Termux phone agent?", False, interactive=interactive)
    phone_host = ask("Phone SSH host/name", existing_env.get("PHONE_HOST", "phone"), interactive=interactive) \
        if use_phone else ""

    use_modal = ask_bool(
        "3/6 — Enable the optional Modal integration? (usage/cost must be verified separately)",
        existing_env.get("ENABLE_MODAL", "false").lower() == "true",
        interactive=interactive,
    )
    modal_id = existing_env.get("MODAL_TOKEN_ID", "")
    modal_secret = existing_env.get("MODAL_TOKEN_SECRET", "")
    if use_modal:
        if not modal_id:
            modal_id = ask("Modal token ID (leave empty to configure later)", secret=True, interactive=interactive)
        if not modal_secret:
            modal_secret = ask("Modal token secret (leave empty to configure later)", secret=True, interactive=interactive)

    use_telegram = ask_bool(
        "4/6 — Enable Telegram notifications?",
        existing_env.get("ENABLE_TELEGRAM", "false").lower() == "true",
        interactive=interactive,
    )
    telegram_token = existing_env.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat = existing_env.get("TELEGRAM_CHAT_ID", "")
    if use_telegram:
        if not telegram_token:
            telegram_token = ask("Telegram bot token (leave empty to configure later)", secret=True, interactive=interactive)
        if not telegram_chat:
            telegram_chat = ask("Telegram chat ID", interactive=interactive)

    domain_default = args.domain or existing_env.get("DOMAIN") or "localhost"
    email_default = args.acme_email or existing_env.get("ACME_EMAIL") or "admin@example.com"
    domain = args.domain or ask("5/6 — Domain (localhost for development)", domain_default, interactive=interactive)
    acme_email = args.acme_email or ask("ACME contact email", email_default, interactive=interactive)

    print("\n6/6 — Generating or preserving security values...")
    generated = {
        "FERNET_KEY": generated_fernet_key(),
        "BEARER_TOKEN": secrets.token_urlsafe(32),
        "AGENTS_AUTH_TOKEN": secrets.token_urlsafe(32),
    }
    env_values = {
        **existing_env,
        "FERNET_KEY": generated["FERNET_KEY"] if args.force_secrets or not existing_env.get("FERNET_KEY") else existing_env["FERNET_KEY"],
        "BEARER_TOKEN": generated["BEARER_TOKEN"] if args.force_secrets or not existing_env.get("BEARER_TOKEN") else existing_env["BEARER_TOKEN"],
        "AGENTS_AUTH_TOKEN": generated["AGENTS_AUTH_TOKEN"] if args.force_secrets or not existing_env.get("AGENTS_AUTH_TOKEN") else existing_env["AGENTS_AUTH_TOKEN"],
        "DATABASE_PATH": existing_env.get("DATABASE_PATH", "./data/aetherflow.db"),
        "HOST": existing_env.get("HOST", "127.0.0.1"),
        "PORT": existing_env.get("PORT", "8000"),
        "LOG_LEVEL": existing_env.get("LOG_LEVEL", "INFO"),
        "DOMAIN": domain,
        "ACME_EMAIL": acme_email,
        "PHONE_HOST": phone_host,
        "ENABLE_MODAL": str(use_modal).lower(),
        "MODAL_TOKEN_ID": modal_id,
        "MODAL_TOKEN_SECRET": modal_secret,
        "ENABLE_TELEGRAM": str(use_telegram).lower(),
        "TELEGRAM_BOT_TOKEN": telegram_token,
        "TELEGRAM_CHAT_ID": telegram_chat,
        "LITELLM_MASTER_KEY": existing_env.get("LITELLM_MASTER_KEY", ""),
    }
    env_text = (
        "# Generated by scripts/init_wizard.py; keep private and never commit.\n"
        "# Optional integration values are stored here, not in config/config.yaml.\n"
        + "".join(f"{key}={env_values[key]}\n" for key in [
            "FERNET_KEY", "BEARER_TOKEN", "AGENTS_AUTH_TOKEN", "DATABASE_PATH", "HOST", "PORT",
            "LOG_LEVEL", "DOMAIN", "ACME_EMAIL", "PHONE_HOST", "ENABLE_MODAL", "MODAL_TOKEN_ID",
            "MODAL_TOKEN_SECRET", "ENABLE_TELEGRAM", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "LITELLM_MASTER_KEY",
        ])
    )
    write_private_file(env_path, env_text)

    if existing_config_path.exists() and not args.force_config:
        replace_config = ask_bool(
            "config/config.yaml already exists. Replace it with the choices above?", False,
            interactive=interactive,
        )
    else:
        replace_config = True

    if replace_config:
        config = {
            "cost_policy": {
                "tier": tier,
                "max_monthly_cost_usd": 30 if tier == "paid_optional" else 0,
                "require_confirm_for_paid": True,
            },
            "limits": {
                "max_file_size_mb": 2000,
                "max_concurrent_per_environment": 1,
                "job_events_retention_days": 30,
            },
            "retention": {"default_hours": 24, "on_delete_after_completion": "immediate", "delay_hours": 0},
            "agents": {"auth_token": "", "phone_host": phone_host or None},
            "environment_selection": {
                "order": (["phone", "vps"] if tier == "local_only" and use_phone else ["vps"]
                          if tier == "local_only" else (["phone", "vps", "cloud"] if use_phone else ["vps", "cloud"])),
                "per_job_override": True,
            },
            "integrations": {
                "telegram": {"enabled": use_telegram, "bot_token": "", "chat_id": ""},
                "modal": {"enabled": use_modal, "token_id": "", "token_secret": ""},
            },
            "notes": [
                "Secret values belong in .env; do not copy credentials into this YAML file.",
                "Integration flags are configuration intent, not proof that runtime adapters are implemented.",
            ],
        }
        serialized = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        write_private_file(existing_config_path, serialized)
        print(f"Wrote configuration: {existing_config_path}")
    else:
        print(f"Preserved existing configuration: {existing_config_path}")

    (root / "data").mkdir(mode=0o750, exist_ok=True)
    (root / "logs").mkdir(mode=0o750, exist_ok=True)
    (root / "tmp").mkdir(mode=0o750, exist_ok=True)

    print("\nSetup files created. Secret values were not printed.")
    print(f"  Cost policy : {tier}")
    print(f"  Phone       : {'configured' if use_phone else 'disabled'}")
    print(f"  Modal       : {'enabled in config' if use_modal else 'disabled'}")
    if use_modal and (not modal_id or not modal_secret):
        print("  WARNING     : Modal credentials are still blank; configure them in .env before use.")
    print(f"  Telegram    : {'enabled in config' if use_telegram else 'disabled'}")
    if use_telegram and not telegram_token:
        print("  WARNING     : Telegram token is blank; configure it in .env before use.")
    print(f"  Domain      : {domain}")
    if domain == "localhost":
        print("  Development : set a real DNS name and ACME_EMAIL for public HTTPS deployment.")
    print("  Next        : review .env, then run the test and spec-validation commands in README.md.")
    print("  Notice      : job execution, agent communication and integrations remain scaffold work.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nSetup cancelled; existing secrets were not intentionally rotated.", file=sys.stderr)
        raise SystemExit(130)
