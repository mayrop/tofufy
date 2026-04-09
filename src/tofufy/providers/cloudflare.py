from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Pattern

from tofufy.cloudflare_api import CloudflareAPIClient, load_credentials
from tofufy.providers.cloudflare_models import (
    MANAGED_TRANSFORM_DESCRIPTIONS,
    RULESET_PHASE_COMMENTS,
    RULESET_PHASE_FILENAMES,
    RULESET_PHASE_RESOURCE_NAMES,
    PRESERVE_LOCAL_SETTING_IDS,
    SECTION_FILE_HEADERS,
    SECTION_SETTING_COMMENTS,
    SECTION_SETTING_IDS,
    SUPPORTED_SETTINGS_FILES,
    CloudflareDNSRecord,
    CloudflareListDefinition,
    CloudflareRulesetDefinition,
    CloudflareZoneSetting,
    compute_relative_hostname,
    finalize_dns_key,
    group_zone_settings,
    infer_list_internal_type,
    normalize_list_items,
    sanitize_identifier,
)
from tofufy.render import render_attribute_block, to_hcl_literal

DESCRIPTION = "Generate Terraform/OpenTofu-friendly configuration for Cloudflare resources."

DEFAULT_ARGUMENTS: Dict[str, Any] = {
    "account_id": None,
    "zone_id": None,
    "zone_name": None,
    "output_dir": ".",
    "dns_file": "config-dns-records.tf",
    "imports_file": "imports.tf",
    "only_hostnames": [],
    "only_sections": [],
    "add_comments": False,
}


def normalize_config_key(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("--"):
        text = text[2:]
    return text.replace("-", "_")


def normalize_config_data(config_data: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = dict(DEFAULT_ARGUMENTS)
    for key, value in config_data.items():
        normalized_key = normalize_config_key(key)
        if normalized_key == "arguments" and isinstance(value, dict):
            for nested_key, nested_value in value.items():
                normalized[normalize_config_key(nested_key)] = nested_value
        else:
            normalized[normalized_key] = value
    return normalized


def prepare_config(config_data: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_config_data(config_data)


def _coerce_string_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [token.strip() for token in raw.split(",") if token.strip()]
    try:
        iterator = iter(raw)
    except TypeError:
        text = str(raw).strip()
        return [text] if text else []
    return [str(entry).strip() for entry in iterator if str(entry).strip()]


def parse_hostname_patterns(raw: Any) -> List[Pattern[str]]:
    patterns: List[Pattern[str]] = []
    for entry in _coerce_string_list(raw):
        patterns.append(re.compile(entry, re.IGNORECASE))
    return patterns


def parse_sections(raw: Any) -> List[str]:
    sections = _coerce_string_list(raw)
    if not sections:
        return list(SUPPORTED_SETTINGS_FILES)
    unknown = sorted(set(sections) - set(SUPPORTED_SETTINGS_FILES))
    if unknown:
        raise ValueError(f"Unsupported Cloudflare sections: {', '.join(unknown)}")
    return sections


def configure_parser(parser: argparse.ArgumentParser, normalized_config: Dict[str, Any]) -> None:
    parser.add_argument(
        "--only-hostnames",
        help="Comma-separated list of fully-qualified hostnames; only matching DNS records will be exported.",
    )
    parser.add_argument(
        "--only-sections",
        help="Comma-separated list of Cloudflare settings sections to export.",
    )
    parser.add_argument(
        "--add-comments",
        action="store_true",
        help="Emit explanatory comments in generated Cloudflare Terraform files.",
    )
    parser.set_defaults(
        **{
            key: value
            for key, value in normalized_config.items()
            if key in {"only_hostnames", "only_sections", "add_comments"}
        }
    )


def finalize_args(
    args: argparse.Namespace,
    normalized_config: Dict[str, Any],
    config_data: Dict[str, Any],
    config_path: Path,
) -> argparse.Namespace:
    for key, value in normalized_config.items():
        if key in {"only_hostnames", "only_sections", "add_comments"}:
            continue
        setattr(args, key, value)
    args._config_data = config_data
    args.config_file = str(config_path)
    args.config_dir = str(config_path.parent.resolve())
    return args


def run(args: argparse.Namespace) -> int:
    if not args.zone_id:
        print("Cloudflare export requires zone_id in the config.", file=sys.stderr)
        return 1
    if not args.account_id:
        print("Cloudflare export requires account_id in the config.", file=sys.stderr)
        return 1

    try:
        sections = parse_sections(getattr(args, "only_sections", None))
        hostname_patterns = parse_hostname_patterns(getattr(args, "only_hostnames", None))
        client = CloudflareAPIClient(load_credentials())
    except ValueError as exc:
        print(f"Cloudflare provider validation failed: {exc}", file=sys.stderr)
        return 1

    config_dir = Path(getattr(args, "config_dir", "."))
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (config_dir / output_dir).resolve()
    add_comments = bool(getattr(args, "add_comments", False))

    try:
        dns_records = normalize_dns_records(
            client.list_dns_records(args.zone_id),
            zone_id=args.zone_id,
            zone_name=str(getattr(args, "zone_name", "") or ""),
            include_patterns=hostname_patterns,
        )
        write_dns_file(dns_records, output_dir / args.dns_file)
    except Exception as exc:
        print(f"Failed to export Cloudflare DNS: {exc}", file=sys.stderr)
        return 1

    try:
        grouped_settings = group_zone_settings(client.list_zone_settings(args.zone_id), sections)
    except Exception as exc:
        print(f"Failed to export Cloudflare settings: {exc}", file=sys.stderr)
        return 1

    try:
        list_definitions = normalize_account_lists(
            client.list_account_lists(args.account_id),
            client,
            args.account_id,
        )
        write_generated_list_files(list_definitions, output_dir, add_comments=add_comments)
    except Exception as exc:
        print(f"Failed to export Cloudflare lists: {exc}", file=sys.stderr)
        return 1

    try:
        ruleset_definitions = normalize_zone_rulesets(
            client.list_zone_rulesets(args.zone_id),
            client,
            args.zone_id,
        )
    except Exception as exc:
        print(f"Failed to export Cloudflare rulesets: {exc}", file=sys.stderr)
        return 1

    try:
        transforms = normalize_managed_transforms(client.get_managed_transforms(args.zone_id))
        write_managed_transforms_file(transforms, output_dir / "cloudflare-rules.tf", add_comments=add_comments)
    except Exception as exc:
        print(f"Failed to export Cloudflare managed transforms: {exc}", file=sys.stderr)
        return 1

    try:
        logpush_jobs = normalize_logpush_jobs(
            client.list_zone_logpush_jobs(args.zone_id),
            client,
            args.zone_id,
        )
        write_logpush_file(logpush_jobs, output_dir / "cloudflare-analytics.tf", add_comments=add_comments)
    except Exception as exc:
        print(f"Failed to export Cloudflare logpush jobs: {exc}", file=sys.stderr)
        return 1

    try:
        write_imports_file(
            dns_records=dns_records,
            zone_id=args.zone_id,
            list_definitions=list_definitions,
            account_id=args.account_id,
            ruleset_definitions=ruleset_definitions,
            grouped_settings=grouped_settings,
            transforms=transforms,
            logpush_jobs=logpush_jobs,
            path=output_dir / args.imports_file,
        )
        write_settings_files(
            grouped_settings=grouped_settings,
            list_definitions=list_definitions,
            ruleset_definitions=ruleset_definitions,
            output_dir=output_dir,
            add_comments=add_comments,
        )
        cleanup_stale_generated_artifacts(output_dir)
    except Exception as exc:
        print(f"Failed to render Cloudflare Terraform files: {exc}", file=sys.stderr)
        return 1

    print(f"Exported Cloudflare dns, settings, lists, rulesets, managed_transforms and logpush for zone {args.zone_id} into {output_dir}")
    return 0


def normalize_dns_records(
    raw_records: List[Dict[str, Any]],
    zone_id: str,
    zone_name: str = "",
    include_patterns: List[Pattern[str]] | None = None,
) -> List[CloudflareDNSRecord]:
    sorted_records = sorted(
        raw_records,
        key=lambda item: (
            str(item.get("name") or "").lower(),
            str(item.get("type") or "").lower(),
            str(item.get("content") or ""),
        ),
    )
    counts: Dict[str, int] = {}
    normalized: List[CloudflareDNSRecord] = []
    for record in sorted_records:
        name = str(record.get("name") or "").rstrip(".")
        record_type = str(record.get("type") or "").upper()
        hostname = name
        if include_patterns and not any(pattern.search(hostname) for pattern in include_patterns):
            continue
        relative_name = compute_relative_hostname(hostname, zone_name)
        base_for_count = f"{relative_name}|{record_type}"
        counts[base_for_count] = counts.get(base_for_count, 0) + 1
        key = finalize_dns_key(relative_name, record_type, counts[base_for_count])
        normalized.append(
            CloudflareDNSRecord(
                key=key,
                name=name,
                record_type=record_type,
                content=str(record.get("content") or ""),
                proxied=bool(record.get("proxied", True)),
                ttl=int(record.get("ttl") or 1),
                tags=sorted(str(tag) for tag in (record.get("tags") or [])),
                settings=dict(record.get("settings") or {"flatten_cname": False}),
                record_id=str(record.get("id") or ""),
            )
        )
    return normalized


def write_dns_file(records: List[CloudflareDNSRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = ["locals {", "  dns_records = {"]
    for record in records:
        lines.append(f'    {json.dumps(record.key)} = {{')
        lines.append(f"      name = {json.dumps(record.name)}")
        lines.append(f"      type = {json.dumps(record.record_type)}")
        lines.append(f"      content = {json.dumps(record.content)}")
        if record.proxied is not True:
            lines.append(f"      proxied = {to_hcl_literal(record.proxied)}")
        if record.tags:
            lines.append(f"      tags = {json.dumps(record.tags)}")
        if record.ttl != 1:
            lines.append(f"      ttl = {record.ttl}")
        if record.settings != {"flatten_cname": False}:
            lines.append("      settings = {")
            for key, value in sorted(record.settings.items()):
                lines.append(f"        {key} = {to_hcl_literal(value)}")
            lines.append("      }")
        lines.append("    }")
    lines.extend(["  }", "}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def render_dns_imports(records: List[CloudflareDNSRecord], zone_id: str) -> List[str]:
    lines: List[str] = []
    for record in records:
        if not record.record_id:
            continue
        lines.extend(
            [
                "import {",
                f'  to = cloudflare_dns_record.this[{json.dumps(record.key)}]',
                f'  id = {json.dumps(f"{zone_id}/{record.record_id}")}',
                "}",
                "",
            ]
        )
    return lines


def _format_hcl_name(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return name
    return json.dumps(name)


def sanitize_filename_component(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return text or "item"


def sanitize_comment_header(value: str) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return " ".join(lines)


def append_comment_lines(lines: List[str], comments: Iterable[str], indent: int) -> None:
    prefix = "  " * indent
    for comment in comments:
        if comment:
            lines.append(f"{prefix}# {comment}")


def append_file_header(lines: List[str], section: str, add_comments: bool) -> None:
    if not add_comments:
        return
    append_comment_lines(lines, SECTION_FILE_HEADERS.get(section, (section,)), 0)
    lines.append("")


def append_settings_map(lines: List[str], section: str, settings: List[Any], indent: int, add_comments: bool) -> None:
    setting_comments = SECTION_SETTING_COMMENTS.get(section, {})
    for index, setting in enumerate(settings):
        if add_comments:
            append_comment_lines(lines, setting_comments.get(setting.setting_id, ()), indent)
        lines.append(f'{"  " * indent}{_format_hcl_name(setting.setting_id)} = {to_hcl_literal(setting.value)}')
        if add_comments and index < len(settings) - 1:
            lines.append("")


def parse_hcl_scalar(text: str) -> Any:
    value = text.strip().rstrip(",")
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return ast.literal_eval(value)


def load_existing_section_values(path: Path, section: str) -> Dict[str, Any]:
    if not path.exists():
        return {}
    existing: Dict[str, Any] = {}
    allowed_ids = set(SECTION_SETTING_IDS.get(section, []))
    pattern = re.compile(r'^\s*(?P<key>"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?)\s*$')
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        raw_key = match.group("key")
        key = raw_key[1:-1] if raw_key.startswith('"') else raw_key
        if key not in allowed_ids:
            continue
        try:
            existing[key] = parse_hcl_scalar(match.group("value"))
        except Exception:
            continue
    return existing


def merge_settings_with_existing(section: str, settings: List[CloudflareZoneSetting], path: Path) -> List[CloudflareZoneSetting]:
    existing_values = load_existing_section_values(path, section)
    current_values = {setting.setting_id: setting for setting in settings}
    preserve_local = PRESERVE_LOCAL_SETTING_IDS.get(section, set())
    merged: List[CloudflareZoneSetting] = []
    for setting_id in SECTION_SETTING_IDS.get(section, []):
        if setting_id in preserve_local and setting_id in existing_values:
            merged.append(CloudflareZoneSetting(section=section, setting_id=setting_id, value=existing_values[setting_id]))
        elif setting_id in current_values:
            merged.append(current_values[setting_id])
        elif setting_id in existing_values:
            merged.append(CloudflareZoneSetting(section=section, setting_id=setting_id, value=existing_values[setting_id]))
    return merged if merged else settings


def render_zone_setting_resource(section: str, local_name: str) -> List[str]:
    return [
        f'resource "cloudflare_zone_setting" "{section}" {{',
        f"  for_each = local.{local_name}",
        "",
        "  zone_id    = local.cloudflare_zone_id",
        "  setting_id = each.key",
        "  value      = each.value",
        "}",
        "",
    ]


def render_ruleset_items(items: List[Dict[str, Any]], indent: int = 2, add_comments: bool = False) -> List[str]:
    lines: List[str] = []
    for index, item in enumerate(items):
        comment = item.get("description") or item.get("ref")
        if add_comments and comment:
            lines.append(f'{"  " * indent}# {comment}')
        render_attribute_block("", item, indent, lines)
        if index < len(items) - 1:
            lines.append("")
    return lines


def sanitize_ruleset_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    ordered_keys = (
        "action",
        "description",
        "enabled",
        "expression",
        "ref",
        "action_parameters",
        "logging",
        "ratelimit",
    )
    raw_id = rule.get("id")
    raw_ref = rule.get("ref")
    for key in ordered_keys:
        if key not in rule:
            continue
        value = rule.get(key)
        if value is None:
            continue
        if key == "ref" and raw_ref == raw_id:
            continue
        sanitized[key] = sanitize_ruleset_value(value)
    return sanitized


def sanitize_ruleset_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if key in {"id", "version", "last_updated"}:
                continue
            if item is None:
                continue
            cleaned[key] = sanitize_ruleset_value(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_ruleset_value(item) for item in value if item is not None]
    return value


def render_ruleset_resource(
    resource_name: str,
    phase: str,
    ruleset: CloudflareRulesetDefinition | None,
    add_comments: bool,
) -> List[str]:
    lines: List[str] = []
    if add_comments:
        append_comment_lines(lines, [RULESET_PHASE_COMMENTS.get(phase, phase)], 0)
    lines.extend(
        [
            f'resource "cloudflare_ruleset" "{resource_name}" {{',
            '  kind    = "zone"',
            '  name    = "default"',
            f'  phase   = "{phase}"',
            "  zone_id = local.cloudflare_zone_id",
            "  rules = [",
        ]
    )
    rules = [sanitize_ruleset_rule(rule) for rule in (ruleset.ruleset.get("rules") or [])] if ruleset else []
    lines.extend(render_ruleset_items(rules, indent=2, add_comments=add_comments))
    lines.extend(["  ]", "}", ""])
    return lines


def render_settings_section_file(section: str, settings: List[Any], add_comments: bool) -> str:
    local_name = f"{section}_settings"
    lines: List[str] = []
    append_file_header(lines, section, add_comments)
    lines.extend(["locals {", f"  {local_name} = {{"])
    append_settings_map(lines, section, settings, 2, add_comments)
    lines.extend(["  }", "}", ""])
    lines.extend(render_zone_setting_resource(section, local_name))
    return "\n".join(lines)


def render_caching_file(
    settings: List[Any],
    ruleset: CloudflareRulesetDefinition | None,
    add_comments: bool,
) -> str:
    lines: List[str] = []
    append_file_header(lines, "caching", add_comments)
    lines.extend(["locals {", "  caching_settings = {"])
    append_settings_map(lines, "caching", settings, 2, add_comments)
    lines.extend(["  }", "}", ""])
    lines.extend(render_zone_setting_resource("caching", "caching_settings"))
    lines.extend(render_ruleset_resource("caching", "http_request_cache_settings", ruleset, add_comments))
    return "\n".join(lines)


def render_security_file(
    settings: List[Any],
    list_definitions: List[CloudflareListDefinition],
    security_ruleset: CloudflareRulesetDefinition | None,
    rate_limit_ruleset: CloudflareRulesetDefinition | None,
    add_comments: bool,
) -> str:
    lines: List[str] = []
    append_file_header(lines, "security", add_comments)
    lines.extend(["locals {", "  cloudflare_lists = ["])
    for definition in list_definitions:
        if add_comments and definition.description:
            append_comment_lines(lines, [definition.description], 2)
        lines.append(f"    local.{definition.local_name},")
    lines.extend(["  ]", "}", ""])
    lines.extend(
        [
            'resource "cloudflare_list" "whitelists" {',
            "  for_each = {",
            "    for cloudflare_list in local.cloudflare_lists :",
            "    cloudflare_list.name => cloudflare_list",
            '    if cloudflare_list.internal_type == "whitelist"',
            "  }",
            "  account_id  = var.cloudflare_account_id",
            '  kind        = try(each.value.type, "ip")',
            "  name        = each.value.name",
            "  description = each.value.description",
            "  items = (",
            '    each.value.type == "ip" ? concat(',
            "      [for x in try(each.value.items, []) :",
            "        merge(",
            "          { ip = tostring(x.ip) },",
            '          can(x.comment) ? { comment = tostring(x.comment) } : {}',
            "        )",
            "        if can(x.ip)",
            "      ],",
            "      [for x in try(each.value.items, []) :",
            "        { ip = tostring(x) }",
            "        if !can(x.ip)",
            "      ]",
            '    ) : each.value.type == "asn" ? concat(',
            "      [for a in try(each.value.items, []) :",
            "        merge(",
            "          { asn = tonumber(a.asn) },",
            '          can(a.comment) ? { comment = tostring(a.comment) } : {}',
            "        )",
            "        if can(a.asn) && can(tonumber(a.asn))",
            "      ],",
            "      [for a in try(each.value.items, []) :",
            "        { asn = tonumber(a) }",
            "        if !can(a.asn) && can(tonumber(a))",
            "      ]",
            '    ) : each.value.type == "hostname" ? concat(',
            "      [for h in try(each.value.items, []) :",
            "        merge(",
            "          { hostname = { url_hostname = tostring(h.hostname) } },",
            '          can(h.comment) ? { comment = tostring(h.comment) } : {}',
            "        )",
            "        if can(h.hostname)",
            "      ],",
            "      [for h in try(each.value.items, []) :",
            "        { hostname = { url_hostname = tostring(h) } }",
            "        if !can(h.hostname)",
            "      ]",
            "    ) : []",
            "  )",
            "}",
            "",
            'resource "cloudflare_list" "blacklists" {',
            "  for_each = {",
            "    for cloudflare_list in local.cloudflare_lists :",
            "    cloudflare_list.name => cloudflare_list",
            '    if cloudflare_list.internal_type == "blacklist"',
            "  }",
            "  account_id  = var.cloudflare_account_id",
            '  kind        = try(each.value.type, "ip")',
            "  name        = each.value.name",
            "  description = each.value.description",
            "  items       = each.value.items",
            "}",
            "",
            "locals {",
            "  security_settings = {",
        ]
    )
    append_settings_map(lines, "security", settings, 2, add_comments)
    lines.extend(["  }", "}", ""])
    lines.extend(render_zone_setting_resource("security", "security_settings"))
    lines.extend(render_ruleset_resource("security", "http_request_firewall_custom", security_ruleset, add_comments))
    lines.extend(render_ruleset_resource("rate_limit", "http_ratelimit", rate_limit_ruleset, add_comments))
    return "\n".join(lines)


def write_settings_files(
    grouped_settings: Dict[str, List[Any]],
    list_definitions: List[CloudflareListDefinition],
    ruleset_definitions: List[CloudflareRulesetDefinition],
    output_dir: Path,
    add_comments: bool,
) -> None:
    rulesets_by_phase = {definition.phase: definition for definition in ruleset_definitions}
    for section, settings in grouped_settings.items():
        path = output_dir / SUPPORTED_SETTINGS_FILES[section]
        merged_settings = merge_settings_with_existing(section, settings, path)
        if section == "caching":
            content = render_caching_file(merged_settings, rulesets_by_phase.get("http_request_cache_settings"), add_comments)
        elif section == "security":
            content = render_security_file(
                merged_settings,
                list_definitions,
                rulesets_by_phase.get("http_request_firewall_custom"),
                rulesets_by_phase.get("http_ratelimit"),
                add_comments,
            )
        else:
            content = render_settings_section_file(section, merged_settings, add_comments)
        path.write_text(content, encoding="utf-8")


def normalize_account_lists(
    raw_lists: List[Dict[str, Any]],
    client: CloudflareAPIClient,
    account_id: str,
) -> List[CloudflareListDefinition]:
    definitions: List[CloudflareListDefinition] = []
    for raw_list in sorted(raw_lists, key=lambda item: str(item.get("name") or "").lower()):
        kind = str(raw_list.get("kind") or "")
        if kind not in {"ip", "asn", "hostname"}:
            continue
        list_id = str(raw_list.get("id") or "")
        resource_name = str(raw_list.get("name") or "")
        items = normalize_list_items(kind, client.list_account_list_items(account_id, list_id))
        definitions.append(
            CloudflareListDefinition(
                local_name=f"generated_cloudflare_list_{sanitize_identifier(resource_name)}",
                resource_name=resource_name,
                internal_type=infer_list_internal_type(resource_name),
                kind=kind,
                description=str(raw_list.get("description") or ""),
                items=items,
                list_id=list_id,
            )
        )
    return definitions


def render_generated_list_file(definition: CloudflareListDefinition, add_comments: bool = False) -> str:
    lines: List[str] = []
    if add_comments:
        comment_header = sanitize_comment_header(definition.description) or sanitize_comment_header(definition.resource_name)
        append_comment_lines(lines, [comment_header], 0)
        lines.append("")
    lines.extend(["locals {", f"  {definition.local_name} = {{"])
    lines.append(f"    internal_type = {json.dumps(definition.internal_type)}")
    lines.append(f"    type = {json.dumps(definition.kind)}")
    lines.append(f"    name = {json.dumps(definition.resource_name)}")
    lines.append(f"    description = {json.dumps(definition.description)}")
    lines.append("    items = [")
    for item in definition.items:
        lines.append("      {")
        for key, value in item.items():
            lines.append(f"        {key} = {to_hcl_literal(value)}")
        lines.append("      },")
    lines.extend(["    ]", "  }", "}", ""])
    return "\n".join(lines)


def write_generated_list_files(definitions: List[CloudflareListDefinition], output_dir: Path, add_comments: bool = False) -> None:
    stale_paths = list(output_dir.glob("config-list-*.tf")) + list(output_dir.glob("config-generated-list-*.tf"))
    active_paths = {
        output_dir / f"config-list-{sanitize_filename_component(definition.resource_name)}.tf"
        for definition in definitions
    }
    for stale_path in stale_paths:
        if stale_path not in active_paths:
            stale_path.unlink()
    for definition in definitions:
        path = output_dir / f"config-list-{sanitize_filename_component(definition.resource_name)}.tf"
        path.write_text(render_generated_list_file(definition, add_comments=add_comments), encoding="utf-8")


def render_list_imports(definitions: List[CloudflareListDefinition], account_id: str) -> List[str]:
    lines: List[str] = []
    for definition in definitions:
        if not definition.list_id:
            continue
        resource_name = "whitelists" if definition.internal_type == "whitelist" else "blacklists"
        lines.extend(
            [
                "import {",
                f'  to = cloudflare_list.{resource_name}[{json.dumps(definition.resource_name)}]',
                f'  id = {json.dumps(f"{account_id}/{definition.list_id}")}',
                "}",
                "",
            ]
        )
    return lines


def normalize_zone_rulesets(
    raw_rulesets: List[Dict[str, Any]],
    client: CloudflareAPIClient,
    zone_id: str,
) -> List[CloudflareRulesetDefinition]:
    definitions: List[CloudflareRulesetDefinition] = []
    for raw_ruleset in sorted(raw_rulesets, key=lambda item: (str(item.get("phase") or ""), str(item.get("id") or ""))):
        phase = str(raw_ruleset.get("phase") or "")
        if phase not in RULESET_PHASE_FILENAMES:
            continue
        ruleset_id = str(raw_ruleset.get("id") or "")
        if not ruleset_id:
            continue
        detailed = client.get_zone_ruleset(zone_id, ruleset_id)
        definitions.append(
            CloudflareRulesetDefinition(
                phase=str(detailed.get("phase") or phase),
                ruleset_id=ruleset_id,
                name=str(detailed.get("name") or raw_ruleset.get("name") or ""),
                kind=str(detailed.get("kind") or raw_ruleset.get("kind") or ""),
                description=str(detailed.get("description") or raw_ruleset.get("description") or ""),
                ruleset=detailed,
            )
        )
    return definitions


def render_ruleset_imports(definitions: List[CloudflareRulesetDefinition], zone_id: str) -> List[str]:
    lines: List[str] = []
    for definition in definitions:
        resource_name = RULESET_PHASE_RESOURCE_NAMES.get(definition.phase)
        if not resource_name:
            continue
        lines.extend(
            [
                "import {",
                f"  to = cloudflare_ruleset.{resource_name}",
                f'  id = {json.dumps(f"{zone_id}/{definition.ruleset_id}")}',
                "}",
                "",
            ]
        )
    return lines


def write_imports_file(
    dns_records: List[CloudflareDNSRecord],
    zone_id: str,
    list_definitions: List[CloudflareListDefinition],
    account_id: str,
    ruleset_definitions: List[CloudflareRulesetDefinition],
    grouped_settings: Dict[str, List[CloudflareZoneSetting]],
    transforms: Dict[str, Dict[str, bool]],
    logpush_jobs: List[Dict[str, Any]],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.extend(render_dns_imports(dns_records, zone_id))
    lines.extend(render_list_imports(list_definitions, account_id))
    lines.extend(render_ruleset_imports(ruleset_definitions, zone_id))
    lines.extend(render_zone_setting_imports(grouped_settings, zone_id))
    lines.extend(render_managed_transforms_imports(transforms, zone_id))
    lines.extend(render_logpush_imports(logpush_jobs, zone_id))
    path.write_text("\n".join(lines).rstrip() + ("\n" if lines else ""), encoding="utf-8")


def normalize_managed_transforms(payload: Dict[str, Any]) -> Dict[str, Dict[str, bool]]:
    request_headers = {
        str(item.get("id")): bool(item.get("enabled", True))
        for item in (payload.get("managed_request_headers") or [])
        if item.get("id") is not None
    }
    response_headers = {
        str(item.get("id")): bool(item.get("enabled", True))
        for item in (payload.get("managed_response_headers") or [])
        if item.get("id") is not None
    }
    return {"request": request_headers, "response": response_headers}


def render_zone_setting_imports(
    grouped_settings: Dict[str, List[CloudflareZoneSetting]],
    zone_id: str,
) -> List[str]:
    lines: List[str] = []
    for section in sorted(grouped_settings):
        for setting in grouped_settings[section]:
            lines.extend(
                [
                    "import {",
                    f'  to = cloudflare_zone_setting.{section}[{json.dumps(setting.setting_id)}]',
                    f'  id = {json.dumps(f"{zone_id}/{setting.setting_id}")}',
                    "}",
                    "",
                ]
            )
    return lines


def render_managed_transforms_imports(
    transforms: Dict[str, Dict[str, bool]],
    zone_id: str,
) -> List[str]:
    if not transforms.get("request") and not transforms.get("response"):
        return []
    return [
        "import {",
        "  to = cloudflare_managed_transforms.headers",
        f'  id = {json.dumps(zone_id)}',
        "}",
        "",
    ]


def render_managed_transform_map(values: Dict[str, bool], section: str, add_comments: bool) -> List[str]:
    lines: List[str] = []
    descriptions = MANAGED_TRANSFORM_DESCRIPTIONS[section]
    for index, (header_id, enabled) in enumerate(sorted(values.items())):
        if add_comments:
            description = descriptions.get(header_id)
            if description:
                append_comment_lines(lines, [description], 2)
        lines.append(f"    {json.dumps(header_id)} = {to_hcl_literal(enabled)}")
        if add_comments and index < len(values) - 1:
            lines.append("")
    return lines


def render_managed_transforms_file(transforms: Dict[str, Dict[str, bool]], add_comments: bool) -> str:
    lines: List[str] = []
    if add_comments:
        append_comment_lines(lines, ["Rules > Settings > Managed Transforms"], 0)
        lines.append("")
    lines.extend(["locals {", "  generated_managed_request_headers = {"])
    lines.extend(render_managed_transform_map(transforms.get("request", {}), "request", add_comments))
    lines.extend(["  }", "", "  generated_managed_response_headers = {"])
    lines.extend(render_managed_transform_map(transforms.get("response", {}), "response", add_comments))
    lines.extend(["  }", "}", "", 'resource "cloudflare_managed_transforms" "headers" {', "  zone_id = local.cloudflare_zone_id", "", "  managed_request_headers = [", "    for key, value in local.generated_managed_request_headers : {", "      id      = key", "      enabled = value", "    } if value", "  ]", "", "  managed_response_headers = [", "    for key, value in local.generated_managed_response_headers : {", "      id      = key", "      enabled = value", "    } if value", "  ]", "}", ""])
    return "\n".join(lines)


def write_managed_transforms_file(transforms: Dict[str, Dict[str, bool]], path: Path, add_comments: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_managed_transforms_file(transforms, add_comments), encoding="utf-8")


def normalize_logpush_jobs(
    raw_jobs: List[Dict[str, Any]],
    client: CloudflareAPIClient,
    zone_id: str,
) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for raw_job in sorted(raw_jobs, key=lambda item: (str(item.get("dataset") or ""), int(item.get("id") or 0))):
        job_id = raw_job.get("id")
        if job_id is None:
            continue
        jobs.append(client.get_zone_logpush_job(zone_id, job_id))
    return jobs


def build_logpush_job_map(jobs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    mapped: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, int] = {}
    for job in jobs:
        base = sanitize_identifier(str(job.get("name") or job.get("dataset") or "job"))
        counts[base] = counts.get(base, 0) + 1
        key = base if counts[base] == 1 else f"{base}_{counts[base]}"
        mapped[key] = job
    return mapped


def render_logpush_imports(jobs: List[Dict[str, Any]], zone_id: str) -> List[str]:
    lines: List[str] = []
    mapped_jobs = build_logpush_job_map(jobs)
    for key, job in mapped_jobs.items():
        job_id = job.get("id")
        if job_id is None:
            continue
        lines.extend(
            [
                "import {",
                f'  to = cloudflare_logpush_job.this[{json.dumps(key)}]',
                f'  id = {json.dumps(f"{zone_id}/{job_id}")}',
                "}",
                "",
            ]
        )
    return lines


def render_logpush_file(
    jobs: List[Dict[str, Any]],
    add_comments: bool,
) -> str:
    lines: List[str] = []
    if add_comments:
        append_comment_lines(lines, ["Analytics > Logpush Jobs"], 0)
        lines.append("")
    lines.extend(["locals {", "  generated_logpush_jobs = {"])
    mapped_jobs = build_logpush_job_map(jobs)
    for index, (key, job) in enumerate(mapped_jobs.items()):
        if add_comments:
            label = str(job.get("name") or job.get("dataset") or key)
            append_comment_lines(lines, [label], 2)
        lines.append(f"    {key} = {{")
        for attr in ("destination_conf", "dataset", "enabled", "kind", "max_upload_bytes", "max_upload_records", "name"):
            if job.get(attr) is not None:
                render_attribute_block(attr, job.get(attr), 3, lines)
        if job.get("output_options") is not None:
            render_attribute_block("output_options", job.get("output_options"), 3, lines)
        lines.append("    }")
        if add_comments and index < len(mapped_jobs) - 1:
            lines.append("")
    lines.extend(["  }", "}", ""])
    lines.extend(['resource "cloudflare_logpush_job" "this" {', "  for_each = local.generated_logpush_jobs", "", "  zone_id            = local.cloudflare_zone_id", "  destination_conf   = each.value.destination_conf", "  dataset            = each.value.dataset", "  enabled            = each.value.enabled", "  kind               = try(each.value.kind, null)", "  max_upload_bytes   = try(each.value.max_upload_bytes, null)", "  max_upload_records = try(each.value.max_upload_records, null)", "  name               = try(each.value.name, null)", "  output_options     = try(each.value.output_options, null)", "}", ""])
    return "\n".join(lines)


def write_logpush_file(
    jobs: List[Dict[str, Any]],
    path: Path,
    add_comments: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_logpush_file(jobs, add_comments), encoding="utf-8")


def cleanup_stale_generated_artifacts(output_dir: Path) -> None:
    for path in list(output_dir.glob("generated-ruleset-*.tf")) + list(output_dir.glob("config-ruleset-*.tf")):
        path.unlink()
    for name in ("imports-lists.tf", "imports-rulesets.tf"):
        stale_path = output_dir / name
        if stale_path.exists():
            stale_path.unlink()
    for name in ("config-generated-managed-transforms.tf", "config-generated-logpush-jobs.tf", "config-managed-transforms.tf", "config-logpush-jobs.tf"):
        stale_path = output_dir / name
        if stale_path.exists():
            stale_path.unlink()
