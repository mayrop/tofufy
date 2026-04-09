from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List, Pattern

IDENTIFIER_PATTERN: Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def to_hcl_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)


def render_attribute_block(
    attribute_name: str,
    attribute_value: Any,
    indent: int,
    lines: List[str],
) -> None:
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


def render_assignment_map(
    local_name: str,
    records: dict[str, dict[str, Any]],
    map_indent: int,
    item_indent: int,
) -> str:
    lines: List[str] = ["locals {", f"  {local_name} = {{"]
    if records:
        for record_key, attributes in records.items():
            lines.append(f"{'  ' * map_indent}{record_key} = {{")
            attribute_lines: List[str] = []
            for attr_name, attr_value in attributes.items():
                render_attribute_block(attr_name, attr_value, item_indent, attribute_lines)
            lines.extend(attribute_lines)
            lines.append(f"{'  ' * map_indent}}}")
    lines.append("  }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def replace_marked_block(
    original: str,
    begin_marker: str,
    end_marker: str,
    generated: str,
) -> str:
    pattern = re.compile(
        rf"{re.escape(begin_marker)}.*?{re.escape(end_marker)}\n?",
        re.DOTALL,
    )
    cleaned = re.sub(pattern, "", original).rstrip()

    if cleaned:
        return cleaned + "\n\n" + generated
    return generated


def replace_inline_marked_block(
    original: str,
    begin_marker: str,
    end_marker: str,
    generated_body: str,
) -> str:
    pattern = re.compile(
        rf"(?P<begin>[ \t]*{re.escape(begin_marker)}\n)"
        rf"(?P<body>.*?)"
        rf"(?P<end>[ \t]*{re.escape(end_marker)})",
        re.DOTALL,
    )

    match = pattern.search(original)
    if not match:
        raise ValueError(f"Marker block not found for {begin_marker} / {end_marker}")

    body = generated_body
    if body and not body.endswith("\n"):
        body += "\n"
    return original[: match.start()] + match.group("begin") + body + match.group("end") + original[match.end() :]


def replace_marked_block_in_file(
    path: Path,
    begin_marker: str,
    end_marker: str,
    generated: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        replace_marked_block(original, begin_marker, end_marker, generated),
        encoding="utf-8",
    )


def replace_inline_marked_block_in_file(
    path: Path,
    begin_marker: str,
    end_marker: str,
    generated_body: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        replace_inline_marked_block(original, begin_marker, end_marker, generated_body),
        encoding="utf-8",
    )
