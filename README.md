# tofufy

Generate Terraform/OpenTofu-friendly configuration for the Route53 hosted zones you already have in AWS. The CLI connects to Route53, downloads all record sets for the zones you list, and writes Terraform locals plus import blocks so you can recreate those records under infrastructure-as-code.

## Requirements

- Python 3.11+
- AWS credentials with permission to call the Route53 `ListHostedZones`, `GetHostedZone`, `ListResourceRecordSets`, and `ListTagsForResource` APIs
- A `config-route53.json` file in the project root (described below)
- Optional: [`uv`](https://docs.astral.sh/uv/) for isolated execution; otherwise any virtualenv + `pip install -e .`

## Installation & Execution

```bash
uv sync               # or: python -m venv .venv && source .venv/bin/activate && pip install -e .
uv run tofufy          # runs tofufy.cli:main using the config in config-route53.json
```

You can pass CLI-only overrides exactly once per run. The currently supported flags are:

- `--only-hostnames "host1.example.com,^api\\."` – regular expressions to *include*
- `--export-target {records|zones|both}` – skip writing either the per-zone records or the zone metadata/locals

For a full list of options (including defaults sourced from the config file) run `uv run tofufy -- --help`.

## Configuration (`config-route53.json`)

The tool refuses to run without this file. It is a JSON object where each key mirrors an argument accepted by `tofufy`. You can keep everything at the top level or nest arguments under an `arguments` object; nested values win when both are present. Example:

```json
{
  "skip_record_types": ["NS", "SOA"],
  "skippable_import_types": ["A", "CNAME"],
  "zone_ids": [
    "Z0111111JC1KE1HTYMZ1"
  ],
  "output_dir": ".",
  "locals_file": "locals.tf",
  "imports_file": "imports.tf",
  "zones_file": "config-zones.tf",
  "single_zone": true,
  "single_zone_records_file": "config-records.tf",
  "skip_hostnames": [
    "mycustom.domain.com"
  ],
  "only_hostnames": [],
  "export_target": "both",
  "skip_zone_tags": true
}
```

### Key settings

- `zone_ids` (required) – list/CSV/string entries for the hosted zones to export.
- `output_dir` – directory for per-zone `route53-records-<zone>.tf` files, one per public/private zone pairing.
- `locals_file` – Terraform locals file updated with a merged `local.zone_records` map. Content is wrapped in `# BEGIN/END GENERATED ROUTE53 RECORDS` markers.
- `imports_file` – destination for `import { ... }` blocks matching every generated record; rerun imports after each export to keep Terraform in sync.
- `zones_file` – locals file describing each hosted zone (name, comment, tags, VPC attachments). Only written when exporting more than one zone.
- `single_zone` – when `true`, you must list exactly one `zone_id`; records go into `single_zone_records_file` and a dedicated locals block (`# BEGIN/END GENERATED PRIMARY ZONE`) is inserted into `locals_file`.
- `single_zone_records_file` – path for the monolithic records file created in `--single-zone` mode.
- `profile` – AWS profile name fed to `boto3.Session(profile_name=...)`; omit to rely on the default credential resolution order.
- `skip_hostnames` – list/CSV of regular expressions. Matches skip record export/import generation for `A`/`CNAME` types (others always export).
- `only_hostnames` – regular expressions; when non-empty, only records whose FQDN matches at least one expression are considered.
- `export_target` – `records`, `zones`, or `both`. `zones` produces only zone metadata locals; `records` writes only the per-zone records and locals.
- `skip_zone_tags` – set `true` to avoid calling `ListTagsForResource` if your IAM policy prohibits it or you don't want to export the tags for each zone.
- `skip_record_types` – upper-cased record types that should be completely ignored (defaults to `NS` and `SOA`).
- `skippable_import_types` – record types that can be filtered by `skip_hostnames`/`only_hostnames` when building import statements.

## Generated files

Running `tofufy` creates or updates the following:

- `route53-records-<zone>.tf` in `output_dir`: locals for every record in the zone and a matching `local.zone_records_<zone>` map.
- `locals_file`: contains a `locals { zone_records = merge(...) }` block assembling all zone locals and (in single-zone mode) a `locals.zone` block for the primary zone.
- `zones_file`: locals describing each exported hosted zone, including its tags and private-zone/VPC metadata.
- `single_zone_records_file` (single-zone mode only): a single file containing the rendered locals for that primary zone.
- `imports_file`: Terraform `import` blocks for the zone resources and every `aws_route53_record`, ready to feed into `terraform`/`tofu import`.

All files are replaced atomically on each run, so commit the outputs if you want deterministic IaC diffs. If any zone fails, the command exits with code `2` to signal "partial success" and still writes whatever it could.

## Typical workflow

1. Create/adjust `config-route53.json` with the zones you want.
2. Ensure you can access AWS (either by exporting `AWS_PROFILE`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or setting the `profile` field).
3. Run `uv run tofufy` (add `--only-hostnames` or `--export-target zones` as needed).
4. Inspect the generated `route53-records-*.tf`, `config-zones.tf`, and `imports.tf` files before running `terraform import`/`tofu import`.
5. Add the files to version control along with the corresponding Terraform modules to keep AWS and IaC synchronized.

