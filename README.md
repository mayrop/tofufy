# tofufy

Generate Terraform/OpenTofu-friendly configuration for the Route53 hosted zones you already have in AWS. The CLI connects to Route53, downloads all record sets for the zones you list, and writes Terraform locals plus import blocks so you can recreate those records under infrastructure-as-code.

## ⚡ Get started

### 🔧 Requirements

- Python 3.11+
- AWS credentials with permission to call the Route53 `ListHostedZones`, `GetHostedZone`, `ListResourceRecordSets`, and `ListTagsForResource` APIs
- A `config-route53.json` file in the project root (described below)
- [`uv`](https://docs.astral.sh/uv/) for isolated execution.

### Install Tofufy 

Choose your preferred installation method:

#### Option 1: Persistent Installation (Recommended)

Install once and use everywhere:
```bash
uv tool install tofufy --from git+https://github.com/mayrop/tofufy.git`
```

Then use the tool directly (where you have the `config-route53.json` file):
```bash
tofufy
```

#### Option 2: One-time Usage
```bash
uvx --from git+https://github.com/mayrop/tofufy.git tofufy --help
```

## 📖 Configuration (`config-route53.json`)

The tool will not run without this file.

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
  "single_zone": false,
  "single_zone_records_file": "config-records.tf",
  "skip_hostnames": [
    "mycustom.domain.com"
  ],
  "only_hostnames": [],
  "export_target": "both",
  "skip_zone_tags": true
}
```

### 🔍 Key settings

- `zone_ids` (required) – entries for the hosted zones to export.
- `output_dir` – directory for the generated files.
- `locals_file` – Destination for the merged `local.zone_records` map. Content is wrapped in `# BEGIN/END GENERATED ROUTE53 RECORDS` markers.
- `imports_file` – Destination for `import { ... }` blocks matching every generated record.
- `profile` – AWS profile name fed to `boto3.Session(profile_name=...)`; omit if profile is `default`.
- `skip_hostnames` – List of hostname that should be skipped for import/generation. The generation will be skipped on both record export/import generation for the `skippable_import_types` record types (defaults to `A`, `CNAME`).
- `skip_zone_tags` – set `true` to avoid calling `ListTagsForResource` if your IAM policy prohibits it or you don't want to export the tags for each zone.
- `export_target` – `records`, `zones`, or `both`. `zones` produces only zone metadata locals; `records` writes only the per-zone records and locals.
- `skip_record_types` – Upper-cased record types that should be completely ignored (defaults to `NS` and `SOA`).
- `skippable_import_types` – record types that can be filtered by `skip_hostnames`/`only_hostnames` when building import statements.

#### Multi-zone
- `zones_file` – Destination path describing each hosted zone. Only written `single_zone` is `false`.

#### Single zone
- `single_zone` – When `true`, you must list exactly one `zone_id`; records go into `single_zone_records_file` and a dedicated locals block (`# BEGIN/END GENERATED PRIMARY ZONE`) is inserted into `locals_file`.
- `single_zone_records_file` – Destination path for the monolithic records file created in `--single-zone` mode.

### Options
You can pass CLI-only overrides exactly once per run. The currently supported flags are:

- `--only-hostnames "host1.example.com,^api\\."` – regular expressions to *include*
- `--export-target {records|zones|both}` – skip writing either the per-zone records or the zone metadata/locals

For a full list of options (including defaults sourced from the config file) run `uv run tofufy -- --help`.

## 🎯 Generated files

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
3. Run `tofufy` (add `--only-hostnames` or `--export-target zones` as needed).
4. Run `tofu fmt`.
5. Inspect the generated `route53-records-*.tf`, `config-zones.tf`, and `imports.tf` files and run `tofu plan` to check results.
6. Add the files to version control along with the corresponding Terraform modules to keep AWS and IaC synchronized.

