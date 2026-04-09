# Route53

## Default config file

`.tofufy-config-aws-route53.json`

Route53 also keeps a legacy fallback:

- `config-route53.json`

If no explicit `--config` is passed, `tofufy` looks for the provider default in the current working directory.

## Example config

```json
{
  "zone_ids": [
    "Z0111111JC1KE1HTYMZ1"
  ],
  "output_dir": ".",
  "locals_file": "locals.tf",
  "imports_file": "imports.tf",
  "zones_file": "config-zones.tf",
  "single_zone": false,
  "single_zone_records_file": "config-records.tf",
  "profile": null,
  "skip_record_types": ["NS", "SOA"],
  "skippable_import_types": ["A", "CNAME"],
  "skip_hostnames": [],
  "only_hostnames": [],
  "export_target": "both",
  "skip_zone_tags": false
}
```

## Common commands

Default config discovery:

```bash
cd tofufy
uv run tofufy --provider aws-route53
```

Explicit config path:

```bash
cd tofufy
uv run tofufy --provider aws-route53 --config path/to/.tofufy-config-aws-route53.json
```

## Key settings

- `zone_ids`: hosted zones to export
- `output_dir`: destination directory for generated files
- `locals_file`: destination for merged record locals
- `imports_file`: destination for `import { ... }` blocks
- `zones_file`: destination for hosted-zone metadata
- `single_zone`: enables single-zone mode
- `single_zone_records_file`: monolithic records file used in single-zone mode
- `profile`: AWS profile passed to `boto3.Session(profile_name=...)`
- `skip_hostnames`: hostnames to skip for generation and selected imports
- `only_hostnames`: include-only regex list
- `export_target`: `records`, `zones`, or `both`
- `skip_zone_tags`: avoids `ListTagsForResource`

## Outputs

Depending on config, Route53 generates or updates:

- `route53-records-<zone>.tf`
- `locals.tf`
- `config-zones.tf`
- `config-records.tf` in single-zone mode
- `imports.tf`

Route53 preserves the existing marker-based structure in `locals_file` for generated record and primary-zone blocks.

## Typical workflow

1. Create `.tofufy-config-aws-route53.json`.
2. Ensure AWS credentials are available.
3. Run `tofufy --provider aws-route53`.
4. Run `tofu fmt`.
5. Inspect the diff.
6. Run `tofu plan`.
