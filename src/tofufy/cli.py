#!/usr/bin/env python3
"""Provider-aware entrypoint for tofufy exporters."""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

from tofufy.config import (
    DEFAULT_PROVIDER,
    SUPPORTED_PROVIDERS,
    discover_config_path,
    load_config_file,
    resolve_provider_args,
)
from tofufy.providers import get_provider_module


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    bootstrap_args = resolve_provider_args(argv_list)
    provider_module = get_provider_module(bootstrap_args.provider)
    config_path = discover_config_path(bootstrap_args.provider, bootstrap_args.config)
    config_data = load_config_file(config_path, required=True)
    normalized_config: Dict[str, object] = provider_module.prepare_config(config_data)

    parser = argparse.ArgumentParser(description=provider_module.DESCRIPTION)
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=bootstrap_args.provider,
        help=f"Select which remote service tofufy should export (default: {DEFAULT_PROVIDER}).",
    )
    parser.add_argument(
        "--config",
        default=str(config_path),
        help="Path to the provider-specific tofufy JSON config file.",
    )
    provider_module.configure_parser(parser, normalized_config)
    args = parser.parse_args(argv_list)
    return provider_module.finalize_args(args, normalized_config, config_data, Path(config_path))


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = parse_args(argv)
        provider_module = get_provider_module(args.provider)
    except (OSError, ValueError) as exc:
        print(f"Failed to parse arguments: {exc}", file=sys.stderr)
        return 1

    return provider_module.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
