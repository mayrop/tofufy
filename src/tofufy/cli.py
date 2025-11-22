#!/usr/bin/env python3
"""Generate Terraform locals for Route53 record sets for each hosted zone."""

import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Pattern, Tuple, Union

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DEFAULT_CONFIG_PATH = "config-route53.json"

SKIP_RECORD_TYPES = {"NS", "SOA"}
SKIPPABLE_IMPORT_TYPES = {"A", "CNAME"}

DEFAULT_ARGUMENTS: Dict[str, Any] = {
    "zone_ids": [],
    "output_dir": ".",
    "locals_file": "locals.tf",
    "imports_file": "imports.tf",
    "zones_file": "config-zones.tf",
    "single_zone": False,
    "single_zone_records_file": "config-records.tf",
    "profile": None,
    "skip_hostnames": [],
    "only_hostnames": [],
    "export_target": "both",
    "skip_zone_tags": False,
}

EXPORT_TARGET_CHOICES = ("records", "zones", "both")

PERCENT_ESCAPE_PATTERN = re.compile(r"%(?!%)")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

BEGIN_MARKER = "# BEGIN GENERATED ROUTE53 RECORDS"
END_MARKER = "# END GENERATED ROUTE53 RECORDS"
SINGLE_ZONE_BEGIN_MARKER = "# BEGIN GENERATED PRIMARY ZONE"
SINGLE_ZONE_END_MARKER = "# END GENERATED PRIMARY ZONE"


def build_session(profile: Optional[str]):
    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    return boto3.Session(**session_kwargs)


def normalize_config_key(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("--"):
        text = text[2:]
    return text.replace("-", "_")


def _coerce_string_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        tokens = [token.strip() for token in raw.split(",")]
        return [token for token in tokens if token]
    try:
        iterator = iter(raw)
    except TypeError:
        text = str(raw).strip()
        return [text] if text else []
    values: List[str] = []
    for entry in iterator:
        if entry is None:
            continue
        text = str(entry).strip()
        if text:
            values.append(text)
    return values


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


def apply_record_type_overrides(config_data: Dict[str, Any]) -> None:
    global SKIP_RECORD_TYPES, SKIPPABLE_IMPORT_TYPES

    skip_record_types = _coerce_string_list(config_data.get("skip_record_types"))
    if skip_record_types:
        SKIP_RECORD_TYPES = {value.upper() for value in skip_record_types}

    skippable_import_types = _coerce_string_list(config_data.get("skippable_import_types"))
    if skippable_import_types:
        SKIPPABLE_IMPORT_TYPES = {value.upper() for value in skippable_import_types}


def normalize_config_data(config_data: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = dict(DEFAULT_ARGUMENTS)
    if not config_data:
        return normalized

    for key, value in config_data.items():
        normalized_key = normalize_config_key(key)
        if normalized_key == "arguments" and isinstance(value, dict):
            for nested_key, nested_value in value.items():
                normalized[normalize_config_key(nested_key)] = nested_value
        else:
            normalized[normalized_key] = value

    for reserved in ("skip_record_types", "skippable_import_types"):
        normalized.pop(reserved, None)

    return normalized


def apply_argument_defaults_from_config(
    parser: argparse.ArgumentParser, normalized_config: Dict[str, Any]
) -> None:
    if not normalized_config:
        return

    parser_destinations = {action.dest for action in parser._actions}

    defaults: Dict[str, Any] = {}
    for key, value in normalized_config.items():
        if key in parser_destinations and key != "help":
            defaults[key] = value

    if defaults:
        parser.set_defaults(**defaults)


def iter_record_sets(client, zone_id: str) -> Iterable[dict]:
    paginator = client.get_paginator("list_resource_record_sets")
    for page in paginator.paginate(HostedZoneId=zone_id):
        for record in page.get("ResourceRecordSets", []):
            yield record


def get_zone_details(client, zone_id: str, include_tags: bool = True) -> Dict[str, Any]:
    response = client.get_hosted_zone(Id=zone_id)
    hosted_zone = response.get("HostedZone", {})
    config = hosted_zone.get("Config") or {}

    zone_name = (hosted_zone.get("Name") or zone_id).rstrip(".")
    private_zone = bool(config.get("PrivateZone"))
    comment = config.get("Comment") or ""

    vpcs: List[Dict[str, Any]] = []
    for vpc in response.get("VPCs") or []:
        vpc_id = vpc.get("VPCId")
        if not vpc_id:
            continue
        vpcs.append(
            {
                "vpc_id": vpc_id,
                "vpc_region": vpc.get("VPCRegion"),
            }
        )

    tags: Dict[str, str] = {}
    if include_tags:
        try:
            tag_response = client.list_tags_for_resource(
                ResourceType="hostedzone", ResourceId=zone_id
            )
        except (ClientError, BotoCoreError):
            tags = {}
        else:
            resource_tags = tag_response.get("ResourceTagSet", {}).get("Tags", [])
            for entry in resource_tags:
                key = entry.get("Key")
                if key:
                    tags[key] = entry.get("Value", "")

    return {
        "id": zone_id,
        "name": zone_name,
        "private_zone": private_zone,
        "comment": comment,
        "tags": tags,
        "vpcs": vpcs,
    }


def compute_relative_name(record_name: str, zone_name: str) -> str:
    name = (record_name or "").rstrip(".")
    zone = (zone_name or "").rstrip(".")

    if not name or name == zone:
        return ""

    if zone and name.endswith(f".{zone}"):
        return name[: -(len(zone) + 1)]

    return name


def sanitize_subdomain(value: str) -> str:
    if not value:
        return "root"

    sanitized = value.replace("*", "star").replace("\\052", "star")
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", sanitized.lower()).strip("_")
    return sanitized or "root"


def escape_percent_signs(value: str) -> str:
    if not value or "%" not in value:
        return value
    return PERCENT_ESCAPE_PATTERN.sub("%%", value)


def normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {to_snake_case(k): normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    return value


def to_snake_case(value: str) -> str:
    snake = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake)
    return snake.lower()


def build_record_key(record_name: str, zone_name: str, record_type: str) -> Tuple[str, str, str]:
    relative_name = compute_relative_name(record_name, zone_name)
    subdomain = sanitize_subdomain(relative_name)
    return f"{record_type.lower()}_{subdomain}", relative_name, subdomain


def normalize_record(record: dict, zone_name: str, zone_id: str) -> Optional[Dict[str, Any]]:
    record_type = (record.get("Type") or "").upper()
    if record_type in SKIP_RECORD_TYPES:
        return None

    raw_name = (record.get("Name") or "").rstrip(".")
    full_name = raw_name.replace("\\052", "*")

    key_base, relative_name, subdomain = build_record_key(full_name, zone_name, record_type)

    normalized: Dict[str, Any] = {
        "key_base": key_base,
        "relative_name": relative_name,
        "subdomain": subdomain,
        "full_name": full_name,
        "type": record_type,
    }

    resource_records = record.get("ResourceRecords")
    if resource_records:
        values: List[str] = []
        for entry in resource_records:
            value = entry.get("Value", "")
            if record_type in {"TXT", "SPF"} and value.startswith('"') and value.endswith('"'):
                value = value[1:-1].replace('\\"', '"')
            value = escape_percent_signs(value)
            values.append(value)
        normalized["records"] = values

    alias_target = record.get("AliasTarget")
    if alias_target:
        alias_map: Dict[str, Any] = {
            "name": (alias_target.get("DNSName") or "").rstrip(".").replace("\\052", "*"),
            "zone_id": alias_target.get("HostedZoneId"),
        }
        if alias_target.get("EvaluateTargetHealth") is not None:
            alias_map["evaluate_target_health"] = alias_target.get("EvaluateTargetHealth")
        normalized["alias"] = alias_map

    geo_location = record.get("GeoLocation")
    if geo_location:
        normalized["geo_location"] = {
            "continent_code": geo_location.get("ContinentCode"),
            "country_code": geo_location.get("CountryCode"),
            "subdivision_code": geo_location.get("SubdivisionCode"),
        }

    for key_name in (
        "TTL",
        "SetIdentifier",
        "HealthCheckId",
        "Failover",
        "TrafficPolicyInstanceId",
        "MultiValueAnswer",
        "Region",
        "Weight",
    ):
        value = record.get(key_name)
        if value is not None:
            normalized[to_snake_case(key_name)] = normalize_value(value)

    record_name_for_id = normalized["full_name"] or zone_name
    import_id_parts = [zone_id, record_name_for_id, record_type]
    set_identifier = record.get("SetIdentifier")
    if set_identifier:
        import_id_parts.append(set_identifier)
    normalized["import_id"] = "_".join(import_id_parts)

    return normalized


def build_record_attributes(record: Dict[str, Any]) -> "OrderedDict[str, Any]":
    attributes: "OrderedDict[str, Any]" = OrderedDict()

    attributes["full_name"] = record["full_name"]
    attributes["type"] = record.get("type", "")

    ttl = record.get("ttl")
    if ttl is not None:
        attributes["ttl"] = ttl

    record_values = record.get("records", [])
    if record_values:
        attributes["records"] = record_values

    alias = record.get("alias")
    if alias:
        alias_attributes: "OrderedDict[str, Any]" = OrderedDict()
        if alias.get("name"):
            alias_attributes["name"] = alias.get("name")
        if alias.get("zone_id"):
            alias_attributes["zone_id"] = alias.get("zone_id")
        if alias.get("evaluate_target_health") is not None:
            alias_attributes["evaluate_target_health"] = alias.get("evaluate_target_health")
        attributes["alias"] = alias_attributes

    for key_name in ("set_identifier", "health_check_id", "failover", "traffic_policy_instance_id"):
        if record.get(key_name) is not None:
            attributes[key_name] = record.get(key_name)

    if record.get("multi_value_answer") is not None:
        attributes["multivalue_answer"] = record.get("multi_value_answer")

    if record.get("region"):
        attributes["latency_routing_policy"] = {"region": record.get("region")}

    if record.get("weight") is not None:
        attributes["weighted_routing_policy"] = {"weight": record.get("weight")}

    geo_location = record.get("geo_location") or {}
    geo_attributes: "OrderedDict[str, Any]" = OrderedDict()
    mapping = {
        "continent_code": "continent",
        "country_code": "country",
        "subdivision_code": "subdivision",
    }
    for original_key, target_key in mapping.items():
        value = geo_location.get(original_key)
        if value:
            geo_attributes[target_key] = value
    if geo_attributes:
        attributes["geolocation_routing_policy"] = geo_attributes

    return attributes


def to_hcl_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)


def render_attribute_block(attribute_name: str, attribute_value: Any, indent: int, lines: List[str]) -> None:
    indent_str = "  " * indent

    def format_name(name: str) -> str:
        if not name:
            return ""
        if IDENTIFIER_PATTERN.match(name):
            return name
        return json.dumps(name)

    if isinstance(attribute_value, dict):
        if not attribute_value:
            if attribute_name:
                lines.append(f"{indent_str}{format_name(attribute_name)} = {{}}")
            else:
                lines.append(f"{indent_str}{{}},")
            return
        if attribute_name:
            lines.append(f"{indent_str}{format_name(attribute_name)} = {{")
        else:
            lines.append(f"{indent_str}{{")
        for key, value in attribute_value.items():
            render_attribute_block(key, value, indent + 1, lines)
        closing = "}" if attribute_name else "},"
        lines.append(f"{indent_str}{closing}")
    elif isinstance(attribute_value, list):
        if not attribute_value:
            if attribute_name:
                lines.append(f"{indent_str}{format_name(attribute_name)} = []")
            else:
                lines.append(f"{indent_str}[]")
            return
        if attribute_name:
            lines.append(f"{indent_str}{format_name(attribute_name)} = [")
        else:
            lines.append(f"{indent_str}[")
        for item in attribute_value:
            render_attribute_block("", item, indent + 1, lines)
        closing = "]" if attribute_name else "],"
        lines.append(f"{indent_str}{closing}")
    else:
        literal = to_hcl_literal(attribute_value)
        if attribute_name:
            lines.append(f"{indent_str}{format_name(attribute_name)} = {literal}")
        else:
            lines.append(f"{indent_str}{literal},")


def render_zone_file(
    local_var: str,
    zone_key: str,
    records: "OrderedDict[str, OrderedDict[str, Any]]",
) -> str:
    lines: List[str] = []
    lines.append("locals {")
    lines.append(f"  {local_var} = {{")
    lines.append(f"    {json.dumps(zone_key)} = {{")
    if records:
        for record_key, attributes in records.items():
            lines.append(f"      {record_key} = {{")
            attribute_lines: List[str] = []
            for attr_name, attr_value in attributes.items():
                render_attribute_block(attr_name, attr_value, 4, attribute_lines)
            lines.extend(attribute_lines)
            lines.append("      }")
    lines.append("    }")
    lines.append("  }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def render_single_zone_records(
    local_var: str,
    records: "OrderedDict[str, OrderedDict[str, Any]]",
) -> str:
    lines: List[str] = []
    lines.append("locals {")
    lines.append(f"  {local_var} = {{")
    if records:
        for record_key, attributes in records.items():
            lines.append(f"    {record_key} = {{")
            attribute_lines: List[str] = []
            for attr_name, attr_value in attributes.items():
                render_attribute_block(attr_name, attr_value, 3, attribute_lines)
            lines.extend(attribute_lines)
            lines.append("    }")
    lines.append("  }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def to_ordered(value: Any) -> Any:
    if isinstance(value, dict):
        ordered: "OrderedDict[str, Any]" = OrderedDict()
        for key, entry in value.items():
            ordered[key] = to_ordered(entry)
        return ordered
    if isinstance(value, list):
        return [to_ordered(item) for item in value]
    return value


def ordered_to_builtin(value: Any) -> Any:
    if isinstance(value, OrderedDict):
        return {key: ordered_to_builtin(val) for key, val in value.items()}
    if isinstance(value, dict):
        return {key: ordered_to_builtin(val) for key, val in value.items()}
    if isinstance(value, list):
        return [ordered_to_builtin(item) for item in value]
    return value


def build_vpc_map(vpcs: List[Dict[str, Any]]) -> "OrderedDict[str, OrderedDict[str, Any]]":
    vpc_map: "OrderedDict[str, OrderedDict[str, Any]]" = OrderedDict()
    sorted_vpcs = sorted(
        vpcs,
        key=lambda item: (item.get("vpc_region") or "", item.get("vpc_id") or ""),
    )
    for index, vpc in enumerate(sorted_vpcs, start=1):
        identifier = sanitize_identifier(
            f"{vpc.get('vpc_region') or 'unknown'}_{vpc.get('vpc_id') or index}"
        )
        if not identifier:
            identifier = f"vpc_{index:02d}"

        block: "OrderedDict[str, Any]" = OrderedDict()
        if vpc.get("vpc_id"):
            block["vpc_id"] = vpc.get("vpc_id")
        if vpc.get("vpc_region"):
            block["vpc_region"] = vpc.get("vpc_region")

        vpc_map[identifier] = block

    return vpc_map


def build_zone_configuration(
    zone_details: Dict[str, Any],
    include_tags: bool = True,
) -> "OrderedDict[str, Any]":
    attributes: "OrderedDict[str, Any]" = OrderedDict()
    attributes["name"] = zone_details.get("name")
    attributes["comment"] = zone_details.get("comment") or ""
    attributes["private_zone"] = bool(zone_details.get("private_zone"))

    if zone_details.get("private_zone"):
        vpcs = zone_details.get("vpcs") or []
        if vpcs:
            attributes["vpcs"] = build_vpc_map(vpcs)

    tags: Dict[str, str] = {}
    if include_tags:
        tags = zone_details.get("tags") or {}
    attributes["tags"] = OrderedDict(sorted((tags or {}).items()))

    return attributes


def write_zones_file(
    zones: "OrderedDict[str, OrderedDict[str, Any]]",
    zones_path: Path,
    zone_records_var: str = "zone_records",
) -> None:
    zones_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines.append("locals {")
    lines.append("  zones = {")
    if zones:
        for zone_key, attributes in zones.items():
            lines.append(f"    {json.dumps(zone_key)} = {{")
            attribute_lines: List[str] = []
            for attr_name, attr_value in attributes.items():
                render_attribute_block(attr_name, attr_value, 3, attribute_lines)
            lines.extend(attribute_lines)
            lines.append("    }")
    lines.append("  }")
    lines.append("")
    lines.append("}")
    lines.append("")

    zones_path.write_text("\n".join(lines), encoding="utf-8")


def sanitize_identifier(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


def collect_zone_records(
    client,
    zone_id: str,
    zone_name: str,
    private_zone: bool,
    skip_patterns: Optional[List[Pattern[str]]] = None,
    include_patterns: Optional[List[Pattern[str]]] = None,
) -> Tuple[str, str, str, "OrderedDict[str, OrderedDict[str, Any]]", List[Tuple[str, str, str]]]:
    records: List[Dict[str, Any]] = []
    for item in iter_record_sets(client, zone_id):
        normalized = normalize_record(item, zone_name, zone_id)
        if normalized is None:
            continue
        hostname = normalized.get("full_name") or ""
        if skip_patterns and normalized.get("type") in SKIPPABLE_IMPORT_TYPES:
            for pattern in skip_patterns:
                if pattern.search(hostname):
                    normalized = None
                    break
            if normalized is None:
                continue
        if include_patterns and not any(pattern.search(hostname) for pattern in include_patterns):
            continue
        records.append(normalized)

    zone_key = zone_name if not private_zone else f"{zone_name}_private"
    local_var = sanitize_identifier(f"zone_records_{zone_key}")

    filename_domain = zone_name.replace(".", "-")
    if private_zone:
        filename_domain = f"{filename_domain}-private"

    record_blocks: "OrderedDict[str, OrderedDict[str, Any]]" = OrderedDict()
    counts: Dict[str, int] = {}
    import_entries: List[Tuple[str, str, str]] = []
    for record in sorted(records, key=lambda item: (item["key_base"], item.get("set_identifier") or "", item["full_name"])):
        base = record["key_base"]
        counts[base] = counts.get(base, 0) + 1
        suffix = "" if counts[base] == 1 else f"_{counts[base]:02d}"
        record_key = f"{base}{suffix}"
        record["key"] = record_key
        record_blocks[record_key] = build_record_attributes(record)
        import_entries.append((zone_key, record_key, record["import_id"]))

    return zone_key, local_var, filename_domain, record_blocks, import_entries


def export_records(
    client,
    zone_id: str,
    zone_name: str,
    private_zone: bool,
    output_dir: Path,
    skip_patterns: Optional[List[Pattern[str]]] = None,
    include_patterns: Optional[List[Pattern[str]]] = None,
) -> Tuple[str, str, Path, List[Tuple[str, str, str]]]:
    output_dir.mkdir(parents=True, exist_ok=True)

    zone_key, local_var, filename_domain, record_blocks, import_entries = collect_zone_records(
        client,
        zone_id,
        zone_name,
        private_zone,
        skip_patterns,
        include_patterns,
    )

    output_path = output_dir / f"route53-records-{filename_domain}.tf"
    if output_path.exists():
        output_path.unlink()

    output_path.write_text(render_zone_file(local_var, zone_key, record_blocks), encoding="utf-8")
    return zone_key, local_var, output_path, import_entries


def write_single_zone_records(
    record_blocks: "OrderedDict[str, OrderedDict[str, Any]]",
    records_path: Path,
    local_var: str = "zone_records",
) -> None:
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text(
        render_single_zone_records(local_var, record_blocks),
        encoding="utf-8",
    )


def write_imports_file(
    import_entries: List[Tuple[str, str, str]],
    imports_path: Path,
    single_zone: bool = False,
    zone_resource_id: Optional[str] = None,
    zone_import_entries: Optional[List[Tuple[str, str]]] = None,
) -> None:
    import_entries = sorted(import_entries, key=lambda item: (item[0], item[1]))
    zone_import_entries = sorted(zone_import_entries or [], key=lambda item: item[0])

    lines: List[str] = []
    if zone_resource_id:
        lines.extend(
            [
                "import {",
                "  to = module.zone.aws_route53_zone.this[0]",
                f"  id = {json.dumps(zone_resource_id)}",
                "}",
                "",
            ]
        )
    for zone_key, zone_id in zone_import_entries:
        lines.extend(
            [
                "import {",
                f"  to = module.zones[{json.dumps(zone_key)}].aws_route53_zone.this[0]",
                f"  id = {json.dumps(zone_id)}",
                "}",
                "",
            ]
        )
    for zone_key, record_key, import_id in import_entries:
        if single_zone:
            to_line = f"  to = module.zone.aws_route53_record.this[{json.dumps(record_key)}]"
        else:
            to_line = (
                f"  to = module.zones[{json.dumps(zone_key)}].aws_route53_record.this"
                f"[{json.dumps(record_key)}]"
            )
        lines.extend([
            "import {",
            to_line,
            f"  id = {json.dumps(import_id)}",
            "}",
            "",
        ])
    content = "\n".join(lines).rstrip() + ("\n" if lines else "")
    imports_path.write_text(content, encoding="utf-8")


def update_single_zone_locals(zone_name: str, locals_path: Path) -> None:
    locals_path.parent.mkdir(parents=True, exist_ok=True)
    if locals_path.exists():
        original = locals_path.read_text(encoding="utf-8")
    else:
        original = ""

    pattern = re.compile(
        rf"{re.escape(SINGLE_ZONE_BEGIN_MARKER)}.*?{re.escape(SINGLE_ZONE_END_MARKER)}\n?",
        re.DOTALL,
    )
    cleaned = re.sub(pattern, "", original).rstrip()

    zone_block = [
        SINGLE_ZONE_BEGIN_MARKER,
        "locals {",
        "  zone = {",
        f"    name    = {json.dumps(zone_name)}",
        f"    comment = {json.dumps(f'Primary {zone_name} zone')}",
        "    tags    = {}",
        "  }",
        "}",
        SINGLE_ZONE_END_MARKER,
        "",
    ]

    generated = "\n".join(zone_block)
    if cleaned:
        new_content = cleaned + "\n\n" + generated
    else:
        new_content = generated

    locals_path.write_text(new_content, encoding="utf-8")


def update_locals_file(local_vars: List[str], locals_path: Path) -> None:
    if locals_path.exists():
        original = locals_path.read_text(encoding="utf-8")
    else:
        original = ""

    pattern = re.compile(rf"{re.escape(BEGIN_MARKER)}.*?{re.escape(END_MARKER)}\n?", re.DOTALL)
    cleaned = re.sub(pattern, "", original).rstrip()

    block_lines: List[str] = [BEGIN_MARKER]
    if local_vars:
        block_lines.extend([
            "locals {",
            "  zone_records = merge(",
            "    {},",
        ])
        for index, local_var in enumerate(local_vars):
            suffix = "," if index < len(local_vars) - 1 else ""
            block_lines.append(f"    local.{local_var}{suffix}")
        block_lines.extend([
            "  )",
            "}",
        ])
    else:
        block_lines.extend([
            "locals {",
            "  zone_records = {}",
            "}",
        ])
    block_lines.append(END_MARKER)
    block_lines.append("")

    generated_block = "\n".join(block_lines)
    if cleaned:
        new_content = cleaned + "\n\n" + generated_block
    else:
        new_content = generated_block

    locals_path.write_text(new_content, encoding="utf-8")


def parse_zone_ids_arg(raw: Optional[Union[str, Iterable[str]]]) -> List[str]:
    if not raw:
        return []

    tokens: List[str] = []
    if isinstance(raw, str):
        tokens = [raw]
    else:
        tokens = list(raw)

    zone_ids: List[str] = []
    for token in tokens:
        if not token:
            continue
        parts = str(token).split(",")
        for part in parts:
            zone_id = part.strip().strip('"')
            if zone_id:
                zone_ids.append(zone_id)
    return zone_ids


def parse_hostname_patterns(raw: Optional[Union[str, Iterable[str]]]) -> List[Pattern[str]]:
    if not raw:
        return []

    values: List[str]
    if isinstance(raw, str):
        values = [raw]
    else:
        values = [str(item) for item in raw if item]

    patterns: List[Pattern[str]] = []
    for raw_entry in values:
        for token in raw_entry.split(","):
            expression = token.strip()
            if not expression:
                continue
            patterns.append(re.compile(expression, re.IGNORECASE))
    return patterns


def get_zone_ids(args: argparse.Namespace) -> List[str]:
    zone_ids = parse_zone_ids_arg(getattr(args, "zone_ids", None))
    if zone_ids:
        return zone_ids
    raise ValueError("No zone IDs supplied. Add zone_ids entries to export-config.json.")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    if argv is None:
        argv_list = sys.argv[1:]
    else:
        argv_list = list(argv)

    config_path = Path(DEFAULT_CONFIG_PATH)
    config_data = load_config_file(config_path, required=True)
    apply_record_type_overrides(config_data)
    normalized_config = normalize_config_data(config_data)

    parser = argparse.ArgumentParser(
        description="Generate Terraform locals for Route53 record sets for each hosted zone listed.",
    )
    parser.add_argument(
        "--only-hostnames",
        help=(
            "Comma-separated list of fully-qualified hostnames; only matching records will be exported."
        ),
    )
    parser.add_argument(
        "--export-target",
        choices=EXPORT_TARGET_CHOICES,
        help="Choose whether to export records, zones, or both (default: both).",
    )
    apply_argument_defaults_from_config(parser, normalized_config)

    args = parser.parse_args(argv_list)

    for key, value in normalized_config.items():
        if key in ("only_hostnames", "export_target"):
            continue
        setattr(args, key, value)

    args._config_data = config_data
    args.config_file = str(config_path)
    return args


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = parse_args(argv)
    except (OSError, ValueError) as exc:
        print(f"Failed to parse arguments: {exc}", file=sys.stderr)
        return 1

    try:
        zone_ids = get_zone_ids(args)
    except (OSError, ValueError) as exc:
        print(f"Failed to load zone IDs: {exc}", file=sys.stderr)
        return 1

    skip_patterns = parse_hostname_patterns(getattr(args, "skip_hostnames", None))
    include_patterns = parse_hostname_patterns(getattr(args, "only_hostnames", None))

    skip_zone_tags = bool(args.skip_zone_tags)

    export_target = args.export_target
    export_records_enabled = export_target in ("records", "both")
    export_zones_enabled = export_target in ("zones", "both")

    single_zone_mode = bool(args.single_zone)
    if single_zone_mode and len(zone_ids) != 1:
        print("--single-zone requires exactly one hosted zone ID", file=sys.stderr)
        return 1
    if single_zone_mode and export_zones_enabled and not export_records_enabled:
        print("Zones-only export is not supported with --single-zone", file=sys.stderr)
        return 1
    zone_export_enabled = export_zones_enabled and not single_zone_mode
    include_zone_tags = zone_export_enabled and not skip_zone_tags

    try:
        session = build_session(args.profile)
        client = session.client("route53")
    except (ClientError, BotoCoreError) as exc:
        print(f"Failed to create Route53 client: {exc}", file=sys.stderr)
        return 1

    aggregate_imports: List[Tuple[str, str, str]] = []
    aggregate_zone_imports: List[Tuple[str, str]] = []
    zone_resource_import_id: Optional[str] = None
    successes = 0

    if single_zone_mode:
        records_path = Path(args.single_zone_records_file)
        zone_id = zone_ids[0]
        if export_records_enabled:
            try:
                zone_details = get_zone_details(client, zone_id, include_tags=include_zone_tags)
                zone_name = zone_details["name"]
                private_zone = bool(zone_details["private_zone"])
                (
                    _zone_key,
                    _local_var,
                    _filename,
                    record_blocks,
                    import_entries,
                ) = collect_zone_records(
                    client,
                    zone_id,
                    zone_name,
                    private_zone,
                    skip_patterns=skip_patterns,
                    include_patterns=include_patterns,
                )
                write_single_zone_records(record_blocks, records_path)
                update_single_zone_locals(zone_name, Path(args.locals_file))
            except (ClientError, BotoCoreError) as exc:
                print(f"Failed to export records for {zone_id}: {exc}", file=sys.stderr)
            except OSError as exc:
                print(f"Failed to write records for {zone_id}: {exc}", file=sys.stderr)
            else:
                aggregate_imports.extend(import_entries)
                successes = 1
                zone_resource_import_id = zone_id
                print(f"Exported {zone_id} -> {records_path}")
        if export_zones_enabled and not zone_export_enabled:
            print(
                "Zone export is not supported in --single-zone mode; skipping zone output.",
                file=sys.stderr,
            )
    else:
        output_dir = Path(args.output_dir)
        if export_records_enabled:
            output_dir.mkdir(parents=True, exist_ok=True)

        aggregate_locals: List[str] = []
        aggregate_zone_configs: Dict[str, "OrderedDict[str, Any]"] = {}
        for zone_id in zone_ids:
            try:
                zone_details = get_zone_details(client, zone_id, include_tags=include_zone_tags)
                zone_name = zone_details["name"]
                private_zone = bool(zone_details["private_zone"])
                zone_key = zone_name if not private_zone else f"{zone_name}_private"

                per_zone_messages: List[str] = []
                if export_records_enabled:
                    (
                        zone_key_from_records,
                        local_var,
                        output_path,
                        import_entries,
                    ) = export_records(
                        client,
                        zone_id,
                        zone_name,
                        private_zone,
                        output_dir,
                        skip_patterns=skip_patterns,
                        include_patterns=include_patterns,
                    )
                    aggregate_locals.append(local_var)
                    aggregate_imports.extend(import_entries)
                    per_zone_messages.append(f"records -> {output_path}")
                    # zone_key_from_records matches zone_key but keep source of truth from records
                    zone_key = zone_key_from_records

                if zone_export_enabled:
                    aggregate_zone_imports.append((zone_key, zone_id))
                    aggregate_zone_configs[zone_key] = build_zone_configuration(
                        zone_details,
                        include_tags=not skip_zone_tags,
                    )
                    per_zone_messages.append("zone config prepared")

            except (ClientError, BotoCoreError) as exc:
                print(f"Failed to export data for {zone_id}: {exc}", file=sys.stderr)
                continue
            except OSError as exc:
                print(f"Failed to write data for {zone_id}: {exc}", file=sys.stderr)
                continue
            else:
                if per_zone_messages:
                    successes += 1
                    print(f"Exported {zone_id}: {', '.join(per_zone_messages)}")

        if export_records_enabled:
            aggregate_locals = sorted(set(aggregate_locals))
            try:
                update_locals_file(aggregate_locals, Path(args.locals_file))
                print(f"Updated {args.locals_file} with generated zone record locals")
            except OSError as exc:
                print(f"Failed to update locals file {args.locals_file}: {exc}", file=sys.stderr)
                return 1

        if zone_export_enabled:
            aggregate_zone_imports = sorted(set(aggregate_zone_imports))
            if aggregate_zone_configs:
                ordered_zones: "OrderedDict[str, OrderedDict[str, Any]]" = OrderedDict(
                    (key, aggregate_zone_configs[key]) for key in sorted(aggregate_zone_configs)
                )
                try:
                    write_zones_file(ordered_zones, Path(args.zones_file))
                    print(f"Wrote zone configuration to {args.zones_file}")
                except OSError as exc:
                    print(f"Failed to write zones file {args.zones_file}: {exc}", file=sys.stderr)
                    return 1
            else:
                print(
                    "No zone configurations were generated; skipping zones file update.",
                    file=sys.stderr,
                )

    aggregate_imports = sorted(set(aggregate_imports))

    try:
        write_imports_file(
            aggregate_imports,
            Path(args.imports_file),
            single_zone=single_zone_mode,
            zone_resource_id=zone_resource_import_id,
            zone_import_entries=aggregate_zone_imports,
        )
        print(f"Wrote import statements to {args.imports_file}")
    except OSError as exc:
        print(f"Failed to write imports file {args.imports_file}: {exc}", file=sys.stderr)
        return 1

    if successes != len(zone_ids):
        print(
            f"Completed with partial success: {successes}/{len(zone_ids)} zones exported.",
            file=sys.stderr,
        )
        return 2

    noun = "zone" if successes == 1 else "zones"
    summary_targets: List[str] = []
    destination_parts: List[str] = []
    if export_records_enabled:
        summary_targets.append("records")
        record_destination = (
            args.single_zone_records_file if single_zone_mode else args.output_dir
        )
        destination_parts.append(f"records -> {record_destination}")
    if zone_export_enabled:
        summary_targets.append("zones")
        destination_parts.append(f"zones -> {args.zones_file}")
    actions = " and ".join(summary_targets) if summary_targets else "data"
    destination = "; ".join(destination_parts) if destination_parts else "requested outputs"
    print(f"Exported {actions} for {successes} {noun} ({destination})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
