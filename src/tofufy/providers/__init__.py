from __future__ import annotations

import importlib
from types import ModuleType

PROVIDER_MODULES = {
    "aws-route53": "tofufy.providers.route53",
    "cloudflare": "tofufy.providers.cloudflare",
}


def get_provider_module(provider: str) -> ModuleType:
    try:
        module_path = PROVIDER_MODULES[provider]
    except KeyError as exc:
        supported = ", ".join(sorted(PROVIDER_MODULES))
        raise ValueError(f"Unsupported provider {provider!r}. Expected one of: {supported}") from exc
    return importlib.import_module(module_path)
