from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

DEFAULT_PROVIDER = "aws-route53"
SUPPORTED_PROVIDERS = ("aws-route53", "cloudflare")

DEFAULT_CONFIG_FILES: Dict[str, str] = {
    "aws-route53": ".tofufy-config-aws-route53.json",
    "cloudflare": ".tofufy-config-cloudflare.json",
}

LEGACY_CONFIG_FALLBACKS: Dict[str, Sequence[str]] = {
    "aws-route53": ("config-route53.json",),
    "cloudflare": (),
}


def build_bootstrap_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=DEFAULT_PROVIDER,
        help="Select which remote service tofufy should export.",
    )
    parser.add_argument(
        "--config",
        help="Path to the provider-specific tofufy JSON config file.",
    )
    return parser


def resolve_provider_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = build_bootstrap_parser()
    args, _unknown = parser.parse_known_args(list(argv or ()))
    return args


def discover_config_path(provider: str, explicit_path: Optional[str] = None) -> Path:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported provider {provider!r}. Expected one of: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    if explicit_path:
        return Path(explicit_path)

    candidates = [DEFAULT_CONFIG_FILES[provider], *LEGACY_CONFIG_FALLBACKS.get(provider, ())]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path

    return Path(DEFAULT_CONFIG_FILES[provider])


def load_config_file(path: Path, required: bool = False) -> Dict[str, Any]:
    if not path:
        return {}
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Config file not found: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must define a JSON object")
    return data
